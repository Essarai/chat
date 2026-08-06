"""Controlled ReAct Controller: whitelist tools, max_steps, evidence_bundle."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.agents.specialists.kg_agent import run_kg_agent
from app.agents.specialists.rag_agent import run_rag_agent
from app.agents.specialists.sql_agent import run_sql_agent
from app.agents.state import JournalState
from app.config import get_settings
from app.services.minimax_chat import MiniMaxChat

MAX_STEPS = 5

SQL_OPS = {
    "yearly_counts",
    "yoy_growth",
    "journal_overview",
    "top_keywords",
    "top_authors",
    "top_institutions",
    "authors_by_keyword",
    "institutions_by_keyword",
    "papers_by_top_keywords",
    "topic_keyword_counts",
    "topic_yearly",
    "keywords_by_periods",
    "author_profile",
    "author_keywords_sample",
    "execute_plan",
    "legacy",
}
KG_OPS = {"keyword_ego", "author_ego", "paper_neighborhood", "execute_plan"}
AI_TOPIC_KWS = [
    "人工智能",
    "机器学习",
    "深度学习",
    "神经网络",
    "智能算法",
    "遥感",
    "高光谱",
]


def _parse_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _years(question: str, entities: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    q = question or ""
    m = re.search(r"(?:过去|近|最近)\s*(\d{1,2})\s*年|(\d{1,2})\s*年来", q)
    if m:
        n = int(m.group(1) or m.group(2))
        end = datetime.now().year
        return end - n + 1, end
    return entities.get("year_start"), entities.get("year_end")


def _summarize(source: str, data: Dict[str, Any], limit: int = 900) -> str:
    if not data:
        return f"{source}: 空"
    if data.get("error"):
        return f"{source}错误: {data.get('error')}"
    # prefer compact structured highlights
    parts: List[str] = [f"[{source}]"]
    if data.get("fastest_growth"):
        parts.append(f"增速峰值: {data['fastest_growth']}")
    if data.get("yoy"):
        parts.append(
            "逐年: "
            + ", ".join(
                f"{r.get('year')}:{r.get('paper_count')}" for r in (data.get("yoy") or [])[:12]
            )
        )
    elif data.get("yearly"):
        parts.append(
            "逐年: "
            + ", ".join(
                f"{r.get('year')}:{r.get('paper_count')}"
                for r in (data.get("yearly") or [])[:12]
            )
        )
    if data.get("keywords"):
        parts.append(
            "热词: "
            + ", ".join(
                f"{r.get('keyword')}({r.get('paper_count')})"
                for r in (data.get("keywords") or [])[:8]
            )
        )
    if data.get("authors") and isinstance(data.get("authors"), list):
        a0 = data["authors"][0] if data["authors"] else {}
        if isinstance(a0, dict) and (a0.get("name_zh") or a0.get("paper_count") is not None):
            parts.append(
                "作者: "
                + ", ".join(
                    f"{a.get('name_zh')}({a.get('paper_count')})"
                    for a in data["authors"][:8]
                    if isinstance(a, dict)
                )
            )
    if data.get("institutions"):
        parts.append(
            "机构: "
            + ", ".join(
                f"{i.get('institution') or i.get('name')}({i.get('paper_count')})"
                for i in (data.get("institutions") or [])[:8]
                if isinstance(i, dict)
            )
        )
    if data.get("directions"):
        parts.append(
            "方向: "
            + ", ".join(
                f"{d.get('keyword')}({d.get('paper_count')})"
                for d in data["directions"][:5]
            )
        )
    if data.get("topic_keywords"):
        parts.append(
            "专题词: "
            + ", ".join(
                f"{r.get('keyword')}({r.get('paper_count')})"
                for r in data["topic_keywords"][:8]
            )
        )
    if data.get("periods"):
        for p in (data.get("periods") or [])[:3]:
            kws = ", ".join(
                f"{r.get('keyword')}({r.get('paper_count')})"
                for r in (p.get("keywords") or [])[:6]
            )
            parts.append(f"阶段{p.get('period')}: {kws}")
    if data.get("hits"):
        parts.append(
            "RAG命中: "
            + "; ".join(
                f"{h.get('title')}({h.get('year')})" for h in (data.get("hits") or [])[:4]
            )
        )
    if data.get("scope") == "journal_overview":
        parts.append(
            f"概览区间 {data.get('start_year')}-{data.get('end_year')} "
            f"作者数={len(data.get('authors') or [])} 机构数={len(data.get('institutions') or [])}"
        )
    text = " | ".join(parts) if len(parts) > 1 else json.dumps(data, ensure_ascii=False, default=str)[:limit]
    return text[:limit]


def _fingerprint(action: str, args: Dict[str, Any]) -> str:
    raw = json.dumps({"a": action, "args": args}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _build_sql_plan(args: Dict[str, Any], question: str, entities: Dict[str, Any]) -> Dict[str, Any]:
    y0, y1 = _years(question, entities)
    ops = args.get("sql_ops") or args.get("operations") or []
    if isinstance(ops, str):
        ops = [ops]
    op = args.get("operation")
    if op and op not in ops:
        ops = [op] + list(ops)
    ops = [o for o in ops if o in SQL_OPS]
    task = args.get("task") or "generic"
    kws = args.get("keywords") or entities.get("keywords") or []
    if not kws and re.search(r"人工智能|机器学习|深度学习", question or ""):
        kws = list(AI_TOPIC_KWS)
    if not ops:
        # infer from task hints
        if task == "yearly_growth" or "增长" in (question or ""):
            ops = ["yearly_counts", "yoy_growth"]
            task = "yearly_growth"
        elif task == "journal_overview" or "发展历程" in (question or ""):
            ops = ["journal_overview"]
            task = "journal_overview"
        elif task == "keyword_collab" or "合作" in (question or ""):
            ops = ["authors_by_keyword", "institutions_by_keyword"]
            task = "keyword_collab"
        elif task == "top_directions_with_papers" or "研究方向" in (question or ""):
            ops = ["top_keywords", "papers_by_top_keywords"]
            task = "top_directions_with_papers"
        elif task == "top_teams" or "团队" in (question or ""):
            ops = [
                "top_authors",
                "top_institutions",
                "keywords_by_periods",
                "author_keywords_sample",
            ]
            task = "top_teams"
        elif task == "topic_evolution":
            ops = ["topic_keyword_counts", "topic_yearly"]
        else:
            ops = ["yearly_counts", "top_keywords"]
    # expand aliases
    if "yoy_growth" in ops and "yearly_counts" not in ops:
        ops = ["yearly_counts"] + ops
    return {
        "task": task,
        "sources": ["sql"],
        "sql_ops": ops,
        "year_start": args.get("year_start", y0),
        "year_end": args.get("year_end", y1),
        "keywords": kws,
        "author_name": args.get("author_name") or entities.get("author_name"),
        "top_n_directions": args.get("top_n_directions") or 3,
        "focus": args.get("purpose") or "",
    }


def _merge_sql(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    if not existing:
        return new
    out = dict(existing)
    for k, v in (new or {}).items():
        if v is None:
            continue
        if k in {"yearly", "yoy", "keywords", "authors", "institutions", "directions", "periods", "author_keywords", "topic_keywords", "papers"} and v:
            out[k] = v
        elif k not in out or out[k] in (None, "", [], {}):
            out[k] = v
        else:
            out[k] = v
    # prefer richer scope/task
    if new.get("task"):
        out["task"] = new["task"]
    if new.get("scope"):
        out["scope"] = new["scope"]
    return out


def _run_tool(
    action: str,
    args: Dict[str, Any],
    state: JournalState,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """Returns (state_updates, summary, observation_data)."""
    question = state.get("question") or ""
    entities = dict(state.get("entities") or {})
    args = args or {}

    if action == "sql_agent":
        plan = _build_sql_plan(args, question, entities)
        data = run_sql_agent(question, entities, plan)
        prev = state.get("sql_evidence") or {}
        merged = _merge_sql(prev if isinstance(prev, dict) else {}, data)
        summary = _summarize("sql", data)
        return (
            {
                "sql_evidence": merged,
                "query_plan": {**(state.get("query_plan") or {}), **plan, "sources": list(set((state.get("intents") or []) + ["sql"]))},
                "intents": list(dict.fromkeys((state.get("intents") or []) + ["sql"])),
            },
            summary,
            data,
        )

    if action == "kg_agent":
        kws = args.get("keywords") or entities.get("keywords") or []
        if isinstance(kws, str):
            kws = [kws]
        if not kws:
            for term in ("水稻", "番茄"):
                if term in question:
                    kws = [term]
                    break
        plan = {
            "task": args.get("task") or "keyword_collab",
            "kg_ops": [args.get("operation")] if args.get("operation") in KG_OPS else ["keyword_ego"],
            "keywords": kws,
            "author_name": args.get("author_name") or entities.get("author_name"),
            "sources": ["kg"],
        }
        if args.get("operation") == "author_ego" or plan.get("author_name"):
            plan["kg_ops"] = ["author_ego"]
        data = run_kg_agent(question, {**entities, "keywords": kws}, entities.get("dois") or [], plan)
        prev_kg = state.get("kg_evidence") or {}
        if isinstance(prev_kg, dict) and prev_kg and isinstance(data, dict):
            # Keep keyword neighborhood when a follow-up author_ego arrives.
            merged_kg = dict(prev_kg)
            for key in (
                "authors",
                "neighborhood_authors",
                "institutions",
                "neighborhood_institutions",
                "related_keywords",
                "papers",
                "neighborhood",
                "nodes",
                "edges",
                "query_keyword",
                "keyword",
                "source",
            ):
                if key in prev_kg and key not in data:
                    merged_kg[key] = prev_kg[key]
            merged_kg.update({k: v for k, v in data.items() if v not in (None, [], {}, "")})
            if data.get("collaborators"):
                merged_kg["collaborators"] = data.get("collaborators")
            if data.get("author"):
                merged_kg["ego_author"] = data.get("author")
            data = merged_kg
        summary = _summarize("kg", data)
        return (
            {
                "kg_evidence": data,
                "intents": list(dict.fromkeys((state.get("intents") or []) + ["kg"])),
            },
            summary,
            data,
        )

    if action == "rag_agent":
        queries = args.get("queries") or args.get("rag_queries") or [question]
        if isinstance(queries, str):
            queries = [queries]
        y0 = (
            args.get("year_start")
            or entities.get("year_start")
            or (state.get("query_plan") or {}).get("year_start")
            or (state.get("sql_evidence") or {}).get("start_year")
        )
        y1 = (
            args.get("year_end")
            or entities.get("year_end")
            or (state.get("query_plan") or {}).get("year_end")
            or (state.get("sql_evidence") or {}).get("end_year")
        )
        try:
            y0 = int(y0) if y0 is not None else None
        except (TypeError, ValueError):
            y0 = None
        try:
            y1 = int(y1) if y1 is not None else None
        except (TypeError, ValueError):
            y1 = None
        data = run_rag_agent(
            question,
            top_k=state.get("top_k"),
            queries=queries,
            year_start=y0,
            year_end=y1,
        )
        ents = dict(entities)
        ents["dois"] = [h.get("doi") for h in data.get("hits") or [] if h.get("doi")]
        if y0 is not None:
            ents["year_start"] = y0
        if y1 is not None:
            ents["year_end"] = y1
        summary = _summarize("rag", data)
        return (
            {
                "rag_evidence": data,
                "entities": ents,
                "intents": list(dict.fromkeys((state.get("intents") or []) + ["rag"])),
            },
            summary,
            data,
        )

    raise ValueError(f"unknown action: {action}")


def _decide(
    state: JournalState,
    chat: MiniMaxChat,
) -> Dict[str, Any]:
    question = state.get("question") or ""
    entities = {
        k: (state.get("entities") or {}).get(k)
        for k in ("author_name", "year_start", "year_end", "keywords")
    }
    bundle = state.get("evidence_bundle") or []
    goal = state.get("goal") or question
    obs = "\n".join(
        f"step{b.get('step')}: ({b.get('source')}/{b.get('operation')}) {b.get('summary')}"
        for b in bundle[-4:]
    ) or "（尚无证据）"

    prompt = f"""你是期刊知识助手的 Controller（受控 ReAct）。根据用户问题与已有证据，选择下一步唯一动作。
