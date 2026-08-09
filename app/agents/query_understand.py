"""Query Understanding: LLM fills Intent Schema into state.intent."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from app.agents.intent_schema import (
    CONFIDENCE_THRESHOLD,
    empty_intent,
    intent_to_entities,
    normalize_intent,
)
from app.agents.state import JournalState
from app.config import get_settings
from app.services.minimax_chat import MiniMaxChat


def _parse_json_blob(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _qu_enabled() -> bool:
    raw = (os.getenv("ENABLE_QUERY_UNDERSTAND") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _boost_confidence(intent: Dict[str, Any], question: str) -> Dict[str, Any]:
    """Raise confidence when slots clearly match a known schema combo."""
    out = dict(intent)
    conf = float(out.get("confidence") or 0.0)
    q = question or ""
    entity = out.get("entity")
    op = out.get("operation")
    goal = out.get("goal")
    topics = out.get("topic") or []
    author = out.get("author_name")
    inst = out.get("institution")
    tr = out.get("time_range") or {}

    if goal == "submission_fit" and topics:
        conf = max(conf, 0.85)
    if goal == "refuse":
        conf = max(conf, 0.9)
    if entity == "author" and op == "rank" and (tr.get("start") or tr.get("last_n")):
        conf = max(conf, 0.85 if topics or "作者" in q else conf)
    if entity == "author" and op == "rank" and re.search(r"前|排名|发文量", q):
        conf = max(conf, 0.82)
    if entity == "author" and op == "profile" and author:
        conf = max(conf, 0.85)
    if entity == "institution" and op == "rank":
        conf = max(conf, 0.88)
    if entity == "institution" and inst and op in {"summarize", "profile", "search", "rank"}:
        conf = max(conf, 0.85)
    if op == "trend" and re.search(r"趋势|增速|逐年|热门关键词|热词", q):
        conf = max(conf, 0.85)
    if op == "compare" and re.search(r"对比|变化|热点", q):
        conf = max(conf, 0.8)
    if author and out.get("author_name_b") and re.search(r"合作|合著", q):
        conf = max(conf, 0.88)
    out["confidence"] = max(0.0, min(conf, 1.0))
    return out


def llm_fill_intent(
    question: str,
    draft: Dict[str, Any],
    chat: Optional[MiniMaxChat] = None,
) -> Dict[str, Any]:
    """Ask MiniMax for a strict Intent Schema JSON, then normalize."""
    chat = chat or MiniMaxChat(get_settings())
    prompt = f"""你是期刊问答系统的 Query Understanding 模块。根据用户问题与正则草稿，输出封闭 Intent Schema JSON（不要解释）。

Schema（封闭枚举）:
{{
  "entity": "paper|author|institution|topic|journal",
  "operation": "search|rank|trend|compare|summarize|recommend|profile|coverage",
  "goal": "research_analysis|submission_fit|inventory|refuse",
  "sources": ["sql","kg","rag"] 可多选，通常优先 sql,
  "topic": ["短主题词，不要整句"],
  "author_name": "人名或null",
  "author_name_b": "第二作者或null",
  "institution": "机构全名或null",
  "time_range": {{"start": 年或null, "end": 年或null, "last_n": 近N年或null}},
  "metric": "publication_count|keyword_freq|collaboration|coverage",
  "top_n": 数字或null,
  "confidence": 0到1,
  "legacy_task": null或旧task名,
  "notes": "简短备注"
}}

规则：
1. 发文量前十作者 → entity=author, operation=rank, metric=publication_count。
2. 某主题（如水稻）相关前十作者 → entity=author, operation=rank, topic=["水稻"]。
3. 某机构相关作者/代表成果 → entity=institution, operation=summarize或profile, 填 institution。
4. 发文趋势/热门关键词 → entity=journal, operation=trend；若问热词可 sources=["sql"]。
5. 适合投稿/是否适合发某主题 → goal=submission_fit, operation=coverage, entity=topic, 填 topic。
6. 高被引/被引次数 → goal=refuse, legacy_task=unsupported_citations。
7. author_name 必须是真实人名；宏观期刊问题不要填作者。
8. topic 只要核心词（基因编辑、数字经济），不要「发文量」「有哪些」。
9. 「近五年/近十年」填 time_range.last_n 或换算 start/end。
10. confidence：槽位清晰≥0.8；模糊≤0.5。

