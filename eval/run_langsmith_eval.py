#!/usr/bin/env python3
"""Sync the ten product contracts and run them as a LangSmith experiment."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langsmith import Client  # noqa: E402

from app.agents.eval_contracts import evaluate_core_contract  # noqa: E402
from app.config import get_corpus_settings  # noqa: E402
from app.services.production_orchestrator import ChatOrchestrator  # noqa: E402

DEFAULT_DATASET = "journal-qa-core-10"
_BOTS: Dict[str, ChatOrchestrator] = {}


def load_specs() -> list[Dict[str, Any]]:
    return json.loads((EVAL_DIR / "core_user_tasks_specs.json").read_text(encoding="utf-8"))


def build_examples(dataset_name: str, specs: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [
        {
            "id": uuid.uuid5(uuid.NAMESPACE_URL, f"journal-qa:{dataset_name}:{spec['id']}"),
            "inputs": {
                "case_id": spec["id"],
                "question": spec["example_question"],
                "journal_id": spec["journal_id"],
            },
            "outputs": {"contract": spec},
            "metadata": {"case_id": spec["id"], "role": spec["role"], "spec_version": "v1"},
            "split": "core",
        }
        for spec in specs
    ]


def sync_dataset(client: Client, dataset_name: str, specs: list[Dict[str, Any]]):
    dataset = (
        client.read_dataset(dataset_name=dataset_name)
        if client.has_dataset(dataset_name=dataset_name)
        else client.create_dataset(
            dataset_name=dataset_name,
            description="期刊问答 10 个核心用户任务及确定性质量验收契约",
            metadata={"spec_version": "v1", "case_count": len(specs)},
        )
    )
    examples = build_examples(dataset_name, specs)
    existing_ids = {str(example.id) for example in client.list_examples(dataset_id=dataset.id)}
    updates = [example for example in examples if str(example["id"]) in existing_ids]
    creates = [example for example in examples if str(example["id"]) not in existing_ids]
    if updates:
        client.update_examples(dataset_id=dataset.id, updates=updates)
    if creates:
        client.create_examples(dataset_id=dataset.id, examples=creates)
    return dataset


def target(inputs: Dict[str, Any]) -> Dict[str, Any]:
    journal_id = str(inputs["journal_id"])
    bot = _BOTS.setdefault(journal_id, ChatOrchestrator(get_corpus_settings(journal_id)))
    result = bot.ask(str(inputs["question"]))
    evidence = result.evidence or {}
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
    return {"answer": result.answer, "state": state}


def _evaluation(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]) -> Dict[str, Any]:
    if not outputs or not reference_outputs or "contract" not in reference_outputs:
        return {
            "passed": False,
            "failures": ["missing target or reference output"],
            "quality": {},
        }
    return evaluate_core_contract(
        reference_outputs["contract"], outputs.get("state") or {}, outputs.get("answer") or ""
    )


def answer_relevance_precision(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]):
    score = (_evaluation(outputs, reference_outputs).get("quality") or {}).get(
        "answer_relevance_precision", 0.0
    )
    return {"key": "answer_relevance_precision", "score": float(score)}


def required_goal_coverage(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]):
    score = (_evaluation(outputs, reference_outputs).get("quality") or {}).get(
        "required_goal_coverage", 0.0
    )
    return {"key": "required_goal_coverage", "score": float(score)}


def evidence_accuracy(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]):
    score = (_evaluation(outputs, reference_outputs).get("quality") or {}).get(
        "evidence_accuracy", 0.0
    )
    return {"key": "evidence_accuracy", "score": float(score)}


def contract_hard_gate(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]):
    evaluation = _evaluation(outputs, reference_outputs)
    return {
        "key": "contract_hard_gate",
        "score": 1 if evaluation.get("passed") else 0,
        "comment": "; ".join(evaluation.get("failures") or []) or "all gates passed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--experiment-prefix", default="journal-qa-core")
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--sync-only", action="store_true")
    args = parser.parse_args()

    client = Client()
    specs = load_specs()
    dataset = sync_dataset(client, args.dataset, specs)
    print(f"dataset={dataset.name} cases={len(specs)}")
    if args.sync_only:
        return

    results = client.evaluate(
        target,
        data=dataset.name,
        evaluators=[
            answer_relevance_precision,
            required_goal_coverage,
            evidence_accuracy,
            contract_hard_gate,
        ],
        experiment_prefix=args.experiment_prefix,
        description="生产问答链路 + 核心任务确定性硬门禁",
        metadata={"spec_version": "v1", "case_count": len(specs)},
        max_concurrency=max(1, args.max_concurrency),
    )
    results.wait()
    print(f"experiment={results.experiment_name}")


if __name__ == "__main__":
    main()
