"""Analysis Planner: decompose questions into analysis subgoals (not raw query ops).

Runs after the complexity router. Query tools (sql/kg/rag) are chosen later to
satisfy these analysis needs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.agents.state import JournalState
from app.agents.understand import _looks_like_person_name

# First-class analysis intents (answer-side), distinct from sql_ops / rag_queries.
ANALYSIS_TYPES = (
    "inventory",  # 列举/统计
    "trend",  # 趋势/阶段
    "compare",  # 对比
    "explain",  # 解释原因/机制
    "profile",  # 作者/团队画像
    "network",  # 合作网络
    "coverage",  # 专题覆盖/适投
    "recommend",  # 推荐/策划
    "forecast",  # 前瞻/预测
    "synthesize",  # 综合结论
)


def _subgoal(
    atype: str,
    desc: str,
    *,
    needs: Optional[List[str]] = None,
    query_hint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "type": atype,
        "desc": desc,
        "needs": needs or ["sql"],
        "query_hint": query_hint or {},
        "status": "pending",
    }


def _answer_shape(subgoals: List[Dict[str, Any]]) -> str:
    types = [s["type"] for s in subgoals]
    if "compare" in types:
        return "compare"
    if "recommend" in types or "coverage" in types:
        return "recommend"
    if "forecast" in types and "inventory" in types:
        return "overview_forecast"
    if "network" in types:
        return "network"
    if "profile" in types and "trend" in types:
        return "trajectory"
    if "trend" in types and "explain" in types:
        return "trend_explain"
    if "forecast" in types:
        return "overview_forecast"
    if "inventory" in types and len(types) == 1:
        return "inventory"
    return "synthesize"


def _goal_text(question: str, subgoals: List[Dict[str, Any]], shape: str) -> str:
    lines = [
        f"分析目标形态={shape}",
        "须依次完成以下分析子任务（先分析意图，再取证）：",
    ]
    for i, s in enumerate(subgoals, 1):
        needs = ",".join(s.get("needs") or [])
        lines.append(f"{i}. [{s['type']}] {s['desc']}（证据需求: {needs}）")
    lines.append(f"原问题: {question}")
    return "\n".join(lines)


def plan_analysis(
    question: str,
    *,
    entities: Optional[Dict[str, Any]] = None,
    route: Optional[Dict[str, Any]] = None,
    query_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rule-based analysis decomposition."""
    q = question or ""
    entities = entities or {}
    route = route or {}
    query_plan = query_plan or {}
    task = query_plan.get("task") or ""
    complex_feats = list(route.get("complex_features") or [])
    complexity = route.get("complexity") or "simple"

    subgoals: List[Dict[str, Any]] = []
    author_raw = entities.get("author_name") or query_plan.get("author_name")
    valid_author = bool(author_raw) and _looks_like_person_name(str(author_raw))

    # Journal-level / domain analysis MUST win over a polluted author_name
    # (e.g. entity extract mistaking「团队」「水稻领域」为人名).
    if re.search(
        r"发展历程|学术发展|研究方向演变|核心作者团队|代表性机构|值得关注的研究方向",
        q,
    ) and not (
        valid_author and re.search(r"研究轨迹|首次发表|合作作者变化", q)
    ):
        subgoals = [
            _subgoal(
                "inventory",
                "汇总阶段发文规模、热词、核心作者与机构",
                needs=["sql"],
                query_hint={"task": "journal_overview", "sql_ops": ["journal_overview"]},
            ),
            _subgoal("trend", "识别主要研究方向演变与阶段性热点", needs=["sql"]),
            _subgoal("profile", "概括核心作者团队与代表性机构格局", needs=["sql"]),
            _subgoal("forecast", "基于近期热词提出值得关注的方向", needs=["sql"]),
            _subgoal("synthesize", "形成期刊学术发展总览结论", needs=["sql"]),
        ]
    elif (
        re.search(r"合作.*(作者|机构)|作者和机构|最紧密", q)
        and not (valid_author and re.search(r"研究轨迹|合作作者变化", q))
    ):
        kw = None
        for term in ("水稻", "番茄", "镉"):
            if term in q:
                kw = term
                break
        kws = list(entities.get("keywords") or ([] if not kw else [kw]))
        if kw and kw not in kws:
            kws.insert(0, kw)
        if not kws:
            kws = ["水稻"]
        subgoals = [
            _subgoal(
                "inventory",
                f"统计关键词「{kws[0]}」相关高产作者与机构",
                needs=["sql", "kg"],
                query_hint={
                    "task": "keyword_collab",
                    "sql_ops": ["authors_by_keyword", "institutions_by_keyword"],
                    "keywords": kws[:3],
                },
            ),
            _subgoal("network", "分析合作网络结构与核心节点", needs=["sql", "kg"]),
            _subgoal("synthesize", "归纳合作网络特点", needs=["sql", "kg"]),
        ]

    # --- Prefer task-aware plans from simple router ---
    if not subgoals:
      if task == "unsupported_citations":
        subgoals = [
            _subgoal(
                "inventory",
                "说明本库无被引字段，无法完成高被引排名",
                needs=["sql"],
                query_hint={"task": "unsupported_citations"},
            )
        ]
      elif task == "yearly_growth":
        subgoals = [
            _subgoal(
                "inventory",
                "列出逐年发文量与同比增速",
                needs=["sql"],
                query_hint={"task": "yearly_growth", "sql_ops": ["yearly_counts", "yoy_growth"]},
            ),
            _subgoal("trend", "识别快速增长期与下降期", needs=["sql"]),
            _subgoal("explain", "结合发文波动说明可能原因（勿编造库外事实）", needs=["sql"]),
        ]
      elif task == "hotspot_compare":
        subgoals = [
            _subgoal(
                "inventory",
                "分别取出前后时间窗热词与发文规模",
                needs=["sql"],
                query_hint={"task": "hotspot_compare"},
            ),
            _subgoal("compare", "对比两窗热点异同与升降主题", needs=["sql"]),
            _subgoal("synthesize", "归纳研究热点演变结论", needs=["sql"]),
        ]
      elif task == "top_institutions":
        subgoals = [
            _subgoal(
                "inventory",
                "统计发文量靠前的机构名单",
                needs=["sql"],
                query_hint={"task": "top_institutions"},
            ),
            _subgoal("synthesize", "概括机构格局（如高校学院集中度）", needs=["sql"]),
        ]
      elif task == "topic_coverage":
        subgoals = [
            _subgoal(
                "coverage",
                "统计专题关键词命中与代表论文",
                needs=["sql"],
                query_hint={"task": "topic_coverage"},
            ),
            _subgoal("recommend", "据此判断投稿适合度并仅推荐证据内论文", needs=["sql"]),
        ]
      elif (task == "author_profile" and valid_author) or (
        valid_author
        and re.search(r"轨迹|首次发表|主题变化|合作作者变化|合作者变化", q)
      ):
        name = str(author_raw)
        subgoals = [
            _subgoal(
                "inventory",
                f"列出 {name} 首次/最近发文与全部论文",
                needs=["sql"],
                query_hint={"task": "author_profile", "author_name": name},
            ),
            _subgoal("trend", f"归纳 {name} 研究主题随时间变化", needs=["sql"]),
            _subgoal("network", f"梳理 {name} 主要合作者变化", needs=["sql"]),
            _subgoal("profile", f"形成 {name} 研究轨迹画像", needs=["sql"]),
        ]
      elif task == "coauthored_papers":
        subgoals = [
            _subgoal(
                "inventory",
                "列出两人合著论文清单",
                needs=["sql"],
                query_hint={"task": "coauthored_papers"},
            )
        ]
      elif task == "keyword_authors":
        subgoals = [
            _subgoal(
                "inventory",
                "按关键词列出相关作者与论文",
                needs=["sql"],
                query_hint={"task": "keyword_authors"},
            )
        ]

    # --- Complex / open analysis ---
    if not subgoals:
        if "journal_overview_multi" in complex_feats:
            subgoals = [
                _subgoal(
                    "inventory",
                    "汇总阶段发文规模、热词、核心作者与机构",
                    needs=["sql"],
                    query_hint={"task": "journal_overview", "sql_ops": ["journal_overview"]},
                ),
                _subgoal("trend", "识别主要研究方向演变与阶段性热点", needs=["sql"]),
                _subgoal("profile", "概括核心作者团队与代表性机构格局", needs=["sql"]),
                _subgoal("forecast", "基于近期热词提出值得关注的方向", needs=["sql"]),
                _subgoal("synthesize", "形成期刊学术发展总览结论", needs=["sql"]),
            ]
        elif "directions_then_papers" in complex_feats or re.search(
            r"三个研究方向|发表最多的.*方向|(主要|核心)?研究方向(有哪些|是什么|分析)|"
            r"(本刊|该刊).{0,8}研究方向",
            q,
        ):
            needs = ["sql", "rag"] if re.search(r"三个|发表最多|成果", q) else ["sql"]
            subgoals = [
                _subgoal(
                    "inventory",
                    "统计本刊主要研究方向（热门关键词）及代表论文",
                    needs=needs,
                    query_hint={
                        "task": "top_directions_with_papers",
                        "sql_ops": ["top_keywords", "papers_by_top_keywords"],
                    },
                ),
                _subgoal(
                    "synthesize",
                    "归纳主要研究方向格局（勿将办刊通告/影响因子当作方向）",
                    needs=needs,
                ),
            ]
        elif "teams_and_evolution" in complex_feats or (
            re.search(r"研究团队|影响力最高", q) and re.search(r"方向|变化|演变", q)
        ):
            subgoals = [
                _subgoal(
                    "inventory",
                    "统计高产作者/机构与阶段热词",
                    needs=["sql"],
                    query_hint={"task": "top_teams"},
                ),
                _subgoal("profile", "识别影响力较高的研究团队", needs=["sql"]),
                _subgoal("trend", "总结团队研究方向变化", needs=["sql"]),
            ]
        elif "collab_multi_source" in complex_feats:
            kw = "水稻" if "水稻" in q else (entities.get("keywords") or ["水稻"])[0]
            subgoals = [
                _subgoal(
                    "inventory",
                    f"统计关键词「{kw}」相关高产作者与机构",
                    needs=["sql", "kg"],
                    query_hint={
                        "task": "keyword_collab",
                        "sql_ops": ["authors_by_keyword", "institutions_by_keyword"],
                        "keywords": [kw],
                    },
                ),
                _subgoal("network", "分析合作网络结构与核心节点", needs=["sql", "kg"]),
                _subgoal("synthesize", "归纳合作网络特点", needs=["sql", "kg"]),
            ]
        elif "topic_evolution_multi" in complex_feats or re.search(
            r"(研究)?主题.*(发展|演变|变化)|(发展|演变).*主题", q
        ):
            kws = list(entities.get("keywords") or [])
            for term in ("水稻", "番茄", "镉", "产量", "人工智能"):
                if term in q and term not in kws:
                    kws.insert(0, term)
            if not kws:
                kws = ["水稻"]
            subgoals = [
                _subgoal(
                    "inventory",
                    f"统计专题「{kws[0]}」词频与逐年分布",
                    needs=["sql", "rag"],
                    query_hint={
                        "task": "topic_evolution",
                        "sql_ops": ["topic_keyword_counts", "topic_yearly"],
                        "keywords": kws[:6],
                    },
                ),
                _subgoal("trend", "归纳研究主题发展阶段与跃迁", needs=["sql", "rag"]),
                _subgoal("synthesize", "总结主题演变结论", needs=["sql", "rag"]),
            ]
        elif re.search(r"适合投稿|投稿前调研|潜在投稿", q):
            subgoals = [
                _subgoal("inventory", "梳理近区间热门方向与代表方法", needs=["sql", "rag"]),
                _subgoal("trend", "观察近期热门与方法偏好", needs=["sql", "rag"]),
                _subgoal("recommend", "给出投稿机会与注意事项", needs=["sql", "rag"]),
            ]
        elif re.search(r"专题|策划|推荐.*方向|新兴研究方向|未来.*热点", q):
            subgoals = [
                _subgoal("inventory", "盘点历史相关主题与代表论文", needs=["sql", "rag"]),
                _subgoal("trend", "识别上升中的交叉主题", needs=["sql", "rag"]),
                _subgoal("recommend", "推荐可策划/可关注的方向", needs=["rag", "sql"]),
                _subgoal("forecast", "说明为何可能形成热点", needs=["sql", "rag"]),
            ]
        elif re.search(r"国际合作|国家.*合作", q):
            subgoals = [
                _subgoal(
                    "inventory",
                    "从机构/合作网络侧取证（注明若无国家字段）",
                    needs=["sql", "kg"],
                ),
                _subgoal("network", "描述跨境/跨机构合作格局（证据范围内）", needs=["sql", "kg"]),
                _subgoal("explain", "说明数据局限（如无国家维度）", needs=["sql"]),
            ]
        elif re.search(r"对比|比较|变化", q) and re.search(r"热点|近.*年|前.*年", q):
            subgoals = [
                _subgoal(
                    "inventory",
                    "取出对比窗热词统计",
                    needs=["sql"],
                    query_hint={"task": "hotspot_compare"},
                ),
                _subgoal("compare", "对比两窗差异", needs=["sql"]),
                _subgoal("synthesize", "形成热点变化结论", needs=["sql"]),
            ]
        elif re.search(r"识别|预测|演变|并分析|同时", q) or complexity == "complex":
            subgoals = [
                _subgoal("inventory", "先收集与问题直接相关的结构化事实", needs=["sql"]),
                _subgoal("trend", "如涉及时间则分析趋势/阶段", needs=["sql"]),
                _subgoal("explain", "在证据范围内解释格局或原因", needs=["sql", "rag"]),
                _subgoal("synthesize", "给出综合分析结论", needs=["sql", "rag"]),
            ]
        else:
            # pure rag / generic
            subgoals = [
                _subgoal(
                    "inventory",
                    "检索相关文献要点",
                    needs=["rag"],
                    query_hint={"task": "generic", "sources": ["rag"]},
                ),
                _subgoal("synthesize", "基于文献归纳回答", needs=["rag"]),
            ]

    # Deduplicate by type+desc
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for s in subgoals:
        key = (s["type"], s["desc"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)

    shape = _answer_shape(uniq)
    goal = _goal_text(q, uniq, shape)

    # Seed query hints from first subgoal that has them
    seed_hint: Dict[str, Any] = {}
    for s in uniq:
        if s.get("query_hint"):
            seed_hint = dict(s["query_hint"])
            break

    return {
        "subgoals": uniq,
        "answer_shape": shape,
        "goal": goal,
        "complexity": complexity,
        "seed_query_hint": seed_hint,
        "source": "analysis_planner_v1",
    }


def analysis_planner_node(state: JournalState) -> Dict[str, Any]:
    question = state.get("question") or ""
    entities = dict(state.get("entities") or {})
    route = dict(state.get("route") or {})
    query_plan = dict(state.get("query_plan") or {})

    analysis = plan_analysis(
        question,
        entities=entities,
        route=route,
        query_plan=query_plan,
    )

    # Enrich query_plan.focus / seed ops for downstream without wiping router plans
    plan = dict(query_plan)
    plan["analysis_shape"] = analysis["answer_shape"]
    plan["focus"] = analysis["goal"]
    hint = analysis.get("seed_query_hint") or {}
    if hint.get("task"):
        # Analysis seed wins when it corrects a mis-routed author_profile etc.
        plan["task"] = hint["task"]
    if hint.get("sql_ops"):
        plan["sql_ops"] = hint["sql_ops"]
    if hint.get("keywords"):
        plan["keywords"] = hint["keywords"]
        entities["keywords"] = hint["keywords"]
    if hint.get("author_name"):
        plan["author_name"] = hint["author_name"]
    elif hint.get("task") in {
        "journal_overview",
        "keyword_collab",
        "hotspot_compare",
        "top_institutions",
        "yearly_growth",
        "topic_evolution",
        "top_teams",
        "top_directions_with_papers",
    }:
        plan.pop("author_name", None)
        if not _looks_like_person_name(str(entities.get("author_name") or "")):
            entities.pop("author_name", None)

    # Align route complexity with analysis needs when multi-source
    needs_all = set()
    for s in analysis.get("subgoals") or []:
        needs_all.update(s.get("needs") or [])
    if len(needs_all) > 1 and route.get("complexity") == "simple":
        # Keep simple if template-covered SQL-only tasks; else escalate soft
        if not (len(needs_all) == 1 and "sql" in needs_all):
            route = dict(route)
            route["complexity"] = "complex"
            route["reason"] = (route.get("reason") or "") + "; 分析规划需多源取证"

    reason = state.get("route_reason") or ""
    n = len(analysis.get("subgoals") or [])
    reason = f"{reason}; 分析规划:{analysis['answer_shape']}×{n}".strip("; ")

    return {
        "analysis_plan": analysis,
        "goal": analysis["goal"],
        "query_plan": plan,
        "entities": entities,
        "route": route,
        "route_reason": reason,
        "stage": "analysis_planned",
    }


def format_analysis_plan_for_prompt(analysis: Dict[str, Any]) -> str:
    if not analysis:
        return ""
    lines = [
        f"[分析规划] shape={analysis.get('answer_shape')}",
        "子任务:",
    ]
    for i, s in enumerate(analysis.get("subgoals") or [], 1):
        lines.append(
            f"{i}. [{s.get('type')}] {s.get('desc')} "
            f"(needs={','.join(s.get('needs') or [])}, status={s.get('status')})"
        )
    return "\n".join(lines)


__all__ = [
    "ANALYSIS_TYPES",
    "plan_analysis",
    "analysis_planner_node",
    "format_analysis_plan_for_prompt",
]