只输出 JSON：
{{
  "thought": "简短理由",
  "action": "sql_agent|kg_agent|rag_agent|finish",
  "args": {{}},
  "status": "continue|done"
}}

工具说明：
- sql_agent: 结构化统计。args 可含 task, sql_ops[], keywords[], year_start, year_end, author_name, top_n_directions
  常用 task: yearly_growth, topic_evolution, keyword_collab, top_directions_with_papers, top_teams, journal_overview, author_profile, keyword_authors
  常用 sql_ops: yearly_counts,yoy_growth,journal_overview,top_keywords,papers_by_top_keywords,authors_by_keyword,institutions_by_keyword,top_authors,top_institutions,keywords_by_periods,author_keywords_sample,topic_keyword_counts,topic_yearly,author_profile
- kg_agent: 合作/实体关系。args 可含 operation(keyword_ego|author_ego), keywords[], author_name
- rag_agent: 语义文献。args 可含 queries[]
- finish: 证据足够，结束检索。args 可含 reason

规则：
1. 每步只选一个 action。
2. 需要中间结果时分多步（如先 top_keywords 再 rag）。
3. 已有足够回答证据时必须 finish。
4. 不要重复完全相同的调用。

目标: {goal}
用户问题: {question}
实体: {json.dumps(entities, ensure_ascii=False)}
已有证据摘要:
{obs}
"""
    raw = chat.chat(
        [
            {"role": "system", "content": "你是受控编排器，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=500,
    )
    return _parse_json(raw)


def _heuristic_first_action(question: str, entities: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic first step when useful; reduces LLM flakiness on Q.md."""
    q = question or ""
    if re.search(r"(每年|逐年).*(发文|数量)|增长最快", q):
        return {
            "thought": "先取逐年发文与增速",
            "action": "sql_agent",
            "args": {"task": "yearly_growth", "sql_ops": ["yearly_counts", "yoy_growth"]},
            "status": "continue",
        }
    if re.search(r"学术发展历程|发展历程|核心作者团队|代表性机构", q):
        return {
            "thought": "全刊发展概览",
            "action": "sql_agent",
            "args": {"task": "journal_overview", "sql_ops": ["journal_overview"]},
            "status": "continue",
        }
    if re.search(r"合作.*(作者|机构)|最紧密", q):
        kw = "水稻" if "水稻" in q else (entities.get("keywords") or ["水稻"])[0]
        return {
            "thought": "关键词合作作者与机构",
            "action": "sql_agent",
            "args": {
                "task": "keyword_collab",
                "sql_ops": ["authors_by_keyword", "institutions_by_keyword"],
                "keywords": [kw],
            },
            "status": "continue",
        }
    if re.search(r"三个研究方向|发表最多的.*方向", q):
        return {
            "thought": "近区间热门方向及代表论文",
            "action": "sql_agent",
            "args": {
                "task": "top_directions_with_papers",
                "sql_ops": ["top_keywords", "papers_by_top_keywords"],
                "top_n_directions": 3,
            },
            "status": "continue",
        }
    if re.search(r"研究团队|影响力最高", q):
        return {
            "thought": "高产作者/机构与阶段热词",
            "action": "sql_agent",
            "args": {
                "task": "top_teams",
                "sql_ops": [
                    "top_authors",
                    "top_institutions",
                    "keywords_by_periods",
                    "author_keywords_sample",
                ],
            },
            "status": "continue",
        }
    if re.search(r"人工智能|机器学习|深度学习", q) and re.search(
        r"主题|演变|发展|变化", q
    ):
        return {
            "thought": "专题关键词统计后再语义补充",
            "action": "sql_agent",
            "args": {
                "task": "topic_evolution",
                "sql_ops": ["topic_keyword_counts", "topic_yearly"],
                "keywords": AI_TOPIC_KWS,
            },
            "status": "continue",
        }
    if re.search(r"(研究)?主题.*(发展|演变|变化)|(发展|演变).*主题", q):
        kws = list(entities.get("keywords") or [])
        for term in ("水稻", "番茄", "镉", "产量"):
            if term in q and term not in kws:
                kws.insert(0, term)
        if not kws:
            kws = ["水稻"]
        return {
            "thought": f"专题「{kws[0]}」词频与逐年变化，再语义补充",
            "action": "sql_agent",
            "args": {
                "task": "topic_evolution",
                "sql_ops": ["topic_keyword_counts", "topic_yearly"],
                "keywords": kws[:6],
            },
            "status": "continue",
        }
    return {}


