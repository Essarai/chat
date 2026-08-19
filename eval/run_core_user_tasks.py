#!/usr/bin/env python3
"""Run the ten product contracts through production or deterministic SQL mode."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.controller import init_state  # noqa: E402
from app.agents.coverage import assess_operations  # noqa: E402
from app.agents.eval_contracts import evaluate_core_contract  # noqa: E402
from app.agents.generate import ensure_answer_operation_coverage, try_operation_plan_answer  # noqa: E402
from app.capabilities.sql_capability import execute_plan  # noqa: E402
from app.config import get_corpus_settings  # noqa: E402
from app.services.minimax_chat import MiniMaxChat  # noqa: E402
from app.services.production_orchestrator import ChatOrchestrator  # noqa: E402
from app.services.sqlite_repo import SQLiteRepo  # noqa: E402


def _contract_only(spec: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    state = init_state(spec["example_question"], journal_id=spec["journal_id"])
    state["intents"] = ["sql"]
    state["sql_evidence"] = execute_plan(
        state["query_plan"],
        spec["example_question"],
        state["entities"],
        SQLiteRepo(get_corpus_settings(spec["journal_id"])),
    )
    state.update(assess_operations(state))  # type: ignore[arg-type]
    answer = try_operation_plan_answer(state) or ensure_answer_operation_coverage("", state)  # type: ignore[arg-type]
    return state, answer


def _production(spec: Dict[str, Any], bots: Dict[str, ChatOrchestrator]) -> tuple[Dict[str, Any], str]:
    journal_id = spec["journal_id"]
    bot = bots.setdefault(journal_id, ChatOrchestrator(get_corpus_settings(journal_id)))
    result = bot.ask(spec["example_question"])
    evidence = result.evidence
    state = {
        "query_plan": evidence.get("query_plan") or {},
        "turn_intent": evidence.get("turn_intent") or {},
        "entities": (evidence.get("result_set") or {}).get("constraints") or {},
        "sql_evidence": evidence.get("sql") or {},
        "kg_evidence": evidence.get("kg") or {},
        "rag_evidence": evidence.get("rag") or {},
        "operation_results": evidence.get("operation_results") or [],
        "coverage_report": evidence.get("coverage_report") or {},
        "result_set": evidence.get("result_set") or {},
        "intents": evidence.get("intents") or result.intents,
    }
    return state, result.answer


def _judge(question: str, answer: str, chat: MiniMaxChat) -> Dict[str, Any]:
    raw = chat.chat(
        [
            {"role": "system", "content": "你是期刊问答质量评审，只输出合法 JSON。"},
            {
                "role": "user",
                "content": (
                    "按相关度、完整性、证据一致性、清晰度各 1–5 分评价，只输出 JSON："
                    '{"relevance":1,"completeness":1,"grounding":1,"clarity":1,"note":""}\n'
                    f"问题：{question}\n回答：{answer[:16000]}"
                ),
            },
        ],
        temperature=0.0,
        max_tokens=300,
    )
    return json.loads(raw[raw.find("{") : raw.rfind("}") + 1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-only", action="store_true", help="skip model and test planner/SQL contracts")
    parser.add_argument("--judge", action="store_true", help="add non-blocking model quality scores")
    parser.add_argument("--output", type=Path, default=EVAL_DIR / "core_user_tasks_results.json")
    args = parser.parse_args()

    specs = json.loads((EVAL_DIR / "core_user_tasks_specs.json").read_text(encoding="utf-8"))
    bots: Dict[str, ChatOrchestrator] = {}
    rows = []
    failed = 0
    for spec in specs:
        started = time.monotonic()
        error = None
        try:
            state, answer = _contract_only(spec) if args.contract_only else _production(spec, bots)
            evaluation = evaluate_core_contract(spec, state, answer)
        except Exception as exc:  # noqa: BLE001
            state, answer = {}, ""
            error = f"{type(exc).__name__}: {exc}"
            evaluation = {"passed": False, "failures": [error], "named_gates": {}, "quality": {}}
        elapsed_ms = int((time.monotonic() - started) * 1000)
        judge = None
        if args.judge and answer:
            try:
                judge = _judge(
                    spec["example_question"],
                    answer,
                    MiniMaxChat(get_corpus_settings(spec["journal_id"])),
                )
            except Exception as exc:  # optional score never changes hard-gate status
                judge = {"error": str(exc)}
        if not evaluation["passed"]:
            failed += 1
        rows.append(
            {
                "id": spec["id"],
                "passed": evaluation["passed"],
                "failures": evaluation["failures"],
                "elapsed_ms": elapsed_ms,
                "intent": state.get("turn_intent") or {},
                "query_plan": state.get("query_plan") or {},
                "operation_results": state.get("operation_results") or [],
                "result_set": state.get("result_set") or {},
                "coverage_report": state.get("coverage_report") or {},
                "quality": evaluation.get("quality") or {},
                "named_gates": evaluation.get("named_gates") or {},
                "judge": judge,
                "answer": answer,
            }
        )
        print(
            f"{spec['id']} {'PASS' if evaluation['passed'] else 'FAIL'} "
            f"{elapsed_ms}ms {evaluation['failures']}",
            flush=True,
        )

    report = {
        "mode": "contract-only" if args.contract_only else "production",
        "passed": len(rows) - failed,
        "failed": failed,
        "total": len(rows),
        "cases": rows,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.output}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
