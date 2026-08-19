from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


_CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_NUMBER = r"\d{1,3}|[一二三四五六七八九十两]"
_CONTINUE_RE = re.compile(
    r"^(?:继续|继续列出|继续展开|还有|更多|剩余|下一批)[。！!？?\s]*$"
)
_CONTEXT_WORD_RE = re.compile(
    r"这些|上述|该批|其中|他们|她们|该作者|该论文|这些作者|这些论文"
)


def _number(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    return _CN_NUM.get(raw)


def _ordinal(question: str) -> Optional[int]:
    match = re.search(rf"第\s*({_NUMBER})\s*(?:位|名|个|篇|条)?", question)
    return _number(match.group(1)) if match else None


def _author_selection_count(question: str) -> Optional[int]:
    match = re.search(
        rf"前\s*({_NUMBER})\s*(?:(?:位|名|个)(?:作者)?|(?:的)?作者)",
        question,
    )
    return _number(match.group(1)) if match else None


def _paper_selection_count(question: str) -> Optional[int]:
    match = re.search(rf"前\s*({_NUMBER})\s*(?:篇|条|个)(?:论文|文章|文献)?", question)
    return _number(match.group(1)) if match else None


def _explicit_year_window(question: str) -> Tuple[Optional[int], Optional[int]]:
    range_match = re.search(
        r"((?:19|20)\d{2})\s*(?:年)?\s*(?:-|~|~|—|至|到)\s*"
        r"((?:19|20)\d{2})\s*(?:年)?",
        question,
    )
    if range_match:
        first, second = int(range_match.group(1)), int(range_match.group(2))
        return min(first, second), max(first, second)

    recent_match = re.search(rf"(?:近|过去|最近)\s*({_NUMBER})\s*年", question)
    if recent_match:
        years = _number(recent_match.group(1))
        if years:
            end = datetime.now().year
            return end - years + 1, end

    years = [int(value) for value in re.findall(r"(?:19|20)\d{2}", question)]
    if len(years) == 1:
        return years[0], years[0]
    return None, None


def _item_type(item: Dict[str, Any]) -> str:
    return str(item.get("type") or item.get("entity_type") or "").strip().lower()


def _paper_doi(item: Dict[str, Any]) -> str:
    return str(item.get("doi") or item.get("id") or "").strip()


def _author_id(item: Dict[str, Any]) -> str:
    return str(item.get("id") or item.get("author_id") or "").strip()


def _clarification(
    message: str,
    previous_turn: Optional[Dict[str, Any]],
    *,
    action: str = "select",
    target_entity: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "kind": "ambiguous",
        "action": action,
        "target_turn_id": (previous_turn or {}).get("turn_id"),
        "target_entity": target_entity,
        "selector": None,
        "explicit_constraints": {},
        "confidence": 1.0,
        "needs_clarification": True,
        "clarification": message,
        "requested_operations": ["clarification"],
        "source": "context",
    }


class ContextResolver:
    """Resolve only deterministic references to the persisted previous result set.

    The output is a semantic intent with selected entities and inherited slots. It
    deliberately never emits a query plan; the pipeline's PlanCompiler remains the
    sole authority that turns this intent into executable operations.
    """

    def resolve(
        self,
        question: str,
        previous_turn: Optional[Dict[str, Any]],
        last_dois: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        q = (question or "").strip()
        if not q:
            return None

        continuation_signal = bool(_CONTINUE_RE.fullmatch(q))
        reference_signal = bool(
            continuation_signal
            or _ordinal(q) is not None
            or _CONTEXT_WORD_RE.search(q)
        )
        if not previous_turn:
            if reference_signal:
                return _clarification(
                    "当前会话中没有可引用的上一轮结果，请先说明要查询的作者、论文或机构。",
                    None,
                )
            return None

        previous = dict(previous_turn)
        result_set = dict(previous.get("result_set") or {})
        items = [dict(item) for item in (result_set.get("items") or []) if isinstance(item, dict)]
        constraints = dict(result_set.get("constraints") or {})
        prior_plan = dict(previous.get("query_plan") or {})
        for key in ("year_start", "year_end", "keywords"):
            if constraints.get(key) in (None, [], "") and prior_plan.get(key) not in (None, [], ""):
                constraints[key] = prior_plan.get(key)

        continuation = dict(previous.get("continuation") or {})
        if continuation_signal:
            if continuation.get("has_more"):
                return {
                    "kind": "followup",
                    "action": "continue",
                    "target_turn_id": previous.get("turn_id"),
                    "target_entity": result_set.get("entity_type") or None,
                    "selector": None,
                    "explicit_constraints": {},
                    "inherited_constraints": constraints,
                    "confidence": 1.0,
                    "needs_clarification": False,
                    "previous_query_plan": prior_plan,
                    "continuation": continuation,
                    "source": "context",
                }
            return _clarification(
                "上一轮没有可继续的截断结果，请说明要查询或展开的内容。",
                previous,
                action="continue",
            )

        authors = [item for item in items if _item_type(item) == "author" and _author_id(item)]
        ordinal = _ordinal(q)
        author_count = _author_selection_count(q)
        asks_author_papers = bool(
            re.search(r"论文|文章|发文|文献", q)
            and (
                ordinal is not None
                or author_count is not None
                or re.search(r"全部|所有|这些作者|上述作者|该作者|他的|她的", q)
            )
        )
        if authors and asks_author_papers:
            selector: Dict[str, Any]
            if ordinal is not None:
                selected = authors[ordinal - 1 : ordinal] if ordinal > 0 else []
                selector = {"ordinal": ordinal}
            elif author_count is not None:
                selected = authors[:author_count]
                selector = {"first": author_count}
            elif re.search(r"全部|所有|这些作者|上述作者", q):
                selected = authors
                selector = {"scope": "all"}
            elif len(authors) == 1:
                selected = authors
                selector = {"scope": "only"}
            else:
                return _clarification(
                    "上一轮有多位作者，请说明要展开哪一位（例如“第 3 位作者”）。",
                    previous,
                    target_entity="author",
                )
            if not selected:
                return _clarification(
                    "选择的序号超出上一轮作者结果范围，请换一个有效序号。",
                    previous,
                    target_entity="author",
                )

            explicit_start, explicit_end = _explicit_year_window(q)
            year_start = explicit_start if explicit_start is not None else constraints.get("year_start")
            year_end = explicit_end if explicit_end is not None else constraints.get("year_end")
            explicit_constraints: Dict[str, Any] = {}
            if explicit_start is not None or explicit_end is not None:
                explicit_constraints = {"year_start": year_start, "year_end": year_end}
            return {
                "kind": "followup",
                "action": "expand",
                "target_turn_id": previous.get("turn_id"),
                "target_entity": "author",
                "selector": selector,
                "explicit_constraints": explicit_constraints,
                "inherited_constraints": constraints,
                "confidence": 1.0,
                "needs_clarification": False,
                "requested_operations": ["papers_for_authors"],
                "targets": selected,
                "author_ids": [_author_id(item) for item in selected],
                "year_start": year_start,
                "year_end": year_end,
                "keywords": list(constraints.get("keywords") or []),
                "source": "context",
            }

        papers = [item for item in items if _item_type(item) == "paper" and _paper_doi(item)]
        if not papers and last_dois:
            papers = [{"type": "paper", "doi": doi} for doi in last_dois if str(doi).strip()]

        paper_reference = bool(
            re.search(r"这些|上述|该批|该论文", q)
            or re.match(rf"^(?:前\s*{_NUMBER}\s*(?:篇|条)|全部|所有|按年份)", q)
        )
        topic_summary = bool(
            papers
            and (paper_reference or re.match(r"^(?:总结|归纳|分析)", q))
            and re.search(r"主题|方向|关键词|相似|共性|共同点|重合", q)
        )
        if topic_summary:
            unique: Dict[str, Dict[str, Any]] = {}
            paper_authors: Dict[str, List[Dict[str, Any]]] = {}
            for item in papers:
                doi = _paper_doi(item)
                unique.setdefault(doi, item)
                author_id = str(item.get("author_id") or "").strip()
                author_name = str(item.get("author_name") or "").strip()
                if author_id or author_name:
                    record = {"author_id": author_id or None, "name": author_name or author_id}
                    if record not in paper_authors.setdefault(doi, []):
                        paper_authors[doi].append(record)
            targets = list(unique.values())
            return {
                "kind": "followup",
                "action": "summarize",
                "target_turn_id": previous.get("turn_id"),
                "target_entity": "paper",
                "selector": {"scope": "all"},
                "explicit_constraints": {},
                "inherited_constraints": constraints,
                "confidence": 1.0,
                "needs_clarification": False,
                "requested_operations": ["paper_set_topic_summary"],
                "targets": targets,
                "dois": list(unique),
                "paper_authors": [
                    {"doi": doi, "authors": paper_authors.get(doi, [])} for doi in unique
                ],
                "source_record_count": len(papers),
                "year_start": constraints.get("year_start"),
                "year_end": constraints.get("year_end"),
                "source": "context",
            }

        paper_count = _paper_selection_count(q)
        sort_requested = bool(re.search(r"按.*年|年份.*排序|时间.*排序|从早到晚|从晚到早", q))
        show_all = bool(re.search(r"全部|所有|都(?:展示|列出|给出|罗列)|完整(?:展示|列出)", q))
        display_requested = bool(
            papers
            and paper_reference
            and (
                sort_requested
                or paper_count is not None
                or show_all
                or re.search(r"展示|列出|罗列|给出", q)
            )
        )
        if display_requested:
            targets = list(papers)
            sort_order: Optional[str] = None
            if sort_requested:
                ascending = bool(re.search(r"升序|从早到晚|由早到晚", q))
                sort_order = "year_asc" if ascending else "year_desc"
                targets.sort(
                    key=lambda item: (
                        int(item.get("year") or 0),
                        str(item.get("title") or ""),
                        _paper_doi(item),
                    ),
                    reverse=not ascending,
                )
            if paper_count is not None:
                targets = targets[:paper_count]
            selector = (
                {"first": paper_count}
                if paper_count is not None
                else {"scope": "all"}
            )
            explicit_constraints = {"sort": sort_order} if sort_order else {}
            return {
                "kind": "followup",
                "action": "sort" if sort_order else "present",
                "target_turn_id": previous.get("turn_id"),
                "target_entity": "paper",
                "selector": selector,
                "explicit_constraints": explicit_constraints,
                "inherited_constraints": constraints,
                "confidence": 1.0,
                "needs_clarification": False,
                "requested_operations": ["present_resultset"],
                "targets": targets,
                "year_start": constraints.get("year_start"),
                "year_end": constraints.get("year_end"),
                "presentation": {"sort": sort_order, "limit": paper_count},
                "source": "context",
            }

        # A contextual pronoun or selector was detected, but the persisted entity
        # set cannot support a single deterministic interpretation.
        if reference_signal:
            entity_types = sorted({_item_type(item) for item in items if _item_type(item)})
            entity_label = "、".join(entity_types) if entity_types else "结果"
            return _clarification(
                f"无法唯一确定要对上一轮{entity_label}执行什么操作，请说明选择对象和需要的结果。",
                previous,
            )
        return None
