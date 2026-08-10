from __future__ import annotations

from typing import Any, Dict, List

from app.agents.state import JournalState
from app.utils import doi_url


def _format_sql(data: Dict[str, Any]) -> str:
    if not data or data.get("error"):
        return f"SQL证据不可用: {data.get('error')}" if data else ""

    yearly = ", ".join(
        f"{r['year']}:{r['paper_count']}" for r in data.get("yearly") or []
    )
    kws = ", ".join(
        f"{r['keyword']}({r['paper_count']})" for r in (data.get("keywords") or [])[:15]
    )
    funds = ", ".join(
        f"{r['agency_norm']}({r['paper_count']})" for r in (data.get("funds") or [])[:10]
    )
    task = data.get("task") or data.get("scope")

    if task == "yearly_growth" or data.get("yoy"):
        y0, y1 = data.get("start_year"), data.get("end_year")
        lines = [
            f"[SQL]【逐年发文与增速】区间: {y0}-{y1}",
            "逐年发文量（含同比）:",
        ]
        for r in data.get("yoy") or data.get("yearly") or []:
            delta = r.get("delta")
            pct = r.get("yoy_pct")
            if delta is None:
                lines.append(f"- {r.get('year')}: {r.get('paper_count')} 篇")
            else:
                pct_s = f"{pct}%" if pct is not None else "—"
                lines.append(
                    f"- {r.get('year')}: {r.get('paper_count')} 篇"
                    f"（Δ{delta:+d}，同比 {pct_s}）"
                )
        fg = data.get("fastest_growth")
        if fg:
            lines.append(
                f"预计算·增长最快年份: {fg.get('year')} "
                f"（Δ{fg.get('delta'):+d}，同比 {fg.get('yoy_pct')}%）"
            )
        lines.append(
            "回答须列出主要年份数量，识别快速增长期与下降期，"
            "并明确指出增长最快的年份；若末年发文量异常偏低，提示可能为未完年。"
        )
        if data.get("keywords"):
            lines.append(
                "热门关键词: "
                + ", ".join(
                    f"{r['keyword']}({r['paper_count']})"
                    for r in (data.get("keywords") or [])[:15]
                )
            )
            lines.append(
                "若问题同时问热门关键词，须按上表学科主题词作答；"
                "严禁用办刊通告、获奖、影响因子、引证报告充当趋势或热词。"
            )
        return "\n".join(lines)

    if data.get("scope") == "unsupported" or task == "unsupported_citations":
        return (
            "[SQL]【能力不足·拒答】"
            + (data.get("reason") or "本库无被引字段，无法按引用次数排名。")
        )

    if data.get("scope") == "hotspot_compare" or task == "hotspot_compare":
        lines = ["[SQL]【两窗研究热点对比】"]
        for p in data.get("periods") or []:
            pk = ", ".join(
                f"{r.get('keyword')}({r.get('paper_count')})"
                for r in (p.get("keywords") or [])[:12]
            )
            lines.append(
                f"阶段 {p.get('period')}（约 {p.get('paper_count')} 篇）热词: {pk or '无'}"
            )
        lines.append("请对比两窗热词异同，指出上升/回落主题；禁止编造未出现的关键词。")
        return "\n".join(lines)

    if data.get("scope") in {"topic_coverage", "submission_fit"} or task in {
        "topic_coverage",
        "submission_fit",
    }:
        y0, y1 = data.get("start_year"), data.get("end_year")
        label = "投稿适配" if (
            data.get("scope") == "submission_fit" or task == "submission_fit"
        ) else "专题关键词覆盖"
        lines = [
            f"[SQL]【{label}】区间: {y0}-{y1}",
            "查询词: " + ", ".join(data.get("keywords_queried") or []),
            f"合计命中(按词频累加，可重叠): {data.get('total_hits')}",
        ]
        if data.get("fit_label"):
            lines.append(f"适合度标签: {data.get('fit_label')}")
        cov = data.get("coverage_summary") or {}
        if cov:
            lines.append(
                f"覆盖摘要: hits≈{cov.get('hit_papers_est')}, "
                f"rising={cov.get('rising_recently')}"
            )
        topic = data.get("topic_keywords") or []
        if not topic or all(int(r.get("paper_count") or 0) == 0 for r in topic):
            lines.append(
                "专题相关关键词几乎无命中。回答须说明该主题在本刊覆盖偏薄，"
                "不宜以该主题作为主投稿方向；禁止用近义但未命中的词冒充。"
            )
        else:
            lines.append(
                "词频: "
                + ", ".join(
                    f"{r.get('keyword')}({r.get('paper_count')})" for r in topic[:15]
                )
            )
        papers = data.get("papers") or []
        if papers:
            lines.append(f"可推荐的相关论文（共 {len(papers)} 篇，仅可引用下列 DOI）:")
            for i, p in enumerate(papers[:12], 1):
                doi = p.get("doi") or ""
                url = doi_url(doi) or ""
                year = p.get("year")
                year_bit = f"（{year}）" if year else ""
                link = f" [查看全文]({url})" if url else ""
                doi_bit = f" DOI: {doi}" if doi else ""
                lines.append(
                    f"{i}. {p.get('title_zh') or p.get('title')}{year_bit}{doi_bit}{link}"
                )
        return "\n".join(lines)

    if data.get("scope") == "top_authors" or task == "top_authors":
        top_n = data.get("top_n") or len(data.get("authors") or [])
        lines = [
            f"[SQL]【高产作者】Top{top_n} 区间: "
            f"{data.get('start_year')}-{data.get('end_year')}",
        ]
        for i, a in enumerate((data.get("authors") or [])[:top_n], 1):
            lines.append(
                f"{i}. {a.get('name_zh') or a.get('name')}（{a.get('paper_count')}篇）"
            )
        return "\n".join(lines)

    if data.get("scope") == "institution_authors" or task == "institution_authors":
        inst = data.get("institution") or ""
        lines = [
            f"[SQL]【机构作者代表成果】机构匹配: {inst}",
            f"作者数={data.get('total_authors')} 关联发文={data.get('total_papers')}",
            "口径=作者署名单位含该机构的论文，不是以该机构为研究对象的文献。",
        ]
        for i, a in enumerate((data.get("authors") or [])[:10], 1):
            papers = a.get("papers") or []
            titles = "；".join(
                (p.get("title_zh") or "")[:40] for p in papers[:3]
            )
            lines.append(
                f"{i}. {a.get('name_zh')}（{a.get('paper_count')}篇）: {titles}"
            )
        return "\n".join(lines)

    if data.get("scope") == "top_institutions" or task == "top_institutions":
        y0, y1 = data.get("start_year"), data.get("end_year")
        top_n = data.get("top_n") or len(data.get("institutions") or [])
        lines = [
            f"[SQL]【机构发文排名 Top{top_n}】区间: {y0}-{y1}",
            "下列按发文量列出机构（回答必须据此完整列出，勿编造）:",
        ]
        for i, inst in enumerate((data.get("institutions") or [])[:top_n], 1):
            lines.append(
                f"{i}. {inst.get('institution')}（{inst.get('paper_count')}篇）"
            )
        return "\n".join(lines)

    if data.get("scope") == "topic_stats" or task == "topic_evolution":
        y0, y1 = data.get("start_year"), data.get("end_year")
        lines = [f"[SQL]【专题关键词统计】区间: {y0}-{y1}"]
        topic = data.get("topic_keywords") or []
        if not topic or all(int(r.get("paper_count") or 0) == 0 for r in topic):
            lines.append(
                "专题相关关键词在本刊几乎无命中（paper_count 均为 0 或缺失）。"
                "回答必须如实说明该主题文献稀少，禁止用全刊热词（如水稻）冒充该主题。"
            )
        else:
            lines.append(
                "专题词频: "
                + ", ".join(
                    f"{r.get('keyword')}({r.get('paper_count')})" for r in topic[:15]
                )
            )
            for term, series in (data.get("yearly_by_keyword") or {}).items():
                if not series:
                    continue
                lines.append(
                    f"{term} 逐年: "
                    + ", ".join(f"{r['year']}:{r['paper_count']}" for r in series)
                )
        return "\n".join(lines)

    if task == "keyword_collab" or data.get("scope") == "keyword_collab":
        kw = data.get("keyword") or ""
        lines = [
            f"[SQL]【关键词合作网络】关键词「{kw}」",
            f"相关论文总数: {data.get('total_papers')}；相关作者总数: {data.get('total_authors')}",
            "高产作者:",
        ]
        for i, a in enumerate((data.get("authors") or [])[:12], 1):
            name = a.get("name_zh") or a.get("name_en") or a.get("author_id")
            lines.append(f"{i}. {name}（{a.get('paper_count')}篇）")
        lines.append("主要机构:")
        for i, inst in enumerate((data.get("institutions") or [])[:12], 1):
            lines.append(
                f"{i}. {inst.get('institution')}（{inst.get('paper_count')}篇）"
            )
        return "\n".join(lines)

    if data.get("scope") == "top_directions" or task == "top_directions_with_papers":
        y0, y1 = data.get("start_year"), data.get("end_year")
        lines = [f"[SQL]【近区间热门研究方向】{y0}-{y1}"]
        for i, d in enumerate((data.get("directions") or [])[:5], 1):
            lines.append(
                f"{i}. 方向「{d.get('keyword')}」（{d.get('paper_count')}篇）代表论文:"
            )
            for p in d.get("papers") or []:
                doi = p.get("doi") or ""
                url = doi_url(doi) or ""
                year = p.get("year")
                year_bit = f"（{year}）" if year else ""
                link = f" [查看全文]({url})" if url else ""
                doi_bit = f" DOI: {doi}" if doi else ""
                lines.append(
                    f"   - {p.get('title_zh')}{year_bit}{doi_bit}{link}"
                )
        return "\n".join(lines)

    if task == "top_teams" or data.get("scope") == "top_teams":
        y0, y1 = data.get("start_year"), data.get("end_year")
        lines = [
            f"[SQL]【高影响力团队线索】区间: {y0}-{y1}",
            "高产作者: "
            + ", ".join(
                f"{r.get('name_zh')}({r.get('paper_count')})"
                for r in (data.get("authors") or [])[:12]
            ),
            "主要机构: "
            + ", ".join(
                f"{r.get('institution')}({r.get('paper_count')})"
                for r in (data.get("institutions") or [])[:12]
            ),
        ]
        for a in data.get("author_keywords") or []:
            ak = ", ".join(
                f"{r.get('keyword')}({r.get('paper_count')})"
                for r in (a.get("keywords") or [])[:6]
            )
            lines.append(
                f"作者 {a.get('name_zh')}（{a.get('paper_count')}篇）关键词: {ak or '无'}"
            )
        for p in data.get("periods") or []:
            pk = ", ".join(
                f"{r.get('keyword')}({r.get('paper_count')})"
                for r in (p.get("keywords") or [])[:8]
            )
            lines.append(
                f"阶段 {p.get('period')}（约 {p.get('paper_count')} 篇）热词: {pk or '无'}"
            )
        lines.append("请据此识别主要研究团队，并归纳方向变化；勿编造未出现的团队名。")
        return "\n".join(lines)

    if data.get("scope") == "journal_overview":
        y0, y1 = data.get("start_year"), data.get("end_year")
        lines = [
            f"[SQL]【全刊发展概览】区间: {y0}-{y1}",
            "按年发文量: "
            + ", ".join(
                f"{r['year']}:{r['paper_count']}" for r in (data.get("yearly") or [])[:25]
            ),
            "全区间热门关键词: "
            + ", ".join(
                f"{r['keyword']}({r['paper_count']})"
                for r in (data.get("keywords") or [])[:15]
            ),
            "核心作者(按发文量): "
            + ", ".join(
                f"{r['name_zh']}({r['paper_count']})"
                for r in (data.get("authors") or [])[:12]
            ),
            "代表性机构(按论文数): "
            + ", ".join(
                f"{r['institution']}({r['paper_count']})"
                for r in (data.get("institutions") or [])[:12]
            ),
        ]
        for p in data.get("periods") or []:
            kws = ", ".join(
                f"{r['keyword']}({r['paper_count']})"
                for r in (p.get("keywords") or [])[:8]
            )
            lines.append(
                f"阶段 {p.get('period')}（发文约 {p.get('paper_count')} 篇）热词: {kws or '无'}"
            )
        lines.append(
            "回答要求：严格基于以上统计归纳演变趋势、核心作者与机构；"
            "不要编造未出现的论文题名；可用有序列表；不要输出裸的 ** 标记。"
        )
        return "\n".join(lines)

    if data.get("scope") == "keyword_authors":
        kw = data.get("keyword") or ""
        authors = data.get("authors") or []
        labels = ", ".join(
            f"{r.get('keyword')}({r.get('paper_count')})"
            for r in (data.get("matched_labels") or [])[:8]
        )
        lines = [
            f"[SQL]【关键词作者检索】关键词含「{kw}」",
            f"匹配关键词标签: {labels or '无'}",
            f"相关论文总数: {data.get('total_papers')}；相关作者总数: {data.get('total_authors')}",
            f"下列按发文量列出前 {len(authors)} 位作者及其相关论文（回答请据此组织，不要再罗列一遍裸题名）：",
        ]
        for i, a in enumerate(authors, 1):
            name = a.get("name_zh") or a.get("name_en") or a.get("author_id")
            lines.append(f"{i}. **{name}**（{a.get('paper_count')}篇）")
            for p in a.get("papers") or []:
                doi = p.get("doi") or ""
                url = doi_url(doi) or ""
                year = p.get("year")
                year_bit = f"（{year}）" if year else ""
                link = f" [查看全文]({url})" if url else ""
                doi_bit = f" DOI: {doi}" if doi else ""
                lines.append(
                    f"   - {p.get('title_zh')}{year_bit}{doi_bit}{link}"
                )
        return "\n".join(lines)

    if data.get("scope") == "coauthored_papers" or data.get("task") == "coauthored_papers":
        a = data.get("author_name_a") or (data.get("author_a") or {}).get("name_zh")
        b = data.get("author_name_b") or (data.get("author_b") or {}).get("name_zh")
        papers = data.get("papers") or []
        lines = [
            f"[SQL]【合著论文】{a} × {b}（共 {data.get('total_papers', len(papers))} 篇）",
            "只回答两人共同署名论文；禁止输出各自全部发文或全部合作者名单。",
        ]
        for i, p in enumerate(papers, 1):
            doi = p.get("doi") or ""
            url = doi_url(doi) or ""
            year = p.get("year")
            year_bit = f"（{year}）" if year else ""
            link = f" [查看全文]({url})" if url else ""
            doi_bit = f" DOI: {doi}" if doi else ""
            lines.append(f"{i}. {p.get('title_zh')}{year_bit}{doi_bit}{link}")
        if not papers:
            lines.append("（无合著论文）")
        return "\n".join(lines)

    if data.get("scope") == "author":
        a = data.get("author") or {}
        name = a.get("name_zh") or data.get("author_name") or "（未知）"
        if not a or data.get("found") is False:
            return (
                f"[SQL]【作者个人统计】未找到作者「{name}」。"
                "本刊库无此人记录；禁止编造其论文/DOI/合作者；"
                "应明确回答：库中无发文记录。"
            )
        lines = [
            f"[SQL]【作者个人统计，非全刊】作者: {name} "
            f"(id={a.get('author_id')})",
            f"本刊发文总量: {data.get('total_papers')} 篇 "
            f"(年份跨度 {data.get('year_min')}-{data.get('year_max')})",
            f"按年发文量: {yearly or '无'}",
            f"该作者论文关键词频次: {kws or '无'}",
            f"该作者论文相关基金(按篇数): {funds or '无'}",
        ]
        papers = data.get("papers") or data.get("recent_papers") or []
        if papers:
            lines.append(
                f"该作者在本刊的全部论文共 {len(papers)} 篇（必须全部列出，保留序号 1..{len(papers)}；"
                "DOI 用纯文本；链接写成 Markdown：[查看全文](url)，禁止 HTML）："
            )
            for i, p in enumerate(papers, 1):
                doi = p.get("doi") or ""
                url = doi_url(doi) or ""
                lines.append(
                    f"{i}. {p.get('title_zh')}\n"
                    f"   年份: {p.get('year')}\n"
                    f"   DOI: {doi}\n"
                    f"   链接: [查看全文]({url})"
                )
        collab = data.get("collaborators") or {}
        inst = data.get("collaborator_institutions") or {}
        people = inst.get("collaborators") or collab.get("collaborators") or []
        if people:
            lines.append(
                f"主要合作者及所属机构（共 {len(people)} 人；"
                "回答必须用有序序号 1. 2. 3. 逐条列出，格式："
                "`1. **姓名**（N篇）：机构A / 机构B`，不要用无序号标题+子弹）："
            )
            for i, c in enumerate(people, 1):
                name = c.get("name_zh") or c.get("name_en") or c.get("author_id")
                co = c.get("co_papers")
                orgs = c.get("institutions") or []
                if isinstance(orgs, str):
                    org_text = orgs
                else:
                    org_text = " / ".join([o for o in orgs if o]) or "（无机构信息）"
                lines.append(f"{i}. **{name}**（{co}篇）：{org_text}")
        elif inst.get("by_institution"):
            rows = inst.get("by_institution") or []
            lines.append(
                f"合作者机构分布（共 {len(rows)} 个机构；回答必须用有序序号列出）："
            )
            for i, row in enumerate(rows[:20], 1):
                lines.append(
                    f"{i}. **{row.get('institution')}**（{row.get('count')}人）："
                    + "；".join(row.get("collaborators") or [])
                )
        return "\n".join(lines)

    lines = [
        f"[SQL]【全刊统计】区间: {data.get('start_year')}-{data.get('end_year')}",
        f"按年发文量: {yearly}",
        f"热门关键词: {kws}",
        f"主要基金: {funds}",
    ]
    return "\n".join(lines)