用户问题：{question}
正则草稿：{json.dumps(draft, ensure_ascii=False)}
"""
    raw = chat.chat(
        [
            {
                "role": "system",
                "content": "你只输出合法 JSON，符合 Intent Schema 封闭枚举。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=500,
    )
    data = _parse_json_blob(raw)
    intent = normalize_intent(data, question=question, draft=draft)
    return _boost_confidence(intent, question)


def heuristic_intent(question: str, draft: Dict[str, Any]) -> Dict[str, Any]:
    """Offline / LLM-fail fallback: build a low-confidence schema from regex draft."""
    raw: Dict[str, Any] = {
        "entity": "journal",
        "operation": "search",
        "goal": "research_analysis",
        "sources": ["sql"],
        "topic": draft.get("keywords") or [],
        "author_name": draft.get("author_name"),
        "author_name_b": draft.get("author_name_b"),
        "institution": draft.get("institution"),
        "time_range": {
            "start": draft.get("year_start"),
            "end": draft.get("year_end"),
            "last_n": None,
        },
        "metric": "publication_count",
        "top_n": None,
        "confidence": 0.45,
    }
    q = question or ""
    if re.search(r"适合投稿|是否适合投|适合投|投稿前|适合发", q):
        raw.update(
            {
                "entity": "topic",
                "operation": "coverage",
                "goal": "submission_fit",
                "confidence": 0.8,
            }
        )
    elif re.search(r"被引用|高被引|被引次数|citation", q, re.I):
        raw.update({"goal": "refuse", "confidence": 0.9})
    elif re.search(r"趋势|增速|逐年|热门关键词|热词", q):
        raw.update({"entity": "journal", "operation": "trend", "confidence": 0.7})
    elif re.search(r"作者", q) and re.search(r"前|排名|发文量", q):
        raw.update({"entity": "author", "operation": "rank", "confidence": 0.7})
    elif draft.get("institution") and re.search(
        r"作者|代表(性)?成果|代表论文|学者", q
    ):
        raw.update(
            {
                "entity": "institution",
                "operation": "summarize",
                "institution": draft.get("institution"),
                "confidence": 0.85,
            }
        )
    elif re.search(
        r"([\u4e00-\u9fff]{2,20}(?:大学|学院|研究院|研究所|科学院)).{0,12}"
        r"(作者|代表(性)?成果|代表论文)",
        q,
    ):
        m = re.search(
            r"([\u4e00-\u9fff]{2,20}(?:大学|学院|研究院|研究所|科学院))",
            q,
        )
        raw.update(
            {
                "entity": "institution",
                "operation": "summarize",
                "institution": m.group(1) if m else None,
                "confidence": 0.85,
            }
        )
    elif draft.get("author_name") and not draft.get("author_name_b"):
        if re.search(r"发文|轨迹|合作|机构", q):
            raw.update(
                {
                    "entity": "author",
                    "operation": "profile",
                    "confidence": 0.7,
                }
            )
    intent = normalize_intent(raw, question=question, draft=draft)
    return _boost_confidence(intent, question)


def query_understand_node(state: JournalState) -> Dict[str, Any]:
    question = state.get("question") or ""
    draft = dict(state.get("entities") or {})
    if not _qu_enabled():
        intent = empty_intent()
        intent["confidence"] = 0.0
        intent["notes"] = "ENABLE_QUERY_UNDERSTAND=0"
        return {"intent": intent, "stage": "understood"}

    try:
        intent = llm_fill_intent(question, draft)
        intent["notes"] = (intent.get("notes") or "")[:200]
        meta_ok = True
    except Exception as e:
        intent = heuristic_intent(question, draft)
        intent["notes"] = f"QU LLM失败，启发式: {e}"[:200]
        meta_ok = False

    # Merge schema slots into entities (keep extract_meta / dois)
    projected = intent_to_entities(intent)
    entities = dict(draft)
    for k, v in projected.items():
        if v is not None and v != []:
            entities[k] = v
    entities["intent_meta"] = {
        "llm_ok": meta_ok,
        "confidence": intent.get("confidence"),
        "threshold": CONFIDENCE_THRESHOLD,
    }
    return {"intent": intent, "entities": entities, "stage": "understood"}