def react_controller_node(state: JournalState) -> Dict[str, Any]:
    """Run controlled ReAct loop; write evidence + trace into state."""
    chat = MiniMaxChat(get_settings())
    question = state.get("question") or ""
    entities = dict(state.get("entities") or {})
    working: JournalState = dict(state)  # type: ignore
    bundle: List[Dict[str, Any]] = list(state.get("evidence_bundle") or [])
    trace: List[Dict[str, Any]] = list(state.get("react_trace") or [])
    errors = list(state.get("errors") or [])
    seen_fps = set()
    goal = state.get("goal") or question
    working["goal"] = goal

    # seed year window into entities
    y0, y1 = _years(question, entities)
    if y0 is not None:
        entities["year_start"] = y0
    if y1 is not None:
        entities["year_end"] = y1
    working["entities"] = entities

    for step in range(1, MAX_STEPS + 1):
        decision: Dict[str, Any] = {}
        if step == 1 and not bundle:
            decision = _heuristic_first_action(question, entities)
        if not decision:
            try:
                decision = _decide(working, chat)
            except Exception as e:
                errors.append(f"react_decide: {e}")
                decision = {"action": "finish", "thought": f"决策失败: {e}", "status": "done"}

        action = (decision.get("action") or "finish").strip()
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        thought = decision.get("thought") or ""

        if action not in {"sql_agent", "kg_agent", "rag_agent", "finish"}:
            action = "finish"

        fp = _fingerprint(action, args)
        if action != "finish" and fp in seen_fps:
            trace.append(
                {
                    "step": step,
                    "thought": thought,
                    "action": action,
                    "args": args,
                    "observation": "重复调用，强制 finish",
                }
            )
            break
        seen_fps.add(fp)

        if action == "finish" or decision.get("status") == "done":
            trace.append(
                {
                    "step": step,
                    "thought": thought,
                    "action": "finish",
                    "args": args,
                    "observation": args.get("reason") or "完成",
                }
            )
            break

        try:
            updates, summary, _data = _run_tool(action, args, working)
            working.update(updates)
            if updates.get("entities"):
                entities = updates["entities"]
                working["entities"] = entities
            bundle.append(
                {
                    "step": len(bundle) + 1,
                    "source": action.replace("_agent", ""),
                    "operation": args.get("operation")
                    or args.get("task")
                    or ",".join(args.get("sql_ops") or args.get("queries") or [])
                    or action,
                    "summary": summary,
                }
            )
            working["evidence_bundle"] = bundle
            trace.append(
                {
                    "step": step,
                    "thought": thought,
                    "action": action,
                    "args": args,
                    "observation": summary[:500],
                }
            )
        except Exception as e:
            errors.append(f"react_tool:{action}: {e}")
            trace.append(
                {
                    "step": step,
                    "thought": thought,
                    "action": action,
                    "args": args,
                    "observation": f"ERROR: {e}",
                }
            )
            # try finish next
            continue

        # auto-finish heuristics: overview or yearly done alone may be enough for some Qs
        if step >= 2 and action == "rag_agent":
            # after sql+rag often enough
            pass
        if (
            step >= 1
            and action == "sql_agent"
            and (args.get("task") == "journal_overview" or "journal_overview" in (args.get("sql_ops") or []))
        ):
            # one-shot overview sufficient
            trace.append(
                {
                    "step": step + 0.1,
                    "thought": "概览数据已齐",
                    "action": "finish",
                    "args": {},
                    "observation": "auto-finish after journal_overview",
                }
            )
            break
        if (
            step >= 1
            and action == "sql_agent"
            and args.get("task") == "yearly_growth"
        ):
            trace.append(
                {
                    "step": step + 0.1,
                    "thought": "增速统计已齐",
                    "action": "finish",
                    "args": {},
                    "observation": "auto-finish after yearly_growth",
                }
            )
            break
        # keyword collab: keyword neighborhood + top-author ego, then finish
        if (
            step == 1
            and action == "sql_agent"
            and args.get("task") == "keyword_collab"
        ):
            kws = (
                args.get("keywords")
                or entities.get("keywords")
                or ["水稻"]
            )
            try:
                updates, summary, kg_data = _run_tool(
                    "kg_agent",
                    {
                        "operation": "keyword_ego",
                        "keywords": kws,
                    },
                    working,
                )
                working.update(updates)
                bundle.append(
                    {
                        "step": len(bundle) + 1,
                        "source": "kg",
                        "operation": "keyword_ego",
                        "summary": summary,
                    }
                )
                trace.append(
                    {
                        "step": step + 1,
                        "thought": "补充关键词图谱邻域",
                        "action": "kg_agent",
                        "args": {"operation": "keyword_ego"},
                        "observation": summary[:500],
                    }
                )
                top_author = None
                sql_ev = working.get("sql_evidence") or {}
                for a in (sql_ev.get("authors") or [])[:1]:
                    if isinstance(a, dict) and (a.get("name_zh") or a.get("name")):
                        top_author = a.get("name_zh") or a.get("name")
                        break
                if not top_author:
                    for a in (kg_data or {}).get("neighborhood_authors") or (
                        kg_data or {}
                    ).get("authors") or []:
                        if isinstance(a, dict) and (a.get("name_zh") or a.get("name")):
                            top_author = a.get("name_zh") or a.get("name")
                            break
                if top_author:
                    updates2, summary2, _ = _run_tool(
                        "kg_agent",
                        {
                            "operation": "author_ego",
                            "author_name": top_author,
                            "keywords": kws,
                        },
                        working,
                    )
                    working.update(updates2)
                    bundle.append(
                        {
                            "step": len(bundle) + 1,
                            "source": "kg",
                            "operation": "author_ego",
                            "summary": summary2,
                        }
                    )
                    trace.append(
                        {
                            "step": step + 2,
                            "thought": f"补充核心作者邻域：{top_author}",
                            "action": "kg_agent",
                            "args": {
                                "operation": "author_ego",
                                "author_name": top_author,
                            },
                            "observation": summary2[:500],
                        }
                    )
            except Exception as e:
                errors.append(f"react_follow_kg: {e}")
            break
        # topic evolution: sql then rag
        if (
            step == 1
            and action == "sql_agent"
            and args.get("task") == "topic_evolution"
        ):
            follow_q = args.get("keywords") or entities.get("keywords") or AI_TOPIC_KWS[:6]
            if isinstance(follow_q, str):
                follow_q = [follow_q]
            try:
                updates, summary, _ = _run_tool(
                    "rag_agent",
                    {
                        "queries": list(follow_q)[:6],
                        "year_start": working.get("entities", {}).get("year_start")
                        or (working.get("query_plan") or {}).get("year_start")
                        or (working.get("sql_evidence") or {}).get("start_year"),
                        "year_end": working.get("entities", {}).get("year_end")
                        or (working.get("query_plan") or {}).get("year_end")
                        or (working.get("sql_evidence") or {}).get("end_year"),
                    },
                    working,
                )
                working.update(updates)
                bundle.append(
                    {
                        "step": len(bundle) + 1,
                        "source": "rag",
                        "operation": "semantic_search",
                        "summary": summary,
                    }
                )
                trace.append(
                    {
                        "step": step + 1,
                        "thought": "语义补充专题相关论文",
                        "action": "rag_agent",
                        "args": {"queries": list(follow_q)[:6]},
                        "observation": summary[:500],
                    }
                )
            except Exception as e:
                errors.append(f"react_follow_rag: {e}")
            break
        # directions: sql then optional rag
        if (
            step == 1
            and action == "sql_agent"
            and args.get("task") == "top_directions_with_papers"
        ):
            dirs = (working.get("sql_evidence") or {}).get("directions") or []
            qlist = [str(d.get("keyword")) for d in dirs[:3] if d.get("keyword")]
            if qlist:
                try:
                    updates, summary, _ = _run_tool(
                        "rag_agent", {"queries": qlist}, working
                    )
                    working.update(updates)
                    bundle.append(
                        {
                            "step": len(bundle) + 1,
                            "source": "rag",
                            "operation": "semantic_search",
                            "summary": summary,
                        }
                    )
                    trace.append(
                        {
                            "step": step + 1,
                            "thought": "按方向补充语义文献",
                            "action": "rag_agent",
                            "args": {"queries": qlist},
                            "observation": summary[:500],
                        }
                    )
                except Exception as e:
                    errors.append(f"react_follow_dir_rag: {e}")
            break
        if step == 1 and action == "sql_agent" and args.get("task") == "top_teams":
            break

    # ensure intents for merge
    intents = list(working.get("intents") or [])
    if working.get("sql_evidence") and "sql" not in intents:
        intents.append("sql")
    if working.get("kg_evidence") and "kg" not in intents:
        intents.append("kg")
    if working.get("rag_evidence") and "rag" not in intents:
        intents.append("rag")
    if not intents:
        intents = ["sql"]

    # set query_plan task for templates when overview
    plan = dict(working.get("query_plan") or {})
    sql = working.get("sql_evidence") or {}
    if sql.get("scope") == "journal_overview":
        plan["task"] = "journal_overview"
        plan["sources"] = intents
    elif sql.get("task") and not plan.get("task"):
        plan["task"] = sql.get("task")
    plan["sources"] = intents

    out: Dict[str, Any] = {
        "sql_evidence": working.get("sql_evidence") or state.get("sql_evidence"),
        "kg_evidence": working.get("kg_evidence") or state.get("kg_evidence"),
        "rag_evidence": working.get("rag_evidence") or state.get("rag_evidence"),
        "entities": working.get("entities") or entities,
        "evidence_bundle": bundle,
        "react_trace": trace,
        "intents": intents,
        "query_plan": plan,
        "goal": goal,
        "stage": "retrieving",
        "route_reason": (state.get("route_reason") or "") + f"; ReAct步数={len(trace)}",
    }
    if errors:
        out["errors"] = errors
    return out