def _format_kg(data: Dict[str, Any]) -> str:
    if not data or data.get("error"):
        return f"KG证据不可用: {data.get('error')}" if data else ""
    if data.get("collaborators") is not None:
        author = data.get("author") or {}
        lines = [
            f"[KG] 作者: {author.get('name_zh')} ({author.get('author_id')}), "
            f"发文 {author.get('paper_count')} 来源={data.get('source')}"
        ]
        for c in data.get("collaborators") or []:
            lines.append(
                f"- 合作者 {c.get('name_zh')} / {c.get('name_en')} "
                f"共同论文 {c.get('co_papers')}"
            )
        if not data.get("collaborators"):
            lines.append("未找到合作者。")
        return "\n".join(lines)
    if data.get("paper"):
        p = data["paper"]
        url = p.get("url") or doi_url(p.get("doi"))
        return (
            f"[KG] 论文: {p.get('title')} ({p.get('doi')})\n"
            f"链接: {url}\n"
            f"作者: {', '.join(a.get('name') or '' for a in p.get('authors') or [])}\n"
            f"机构: {', '.join(p.get('institutions') or [])}\n"
            f"关键词: {', '.join(p.get('keywords') or [])}"
        )
    if data.get("keyword_papers"):
        lines = ["[KG] 相关论文:"]
        for p in data["keyword_papers"]:
            url = p.get("url") or doi_url(p.get("doi"))
            lines.append(
                f"- {p.get('year')} {p.get('title')} ({p.get('doi')}) {url or ''}".rstrip()
            )
        return "\n".join(lines)
    # keyword ego / network summary
    if data.get("query_keyword") or data.get("focus") == "keyword" or data.get("keyword"):
        kw = data.get("query_keyword") or (data.get("keyword") or {}).get("label") or ""
        nb = data.get("neighborhood") or {}
        lines = [
            f"[KG]【关键词邻域】「{kw}」（来源={data.get('source')}；"
            f"作者{nb.get('author_count', len(data.get('authors') or []))}、"
            f"机构{nb.get('institution_count', len(data.get('institutions') or []))}、"
            f"边{nb.get('edge_count', len(data.get('edges') or []))}）"
        ]
        authors = data.get("neighborhood_authors") or data.get("authors") or []
        if authors and isinstance(authors, list) and isinstance(authors[0], dict):
            lines.append("邻域作者（按相关发文）:")
            for a in authors[:12]:
                lines.append(
                    f"- {a.get('name_zh') or a.get('name')}（{a.get('paper_count')}篇）"
                )
        insts = data.get("neighborhood_institutions") or data.get("institutions") or []
        if insts and isinstance(insts, list) and isinstance(insts[0], dict):
            lines.append("邻域机构:")
            for i in insts[:12]:
                lines.append(
                    f"- {i.get('institution') or i.get('name')}（{i.get('paper_count')}篇）"
                )
        related = data.get("related_keywords") or []
        if related:
            lines.append(
                "共现关键词: "
                + ", ".join(
                    f"{r.get('label') or r.get('keyword')}({r.get('paper_count')})"
                    for r in related[:10]
                    if isinstance(r, dict)
                )
            )
        if data.get("collaborators"):
            lines.append("核心作者合作者（author ego）:")
            for c in (data.get("collaborators") or [])[:10]:
                lines.append(
                    f"- {c.get('name_zh')}（合著{c.get('co_papers')}篇）"
                )
        if data.get("neo4j_error"):
            lines.append(f"(Neo4j 回退: {data.get('neo4j_error')})")
        if len(lines) > 1:
            return "\n".join(lines)
    return "[KG] 未检索到图谱信息。"


