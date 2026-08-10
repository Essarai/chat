from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.agents.understand import extract_year_window


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _ordinal(question: str) -> Optional[int]:
    match = re.search(r"第\s*(\d{1,2}|[一二三四五六七八九十两])\s*(?:位|名|个)?", question or "")
    if not match:
        return None
    raw = match.group(1)
    return int(raw) if raw.isdigit() else _CN_NUM.get(raw)


def _new_question_signal(question: str) -> bool:
    q = question or ""
    return bool(
        re.search(r"(?:近|过去|最近)\s*(?:\d+|[一二三四五六七八九十两]+)\s*年", q)
        and re.search(r"机构|趋势|热点|关键词|投稿|研究方向|作者排名|发文最多", q)
    )


def _top_n(question: str, default: int = 10) -> int:
    match = re.search(r"(?:Top|前)\s*(\d{1,2})", question or "", re.I)
    return max(1, min(int(match.group(1)), 50)) if match else default


def _structured_new_question(question: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Zero-model path for high-confidence, fully structured ranking questions."""
    q = question or ""
    if not re.search(r"作者", q) or not re.search(r"发文最多|发文量|高产|排名|前\s*\d+", q):
        return None, None
    if re.search(r"论文|文章", q) and re.search(r"列出|展开|具体", q):
        return None, None
    y0, y1 = extract_year_window(q, default_last_n=None)
    if y0 is None and re.search(r"近年|最近几年", q):
        y1 = datetime.now().year
        y0 = y1 - 4
    intent = {
        "kind": "new_question",
        "action": "query",
        "target_turn_id": None,
        "target_entity": "author",
        "selector": {"top_n": _top_n(q)},
        "explicit_constraints": {"year_start": y0, "year_end": y1},
        "confidence": 0.98,
        "needs_clarification": False,
        "source": "deterministic",
    }
    return intent, {
        "task": "top_authors",
        "action": "query",
        "targets": [],
        "constraints": {"year_start": y0, "year_end": y1},
        "sources": ["sql"],
        "operations": ["top_authors"],
        "sql_ops": ["top_authors"],
        "year_start": y0,
        "year_end": y1,
        "top_n": _top_n(q),
        "presentation": {"template": "top_authors"},
        "complexity": "simple",
        "plan_source": "deterministic",
        "locked": True,
        "focus": "按作者署名论文去重统计发文量并排名。",
    }


def understand_contextual_turn(
    question: str,
    previous_turn: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Return (TurnIntent, locked QueryPlan) for deterministic follow-ups."""
    q = (question or "").strip()
    if not q:
        return None, None
    if not previous_turn:
        return _structured_new_question(q)
    if _new_question_signal(q):
        structured = _structured_new_question(q)
        if structured[0] is not None:
            return structured
        return {
            "kind": "new_question",
            "action": "query",
            "target_turn_id": None,
            "target_entity": None,
            "selector": None,
            "explicit_constraints": {},
            "confidence": 0.99,
            "needs_clarification": False,
            "source": "standalone-signal",
        }, None

    result_set = previous_turn.get("result_set") or {}
    items = list(result_set.get("items") or [])
    constraints = dict(result_set.get("constraints") or {})
    prior_plan = dict(previous_turn.get("query_plan") or {})
    continuation = dict(previous_turn.get("continuation") or {})

    papers = [item for item in items if item.get("type") == "paper" and item.get("doi")]
    if papers and re.search(r"这些|上述|其", q) and re.search(r"按.*年|年份.*排序|时间.*排序", q):
        ordered = sorted(
            papers,
            key=lambda item: (int(item.get("year") or 0), str(item.get("title") or "")),
            reverse=not bool(re.search(r"升序|从早到晚", q)),
        )
        intent = {
            "kind": "followup",
            "action": "sort",
            "target_turn_id": previous_turn.get("turn_id"),
            "target_entity": "paper",
            "selector": {"scope": "all"},
            "explicit_constraints": {"sort": "year_asc" if re.search(r"升序|从早到晚", q) else "year_desc"},
            "inherited_constraints": constraints,
            "confidence": 1.0,
            "needs_clarification": False,
            "source": "context",
        }
        return intent, {
            "task": "resultset_papers",
            "action": "sort",
            "targets": ordered,
            "constraints": constraints,
            "sources": ["sql"],
            "operations": ["present_resultset"],
            "sql_ops": ["present_resultset"],
            "year_start": constraints.get("year_start"),
            "year_end": constraints.get("year_end"),
            "max_items": 100,
            "max_chars": 24000,
            "presentation": {"group_by": "author", "sort": intent["explicit_constraints"]["sort"]},
            "complexity": "simple",
            "plan_source": "conversation",
            "locked": True,
            "focus": "仅对上一轮论文结果集重新排序。",
        }

    if re.fullmatch(r"(?:继续|继续列出|继续展开|还有|剩余|下一批)[。！!？?\s]*", q):
        if continuation.get("has_more") and prior_plan.get("task") == "authors_papers":
            intent = {
                "kind": "followup",
                "action": "continue",
                "target_turn_id": previous_turn.get("turn_id"),
                "target_entity": "paper",
                "selector": None,
                "explicit_constraints": {},
                "confidence": 1.0,
                "needs_clarification": False,
                "source": "context",
            }
            plan = dict(prior_plan)
            plan["offset"] = int(continuation.get("next_offset") or 0)
            plan["locked"] = True
            plan["action"] = "continue"
            return intent, plan

    authors = [item for item in items if item.get("type") == "author" and item.get("id")]
    ordinal = _ordinal(q)
    asks_papers = bool(
        re.search(r"展开|具体|详细|列(?:出|一下|一列)?|查看|给出|罗列|展示", q)
        and (re.search(r"论文|发文|文章|文献", q) or (ordinal and "作者" in q))
    )
    if authors and asks_papers:
        selected = authors
        singular_pronoun = bool(re.search(r"(?:他|她|该作者)的", q))
        if ordinal is not None:
            selected = authors[ordinal - 1 : ordinal]
        elif singular_pronoun and len(authors) > 1:
            intent = {
                "kind": "ambiguous",
                "action": "select",
                "target_turn_id": previous_turn.get("turn_id"),
                "target_entity": "author",
                "selector": None,
                "explicit_constraints": {},
                "confidence": 1.0,
                "needs_clarification": True,
                "clarification": "上一轮有多位作者，请说明要展开哪一位（例如“第 3 位作者”）。",
                "source": "context",
            }
            return intent, {
                "task": "clarification",
                "action": "select",
                "sources": ["sql"],
                "sql_ops": [],
                "locked": True,
                "focus": intent["clarification"],
            }
        if not selected:
            return None, None
        intent = {
            "kind": "followup",
            "action": "expand",
            "target_turn_id": previous_turn.get("turn_id"),
            "target_entity": "author",
            "selector": {"ordinal": ordinal} if ordinal else {"scope": "all"},
            "explicit_constraints": {},
            "inherited_constraints": constraints,
            "confidence": 1.0,
            "needs_clarification": False,
            "source": "context",
        }
        return intent, {
            "task": "authors_papers",
            "action": "expand",
            "targets": selected,
            "constraints": constraints,
            "sources": ["sql"],
            "operations": ["papers_for_authors"],
            "sql_ops": ["papers_for_authors"],
            "author_ids": [a["id"] for a in selected],
            "year_start": constraints.get("year_start"),
            "year_end": constraints.get("year_end"),
            "keywords": list(constraints.get("keywords") or []),
            "offset": 0,
            "max_items": 100,
            "max_chars": 24000,
            "presentation": {"group_by": "author", "include_all": True},
            "continuation": None,
            "complexity": "simple",
            "plan_source": "conversation",
            "locked": True,
            "focus": "列出上一轮所选作者在同一时间范围内的全部论文。",
        }

    return None, None
