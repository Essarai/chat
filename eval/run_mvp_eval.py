#!/usr/bin/env python3
"""Run the MVP author/editor benchmark and preserve stage-level evidence."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.operation_contracts import CONTRACTS  # noqa: E402
from app.config import get_corpus_settings  # noqa: E402
from app.services.production_orchestrator import ChatOrchestrator  # noqa: E402


DEFAULT_SPEC = EVAL_DIR / "mvp_eval_specs_v1.json"
DEFAULT_OUTPUT = EVAL_DIR / "mvp_eval_results_v1.json"
VALID_ROLES = {"author", "editor"}
VALID_STATUSES = {"EXECUTE", "CLARIFY", "PARTIAL", "REJECT", "ASSUME"}
VALID_CAPABILITIES = {f"A{index}" for index in range(1, 9)}
VALID_SOURCES = {"sql", "rag", "kg"}


def load_spec(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("benchmark spec must be one JSON object")
    return payload


def _duplicates(values: Iterable[str]) -> List[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def validate_spec(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    cases = payload.get("cases")
    if not payload.get("spec_version"):
        errors.append("missing spec_version")
    if not isinstance(cases, list) or not cases:
        return errors + ["cases must be a non-empty list"]

    ids = [str(case.get("id") or "") for case in cases if isinstance(case, dict)]
    for duplicate in _duplicates(ids):
        errors.append(f"duplicate case id:{duplicate}")

    required_fields = {
        "id",
        "role",
        "journal_id",
        "title",
        "question",
        "expected_status",
        "capabilities",
        "required_operations",
        "expected_sources",
        "answer_checks",
        "human_review_focus",
    }
    role_capabilities: Dict[str, set] = defaultdict(set)
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"case[{index}] is not an object")
            continue
        case_id = str(case.get("id") or f"case[{index}]")
        missing = sorted(required_fields - set(case))
        if missing:
            errors.append(f"{case_id}:missing fields:{','.join(missing)}")

        role = str(case.get("role") or "")
        if role not in VALID_ROLES:
            errors.append(f"{case_id}:invalid role:{role}")
        status = str(case.get("expected_status") or "")
        if status not in VALID_STATUSES:
            errors.append(f"{case_id}:invalid expected_status:{status}")

        capabilities = {str(value) for value in case.get("capabilities") or []}
        unknown_capabilities = capabilities - VALID_CAPABILITIES
        if unknown_capabilities:
            errors.append(
                f"{case_id}:unknown capabilities:{','.join(sorted(unknown_capabilities))}"
            )
        role_capabilities[role].update(capabilities)

        required_operations = {
            str(value) for value in case.get("required_operations") or []
        }
        optional_operations = {
            str(value) for value in case.get("optional_operations") or []
        }
        forbidden_operations = {
            str(value) for value in case.get("forbidden_operations") or []
        }
        unknown_operations = (
            required_operations | optional_operations | forbidden_operations
        ) - set(CONTRACTS)
        if unknown_operations:
            errors.append(
                f"{case_id}:unregistered operations:{','.join(sorted(unknown_operations))}"
            )
        overlap = required_operations & forbidden_operations
        if overlap:
            errors.append(
                f"{case_id}:required and forbidden overlap:{','.join(sorted(overlap))}"
            )

        sources = {str(value) for value in case.get("expected_sources") or []}
        if not sources or sources - VALID_SOURCES:
            errors.append(f"{case_id}:invalid expected_sources:{sorted(sources)}")
        coverage = case.get("minimum_operation_coverage", 1.0)
        try:
            numeric_coverage = float(coverage)
        except (TypeError, ValueError):
            errors.append(f"{case_id}:invalid minimum_operation_coverage:{coverage}")
        else:
            if not 0.0 <= numeric_coverage <= 1.0:
                errors.append(
                    f"{case_id}:minimum_operation_coverage outside 0-1:{coverage}"
                )

    for role in VALID_ROLES:
        missing_capabilities = VALID_CAPABILITIES - role_capabilities.get(role, set())
        if missing_capabilities:
            errors.append(
                f"{role}:capability coverage missing:{','.join(sorted(missing_capabilities))}"
            )
    return errors


def coverage_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    cases = list(payload.get("cases") or [])
    role_counts = Counter(str(case.get("role")) for case in cases)
    status_counts = Counter(str(case.get("expected_status")) for case in cases)
    split_counts = Counter(str(case.get("split")) for case in cases)
    capability_counts = Counter(
        capability
        for case in cases
        for capability in case.get("capabilities") or []
    )
    role_capability_counts: Dict[str, Counter] = {
        role: Counter(
            capability
            for case in cases
            if case.get("role") == role
            for capability in case.get("capabilities") or []
        )
        for role in sorted(VALID_ROLES)
    }
    return {
        "spec_version": payload.get("spec_version"),
        "total_cases": len(cases),
        "roles": dict(sorted(role_counts.items())),
        "statuses": dict(sorted(status_counts.items())),
        "splits": dict(sorted(split_counts.items())),
        "capabilities": dict(sorted(capability_counts.items())),
        "capabilities_by_role": {
            role: dict(sorted(counts.items()))
            for role, counts in role_capability_counts.items()
        },
    }


def _operation_inventory(evidence: Dict[str, Any]) -> Tuple[set, set]:
    plan = evidence.get("query_plan") or {}
    operations = [
        operation
        for operation in plan.get("operations") or []
        if isinstance(operation, dict)
    ]
    types = {
        str(operation.get("type") or "")
        for operation in operations
        if operation.get("type")
    }
    sources = {
        str(operation.get("source") or "")
        for operation in operations
        if operation.get("source")
    }
    if not types:
        for result in evidence.get("operation_results") or []:
            operation = result.get("operation") or result.get("type")
            if operation:
                types.add(str(operation))
    return types, sources


def _infer_status(
    evidence: Dict[str, Any], answer: str, expected_status: str
) -> str:
    operations, _ = _operation_inventory(evidence)
    if "clarification" in operations:
        return "CLARIFY"
    if "unsupported_citations" in operations:
        return "REJECT"

    report = evidence.get("coverage_report") or {}
    complete = int(report.get("complete_count") or 0)
    partial = int(report.get("partial_count") or 0)
    unsupported = int(report.get("unsupported_count") or 0)
    errors = int(report.get("error_count") or 0)
    if expected_status == "ASSUME" and any(
        token in answer for token in ("默认", "以下按", "暂按", "假设")
    ):
        return "ASSUME"
    if partial or (complete and (unsupported or errors)):
        return "PARTIAL"
    if complete and any(
        token in answer for token in ("只能", "仅能", "无法判断", "无法支持")
    ):
        return "PARTIAL"
    if not complete and (unsupported or errors):
        return "REJECT"
    return "EXECUTE"


def _token_failures(answer: str, checks: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    for token in list(checks.get("must_include") or []) + list(
        checks.get("required_caveats") or []
    ):
        if str(token) not in answer:
            failures.append(f"answer missing:{token}")
    for token in checks.get("must_not_include") or []:
        if str(token) in answer:
            failures.append(f"forbidden answer token:{token}")
    return failures


def auto_evaluate(case: Dict[str, Any], evidence: Dict[str, Any], answer: str) -> Dict[str, Any]:
    failures: List[str] = []
    operations, sources = _operation_inventory(evidence)
    required = {str(value) for value in case.get("required_operations") or []}
    forbidden = {str(value) for value in case.get("forbidden_operations") or []}
    missing_operations = sorted(required - operations)
    forbidden_present = sorted(forbidden & operations)
    if missing_operations:
        failures.append(f"missing operations:{','.join(missing_operations)}")
    if forbidden_present:
        failures.append(f"forbidden operations:{','.join(forbidden_present)}")

    expected_sources = {str(value) for value in case.get("expected_sources") or []}
    if not expected_sources.issubset(sources):
        failures.append(
            "missing sources:" + ",".join(sorted(expected_sources - sources))
        )

    coverage = float((evidence.get("coverage_report") or {}).get("coverage") or 0.0)
    minimum_coverage = float(case.get("minimum_operation_coverage", 1.0))
    if coverage < minimum_coverage:
        failures.append(f"operation coverage:{coverage:.3f}<{minimum_coverage:.3f}")

    actual_status = _infer_status(evidence, answer, str(case["expected_status"]))
    if actual_status != case["expected_status"]:
        failures.append(
            f"status mismatch:expected {case['expected_status']}, got {actual_status}"
        )
    failures.extend(_token_failures(answer, case.get("answer_checks") or {}))
    if evidence.get("errors"):
        failures.append("pipeline errors present")

    return {
        "passed": not failures,
        "failures": failures,
        "expected_status": case["expected_status"],
        "actual_status": actual_status,
        "required_operations": sorted(required),
        "actual_operations": sorted(operations),
        "operation_coverage": coverage,
        "sources": sorted(sources),
        "manual_hard_gates": list(case.get("hard_fail_if") or []),
    }


def human_score_template(payload: Dict[str, Any]) -> Dict[str, Any]:
    dimensions = (payload.get("manual_rubric") or {}).get("dimensions") or []
    return {
        "reviewer": None,
        "reviewed_at": None,
        "hard_failure": None,
        "scores": {str(item.get("key")): None for item in dimensions},
        "total": None,
        "pass": None,
        "notes": "",
    }


def _select_cases(cases: Sequence[Dict[str, Any]], ids: Optional[str]) -> List[Dict[str, Any]]:
    if not ids:
        return list(cases)
    selected_ids = {value.strip() for value in ids.split(",") if value.strip()}
    selected = [case for case in cases if case.get("id") in selected_ids]
    missing = selected_ids - {str(case.get("id")) for case in selected}
    if missing:
        raise ValueError(f"unknown case ids:{','.join(sorted(missing))}")
    return selected


def run_case(
    case: Dict[str, Any], bots: Dict[str, ChatOrchestrator], payload: Dict[str, Any]
) -> Dict[str, Any]:
    journal_id = str(case["journal_id"])
    bot = bots.setdefault(journal_id, ChatOrchestrator(get_corpus_settings(journal_id)))
    started = time.monotonic()
    error = None
    try:
        result = bot.ask(str(case["question"]))
        answer = result.answer
        evidence = result.evidence or {}
        automatic = auto_evaluate(case, evidence, answer)
    except Exception as exc:  # noqa: BLE001 - benchmark must preserve the failure
        answer = ""
        evidence = {}
        error = f"{type(exc).__name__}: {exc}"
        automatic = {
            "passed": False,
            "failures": [error],
            "expected_status": case["expected_status"],
            "actual_status": "ERROR",
        }
    return {
        "id": case["id"],
        "role": case["role"],
        "split": case.get("split"),
        "title": case["title"],
        "question": case["question"],
        "expected_status": case["expected_status"],
        "capabilities": case["capabilities"],
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "error": error,
        "auto_evaluation": automatic,
        "stages": {
            "intent": evidence.get("turn_intent") or {},
            "plan": evidence.get("query_plan") or {},
            "plan_validation": evidence.get("validation_report") or {},
            "execution_trace": evidence.get("execution_trace") or [],
            "operation_results": evidence.get("operation_results") or [],
            "coverage": evidence.get("coverage_report") or {},
            "evidence_bundle": evidence.get("evidence_bundle") or [],
            "quality": evidence.get("quality_report") or {},
            "timings_ms": evidence.get("stage_timings_ms") or {},
            "errors": evidence.get("errors") or [],
        },
        "answer": answer,
        "human_review_focus": case.get("human_review_focus") or [],
        "human_score": human_score_template(payload),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ids", help="comma-separated case ids")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the dataset and print coverage without calling the model",
    )
    args = parser.parse_args()

    payload = load_spec(args.spec)
    errors = validate_spec(payload)
    summary = coverage_summary(payload)
    if errors:
        print(json.dumps({"valid": False, "errors": errors, "summary": summary}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    if args.dry_run:
        print(json.dumps({"valid": True, "summary": summary}, ensure_ascii=False, indent=2))
        return

    cases = _select_cases(payload["cases"], args.ids)
    bots: Dict[str, ChatOrchestrator] = {}
    rows: List[Dict[str, Any]] = []
    for case in cases:
        row = run_case(case, bots, payload)
        rows.append(row)
        status = "AUTO-PASS" if row["auto_evaluation"]["passed"] else "REVIEW"
        print(
            f"{case['id']} {status} {row['elapsed_ms']}ms "
            f"{row['auto_evaluation'].get('failures') or []}",
            flush=True,
        )

    report = {
        "spec_version": payload["spec_version"],
        "generated_at_epoch": int(time.time()),
        "dataset_summary": summary,
        "executed_cases": len(rows),
        "auto_passed": sum(1 for row in rows if row["auto_evaluation"]["passed"]),
        "pending_human_review": len(rows),
        "cases": rows,
    }
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