def _format_rag(data: Dict[str, Any]) -> str:
    if not data or data.get("error"):
        return f"RAG证据不可用: {data.get('error')}" if data else ""
    blocks = ["[RAG] 语义检索结果:"]
    for i, h in enumerate(data.get("hits") or [], 1):
        text = (h.get("text") or "")[:1200]
        url = h.get("url") or doi_url(h.get("doi"))
        blocks.append(
            f"[{i}] 题名: {h.get('title')}\n"
            f"DOI: {h.get('doi')}\n"
            f"链接: {url}\n"
            f"年份: {h.get('year')}\n"
            f"作者: {h.get('authors')}\n"
            f"关键词: {h.get('keywords')}\n"
            f"内容:\n{text}"
        )
    return "\n\n".join(blocks)


def merge_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    plan = state.get("query_plan") or {}
    parts: List[str] = []
    citations: List[Dict[str, Any]] = []
    seen_doi = set()

    focus = plan.get("focus") or state.get("goal") or ""
    if focus:
        parts.append(f"[任务焦点] {focus}")

    analysis = state.get("analysis_plan") or {}
    if analysis.get("subgoals"):
        from app.agents.analysis_planner import format_analysis_plan_for_prompt

        parts.append(format_analysis_plan_for_prompt(analysis))

    route = state.get("route") or {}
    if route:
        parts.append(
            f"[路由] complexity={route.get('complexity')} "
            f"source={route.get('suggested_source')} escalated={route.get('escalated')}"
        )

    bundle = state.get("evidence_bundle") or []
    if bundle:
        parts.append("[检索步骤摘要]")
        for b in bundle:
            parts.append(
                f"- step{b.get('step')}: {b.get('source')}/{b.get('operation')} — "
                f"{(b.get('summary') or '')[:400]}"
            )

    # Include any source that has evidence (ReAct may fill without intents yet)
    sql = state.get("sql_evidence") or {}
    kg = state.get("kg_evidence") or {}
    rag = state.get("rag_evidence") or {}
    if "sql" in intents or sql:
        parts.append(_format_sql(sql))
    if "kg" in intents or kg:
        parts.append(_format_kg(kg))
    if "rag" in intents or rag:
        parts.append(_format_rag(rag))
        for c in rag.get("citations") or []:
            doi = c.get("doi")
            if doi and doi not in seen_doi:
                seen_doi.add(doi)
                citations.append(c)

    evidence_text = "\n\n".join(p for p in parts if p)
    return {"evidence_text": evidence_text, "citations": citations}
