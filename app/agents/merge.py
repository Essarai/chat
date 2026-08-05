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

    if data.get("scope") == "author":
        a = data.get("author") or {}
        lines = [
            f"[SQL]【作者个人统计，非全刊】作者: {a.get('name_zh') or data.get('author_name')} "
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
    parts: List[str] = []
    citations: List[Dict[str, Any]] = []
    seen_doi = set()

    if "sql" in intents:
        parts.append(_format_sql(state.get("sql_evidence") or {}))
    if "kg" in intents:
        parts.append(_format_kg(state.get("kg_evidence") or {}))
    if "rag" in intents:
        rag = state.get("rag_evidence") or {}
        parts.append(_format_rag(rag))
        for c in rag.get("citations") or []:
            doi = c.get("doi")
            if doi and doi not in seen_doi:
                seen_doi.add(doi)
                citations.append(c)

    # if kg empty and rag not selected, keep citations empty
    evidence_text = "\n\n".join(p for p in parts if p)
    return {"evidence_text": evidence_text, "citations": citations}
