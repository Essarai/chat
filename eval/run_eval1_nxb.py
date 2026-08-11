# -*- coding: utf-8 -*-
"""Contract and optional semantic evaluation for eval/1.md."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
EVAL = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_corpus_settings  # noqa: E402
from app.agents.controller import init_state  # noqa: E402
from app.agents.coverage import assess_answer_coverage, assess_operations, assess_quality  # noqa: E402
from app.agents.generate import ensure_answer_operation_coverage, try_operation_plan_answer  # noqa: E402
from app.capabilities.sql_capability import execute_plan  # noqa: E402
from app.services.minimax_chat import MiniMaxChat  # noqa: E402
from app.services.orchestrator import ChatOrchestrator  # noqa: E402
from app.services.sqlite_repo import SQLiteRepo  # noqa: E402


def load_questions(path: Path) -> List[str]:
    questions: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*\d+\.\s*(.+)$", line)
        if match:
            questions.append(match.group(1).strip())
    return questions


def resolve(question: str) -> str:
    for source, target in (
        ("某研究方向", "水稻"),
        ("某作者", "徐建明"),
        ("某研究团队", "徐建明研究团队"),
    ):
        question = question.replace(source, target)
    return question


def check_contract(spec: Dict[str, Any], evidence: Dict[str, Any], answer: str) -> List[str]:
    failures: List[str] = []
    intent = evidence.get("turn_intent") or evidence.get("intent_schema") or {}
    plan = evidence.get("query_plan") or {}
    operations = [op.get("type") for op in plan.get("operations") or [] if isinstance(op, dict)]
    required = spec.get("required_operations") or []
    for operation in required:
        if operation not in operations:
            failures.append(f"missing operation: {operation}")
    for key, expected in (spec.get("expected_intent") or {}).items():
        if intent.get(key) != expected:
            failures.append(f"intent {key}={intent.get(key)} expected={expected}")
    for operation in spec.get("forbidden_operations") or []:
        if operation in operations:
            failures.append(f"forbidden operation: {operation}")
    topics = list(plan.get("keywords") or [])
    for topic in spec.get("forbidden_topics") or []:
        if topic in topics:
            failures.append(f"forbidden literal topic: {topic}")
    for topic in spec.get("topics") or []:
        if topic not in topics:
            failures.append(f"missing topic: {topic}")
    constraints = spec.get("constraints") or {}
    actual_constraints = plan.get("constraints") or {}
    for key in ("year_start", "year_end"):
        expected = constraints.get(key)
        actual = plan.get(key)
        if actual != expected:
            failures.append(f"constraint {key}={actual} expected={expected}")
    for topic in constraints.get("topics") or []:
        if topic not in topics:
            failures.append(f"missing constrained topic: {topic}")
    for key in ("author_name", "author_name_b", "institution"):
        if key in constraints and plan.get(key) != constraints.get(key):
            failures.append(f"constraint {key}={plan.get(key)} expected={constraints.get(key)}")
    results = evidence.get("operation_results") or []
    result_ops = {row.get("operation"): row for row in results}
    for operation in required:
        if operation not in result_ops:
            failures.append(f"missing OperationResult: {operation}")
    allowed_statuses = set((spec.get("expected_coverage") or {}).get("allowed_statuses") or ["complete"])
    for operation in required:
        row = result_ops.get(operation) or {}
        if row and row.get("status") not in allowed_statuses:
            failures.append(f"operation {operation} status={row.get('status')} not in {sorted(allowed_statuses)}")
    coverage = evidence.get("coverage_report") or {}
    if int(coverage.get("required_count") or 0) != len(plan.get("operations") or []):
        failures.append("CoverageReport required_count mismatch")
    result_set = evidence.get("result_set") or {}
    if "items" not in result_set:
        failures.append("ResultSet missing items")
    expected_result = spec.get("result_set") or {}
    if expected_result.get("type") and result_set.get("type") != expected_result["type"]:
        failures.append(f"ResultSet type={result_set.get('type')} expected={expected_result['type']}")
    items = result_set.get("items") or []
    for field in expected_result.get("required_fields") or []:
        if not items or any(item.get(field) is None for item in items if isinstance(item, dict)):
            failures.append(f"ResultSet missing required field: {field}")
    for field in expected_result.get("required_data_fields") or []:
        if not any((row.get("data") or {}).get(field) for row in results):
            failures.append(f"OperationResult data missing field: {field}")
    answer_coverage = evidence.get("answer_coverage") or {}
    if answer_coverage and float(answer_coverage.get("coverage") or 0) < 1.0:
        failures.append(f"answer coverage={answer_coverage.get('coverage')}")
    quality = evidence.get("quality_report") or {}
    min_rgc = float((spec.get("expected_coverage") or {}).get("min_rgc") or 0.0)
    if float(quality.get("required_goal_coverage") or 0.0) < min_rgc:
        failures.append(f"RGC={quality.get('required_goal_coverage')} expected>={min_rgc}")
    if quality and not quality.get("hard_gate_passed"):
        failures.append(f"evidence hard gate failed: {quality.get('hard_gate_violations')}")
    if quality and float(quality.get("evidence_accuracy") or 0.0) < 0.98:
        failures.append(f"EA={quality.get('evidence_accuracy')} expected>=0.98")
    for target in spec.get("answer_targets") or []:
        any_tokens = target.get("any") or []
        all_tokens = target.get("all") or []
        if any_tokens and not any(token in answer for token in any_tokens):
            failures.append(f"answer target missing: {target.get('name')}")
        if all_tokens and not all(token in answer for token in all_tokens):
            failures.append(f"answer target incomplete: {target.get('name')}")
    if not answer.strip():
        failures.append("empty answer")
    return failures


def contract_only(question: str, journal_id: str) -> tuple[Dict[str, Any], str]:
    """Run the production planner/SQL/render contracts without a model call."""
    state = init_state(question, journal_id=journal_id)
    if not (state.get("query_plan") or {}).get("locked"):
        raise RuntimeError("question has no deterministic Query Plan")
    state["intents"] = ["sql"]
    state["sql_evidence"] = execute_plan(
        state["query_plan"], question, state["entities"],
        SQLiteRepo(get_corpus_settings(journal_id)),
    )
    state.update(assess_operations(state))  # type: ignore[arg-type]
    answer = try_operation_plan_answer(state) or ensure_answer_operation_coverage("", state)  # type: ignore[arg-type]
    state["answer_coverage"] = assess_answer_coverage(answer, state["operation_results"])
    state["quality_report"] = assess_quality(answer, state)  # type: ignore[arg-type]
    return {
        "turn_intent": state.get("turn_intent") or {},
        "intent_schema": state.get("intent") or state.get("turn_intent") or {},
        "query_plan": state.get("query_plan") or {},
        "operation_results": state.get("operation_results") or [],
        "coverage_report": state.get("coverage_report") or {},
        "result_set": state.get("result_set") or {},
        "answer_coverage": state.get("answer_coverage") or {},
        "quality_report": state.get("quality_report") or {},
    }, answer


def judge_answer(question: str, answer: str, chat: MiniMaxChat) -> Dict[str, Any]:
    prompt = f"""请评价下面期刊问答，只输出 JSON：
{{"relevance":1到5,"completeness":1到5,"grounding":1到5,"clarity":1到5,"note":"一句话"}}
问题：{question}
回答：{answer[:12000]}
"""
    raw = chat.chat(
        [{"role": "system", "content": "你是问答质量评审，只输出合法 JSON。"}, {"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=300,
    )
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start:end + 1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-only", action="store_true", help="run planner/SQL/render hard gates without models")
    parser.add_argument("--judge", action="store_true", help="run non-blocking LLM semantic scoring")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    questions = load_questions(EVAL / "1.md")
    specs = json.loads((EVAL / "eval1_specs.json").read_text(encoding="utf-8"))
    if len(questions) != len(specs):
        raise SystemExit(f"question/spec count mismatch: {len(questions)} != {len(specs)}")
    if args.limit:
        questions, specs = questions[: args.limit], specs[: args.limit]

    journal_id = "ZDXBNXB"
    bot = ChatOrchestrator(get_corpus_settings(journal_id))
    judge = MiniMaxChat(get_corpus_settings(journal_id)) if args.judge else None
    output = EVAL / "1_results_nxb.md"
    blocks = [
        "# eval/1.md 农生刊契约测试结果", "",
        f"- 测试时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 语料：`{journal_id}`",
        f"- 入口：`{'确定性 Planner/SQL/Renderer' if args.contract_only else 'ChatOrchestrator.ask（与生产一致）'}`",
        f"- 题目数：{len(questions)}", f"- 模型语义评分：{'开启（非门禁）' if args.judge else '关闭'}", "",
    ]
    passed = failed = 0
    elapsed_all: List[float] = []
    judge_rows: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    for index, (original, spec) in enumerate(zip(questions, specs), 1):
        question = resolve(original)
        started = time.monotonic()
        error = None
        result = None
        try:
            if args.contract_only:
                evidence, answer = contract_only(question, journal_id)
            else:
                result = bot.ask(question)
                evidence = result.evidence
                answer = result.answer
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started
        elapsed_all.append(elapsed)
        evidence = evidence if not error else {}
        answer = answer if not error else ""
        failures = [error] if error else check_contract(spec, evidence, answer)
        if failures:
            failed += 1
        else:
            passed += 1
        score = None
        if judge and answer:
            try:
                score = judge_answer(question, answer, judge)
                judge_rows.append(score)
            except Exception as exc:  # semantic judge never fails the hard gate
                score = {"error": str(exc)}
        plan = evidence.get("query_plan") or {}
        records.append({
            "id": spec.get("id"), "question": question, "passed": not failures,
            "failures": failures, "elapsed_ms": int(elapsed * 1000),
            "intent": evidence.get("turn_intent") or evidence.get("intent_schema") or {},
            "query_plan": plan,
            "operation_results": evidence.get("operation_results") or [],
            "result_set": evidence.get("result_set") or {},
            "coverage_report": evidence.get("coverage_report") or {},
            "quality_report": evidence.get("quality_report") or {},
            "answer": answer, "judge": score,
        })
        blocks += [
            f"## Q{index} {'PASS' if not failures else 'FAIL'}", "",
            f"**问题：** {question}", "",
            f"**耗时：** {elapsed:.1f}s", "",
            f"**Intent：** `{json.dumps(evidence.get('intent_schema') or {}, ensure_ascii=False)}`", "",
            f"**Operations：** `{[op.get('type') for op in plan.get('operations') or [] if isinstance(op, dict)]}`", "",
            f"**OperationResult：** `{[(row.get('operation'), row.get('status')) for row in evidence.get('operation_results') or []]}`", "",
            f"**Coverage：** `{json.dumps(evidence.get('coverage_report') or {}, ensure_ascii=False)}`", "",
            f"**Quality：** `{json.dumps(evidence.get('quality_report') or {}, ensure_ascii=False)}`", "",
            f"**ResultSet：** `{json.dumps(evidence.get('result_set') or {}, ensure_ascii=False)[:3000]}`", "",
        ]
        if failures:
            blocks += ["**硬断言失败：**", ""] + [f"- {failure}" for failure in failures] + [""]
        if score:
            blocks += [f"**模型评分（非门禁）：** `{json.dumps(score, ensure_ascii=False)}`", ""]
        blocks += ["### 回答", "", answer or "_(空回答)_", ""]
        print(f"Q{index}/{len(questions)} {'PASS' if not failures else 'FAIL'} {elapsed:.1f}s ops={[op.get('type') for op in plan.get('operations') or [] if isinstance(op, dict)]}", flush=True)

    avg = sum(elapsed_all) / max(len(elapsed_all), 1)
    blocks += ["---", "", f"**硬门禁汇总：** pass={passed} fail={failed} total={sum(elapsed_all):.1f}s avg={avg:.1f}s", ""]
    if judge_rows:
        for metric in ("relevance", "completeness", "grounding", "clarity"):
            values = [float(row[metric]) for row in judge_rows if row.get(metric) is not None]
            if values:
                blocks.append(f"- {metric}: {sum(values) / len(values):.2f}/5")
    output.write_text("\n".join(blocks), encoding="utf-8")
    (EVAL / "1_results_nxb.json").write_text(
        json.dumps({
            "mode": "contract-only" if args.contract_only else "production",
            "passed": passed, "failed": failed, "total": len(records), "cases": records,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {output}", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
