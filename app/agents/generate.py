from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional

from app.agents.evidence_guard import (
    build_evidence_ledger,
    enforce_evidence_constraints,
)
from app.agents.state import JournalState
from app.config import get_settings
from app.services.minimax_chat import MiniMaxChat
from app.utils import doi_url

SYSTEM_PROMPT = """你是《浙江大学学报（农业与生命科学版）》的资深学术分析助手。
请严格基于多路证据（[SQL] / [KG] / [RAG]）与「证据约束」清单综合推理后作答。

写作目标（升维，不是数据报表）：
1. 先给洞察结论（趋势、格局、机制/网络含义），再给必要证据支撑；禁止逐年逐条流水账。
2. 把 SQL 统计、KG 邻域（作者/机构/共现词/合作）与 RAG 内容串成一条叙事，而不是分栏罗列。
3. 数字、论文题名、年份、DOI 只能来自证据约束清单；清单外一律不得出现。
4. 中文，结论优先；可用 Markdown（标题、加粗、少量表格）。名单用有序列表。
5. 禁止编造论文/DOI/链接/人数；禁止「参考文献」章节与 HTML。
6. 禁止提及路由、意图、Agent、SQL/KG/RAG 管道等内部实现信息。

证据硬约束（生成时自检）：
- 每个统计数字须能在 SQL/KG 证据中找到对应；
- 每篇论文引用须存在于允许 DOI/题名列表；
- 论文年份须落在允许时间窗内（若已知）；
- DOI 必须与证据完全匹配，不得改写或拼接。

按问题类型补充：
- 作者发文/合作者：有序列表；合作者格式 `1. **姓名**（N篇）：机构A / 机构B`。
- 主题演变/合作网络：突出阶段跃迁、核心节点与邻域结构，少堆原始计数。
- 链接：DOI 纯文本；全文用 `[查看全文](url)`。
"""


def _build_instruction(intents: List[str], evidence: str = "", task: str = "") -> str:
    has_rag = "rag" in intents
    only_struct = bool(intents) and not has_rag
    author_scoped = "【作者个人统计" in (evidence or "")
    insight = (
        "回答形态：洞察式综述（结论→机制/格局→关键证据），"
        "禁止写成「年份: N篇」逐行数据报告；"
        "必须吸收 [KG] 邻域信息（若有）解释合作/机构/共现结构；"
        "引用论文前核对证据约束中的 DOI/年份。"
    )
    if author_scoped:
        return (
            "本题为作者个人/合作者结构化统计。数字必须来自【作者个人统计】证据；"
            "列举论文或合作者时必须用有序序号 1. 2. 3.，每条一行；"
            "合作者格式：`1. **姓名**（N篇）：机构A / 机构B`；"
            "论文须全部列出；DOI 纯文本；链接用 [查看全文](url)；禁止 HTML。"
        )
    if task in {"topic_evolution", "keyword_collab", "journal_overview", "yearly_growth"}:
        return (
            f"本题任务={task}。{insight}"
            "凡必须点名的名单用有序序号；不要写参考文献；DOI 用纯文本。"
        )
    if only_struct:
        return (
            f"本题为结构化分析（SQL/KG）。{insight}"
            "凡名单/分布必须用有序序号 1. 2. 3.，每条一行；"
            "不要写参考文献；DOI 用纯文本。"
        )
    if has_rag and len(intents) == 1:
        return (
            "本题为文献内容问答。请基于 [RAG] 证据作答并提炼观点；"
            "文末可列相关论文（题名、DOI 纯文本、证据中的链接），不要空的参考文献，不要 HTML。"
            "DOI/年份必须落在证据约束清单内。"
        )
    return (
        f"本题可能同时涉及结构证据与文献。{insight}"
        "优先用 SQL/KG 回答关系/统计部分；仅当证据中确有相关论文时再附 DOI/链接；"
        "DOI 用纯文本；不要 HTML。"
    )


def _fix_heading_bold(text: str) -> str:
    """Strip broken/redundant ** inside ATX headings (#{1,6})."""

    def _repl(m: re.Match) -> str:
        hashes, body = m.group(1), m.group(2).strip()
        # ### **title** / ### **title  → ### title
        if body.startswith("**") and body.endswith("**") and len(body) > 4:
            inner = body[2:-2].strip()
            if "**" not in inner:
                body = inner
        elif body.startswith("**"):
            body = body[2:].lstrip()
        elif body.endswith("**") and body.count("**") == 1:
            body = body[:-2].rstrip()
        # odd number of ** left → drop all markers on this heading line
        if body.count("**") % 2 == 1:
            body = body.replace("**", "")
        return f"{hashes}{body}"

    return re.sub(r"(?m)^(#{1,6}\s+)(.+)$", _repl, text)


def _balance_bold_line(line: str) -> str:
    """
    Ensure ** markers on one line are even.
    - headings / empty after opener → strip orphan
    - single opener with content → auto-close
    - other odd counts → drop the last orphan **
    """
    if "**" not in line:
        return line
    # Leave inline code alone: temporarily park `...`
    parks: List[str] = []

    def _park(m: re.Match) -> str:
        parks.append(m.group(0))
        return f"\x00{len(parks) - 1}\x00"

    work = re.sub(r"`[^`\n]+`", _park, line)
    markers = list(re.finditer(r"\*\*", work))
    n = len(markers)
    if n == 0:
        out = work
    elif n % 2 == 0:
        out = work
    elif n == 1:
        m = markers[0]
        before, after = work[: m.start()], work[m.end() :]
        if re.match(r"^#{1,6}\s+", before) or not after.strip():
            out = before + after
        else:
            core = after.rstrip()
            trail = after[len(core) :]
            out = f"{before}**{core}**{trail}"
    else:
        # Drop trailing orphan (usually an extra closer or unclosed opener).
        last = markers[-1]
        out = work[: last.start()] + work[last.end() :]
        # If still odd (shouldn't), keep stripping.
        while out.count("**") % 2 == 1:
            i = out.rfind("**")
            if i < 0:
                break
            out = out[:i] + out[i + 2 :]

    def _unpark(m: re.Match) -> str:
        return parks[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", _unpark, out)


def ensure_bold_closed(text: str) -> str:
    """Final gate: every ** is paired (line-scoped; journal answers rarely span lines)."""
    if not text or "**" not in text:
        return text
    lines = text.split("\n")
    # Skip fenced code blocks
    out: List[str] = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
        else:
            out.append(_balance_bold_line(line))
    return "\n".join(out)


def _fix_markdown_bold_glitches(text: str) -> str:
    """Repair common model bold mistakes like `**title**（2025）**`."""
    text = _fix_heading_bold(text)
    # **title**（2025）** → **title**（2025）
    text = re.sub(
        r"(\*\*[^*]+?\*\*)\s*([（(]\s*\d{4}\s*年?\s*[)）])\s*\*\*",
        r"\1\2",
        text,
    )
    # title（2025）** DOI → title（2025） DOI
    text = re.sub(
        r"([）)])\s*\*\*(?=\s*(?:DOI|\[查看全文]|查看全文|$))",
        r"\1",
        text,
        flags=re.I | re.M,
    )
    # heading line starts with ** but never closes: **标题
    text = re.sub(r"(?m)^\*\*(?=[^*].*$)(?!.*\*\*)", "", text)
    # trailing orphan ** after sentence / 查看全文
    text = re.sub(r"\*\*(?=\s*(?:查看全文|DOI|$))", "", text, flags=re.M)
    # dangling ** at end of line
    text = re.sub(r"\*\*(?=\s*$)", "", text, flags=re.M)
    # collapse leftover lone ** pairs used as fake headings
    text = re.sub(r"(?m)^\*\*\s*$", "", text)
    # Hard guarantee: pair or drop remaining orphans
    return ensure_bold_closed(text)


def _org_text(orgs: Any) -> str:
    if isinstance(orgs, str):
        return orgs or "（无机构信息）"
    seen = set()
    parts: List[str] = []
    for o in orgs or []:
        s = str(o or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        parts.append(s)
    return " / ".join(parts) or "（无机构信息）"


def _looks_bare_title_line(line: str) -> bool:
    t = (line or "").strip()
    if not t or t.startswith("#") or re.match(r"^[-*+\d]", t):
        return False
    if "DOI" in t or "](" in t or "http" in t or re.search(r"[：:]", t):
        return False
    # long CJK title-like line without list markers
    return bool(re.search(r"[\u4e00-\u9fff]{8,}", t)) and len(t) <= 80


def _ensure_ordered_list_markdown(text: str) -> str:
    """
    Final gate before user-facing output:
    - renumber contiguous top-level ordered items within each section
    - strip trailing bare-title dumps models often append after a real list
    """
    if not text:
        return text
    lines = text.split("\n")
    out: List[str] = []
    expecting = 1
    in_ol_section = False
    bare_run: List[str] = []

    def flush_bare(force_keep: bool = False) -> None:
        nonlocal bare_run
        if not bare_run:
            return
        # Drop only a clear dump of 3+ naked titles after a structured list
        if in_ol_section and not force_keep and len(bare_run) >= 3 and all(
            _looks_bare_title_line(x) for x in bare_run if x.strip()
        ):
            bare_run = []
            return
        out.extend(bare_run)
        bare_run = []

    for line in lines:
        if (
            re.match(r"^#{1,4}\s+", line)
            or re.match(
                r"^(主要合作者|合作者机构|机构分布|全部发文|相关论文|关键词含)",
                line.strip(),
            )
            or re.match(r"^\*\*[^*]+\*\*", line.strip())  # period / section bold titles
        ):
            flush_bare(force_keep=True)
            expecting = 1
            in_ol_section = False
            out.append(line)
            continue

        m = re.match(r"^(\d+)\.\s+(.+)$", line)
        if m:
            flush_bare(force_keep=True)
            body = m.group(2)
            n = expecting if in_ol_section else int(m.group(1))
            if not in_ol_section:
                n = 1
            out.append(f"{n}. {body}")
            expecting = n + 1
            in_ol_section = True
            continue

        if re.match(r"^\s{2,}[-*+]\s+", line):
            flush_bare(force_keep=True)
            out.append(line)
            continue

        if in_ol_section and _looks_bare_title_line(line):
            bare_run.append(line)
            continue

        flush_bare(force_keep=True)
        out.append(line)

    flush_bare()
    return "\n".join(out).strip()


def _kw_names(period: Dict[str, Any], n: int = 5) -> List[str]:
    return [str(r.get("keyword")) for r in (period.get("keywords") or [])[:n] if r.get("keyword")]


def try_journal_overview_template_answer(state: JournalState) -> Optional[str]:
    plan = state.get("query_plan") or {}
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "journal_overview":
        return None
    # Only template when plan/task explicitly asks for overview (ReAct or router).
    task = plan.get("task") or sql.get("task")
    if task and task != "journal_overview":
        return None
    if task != "journal_overview":
        return None
    y0, y1 = sql.get("start_year"), sql.get("end_year")
    periods = sql.get("periods") or []
    authors = sql.get("authors") or []
    institutions = sql.get("institutions") or []
    keywords = sql.get("keywords") or []

    lines = [
        f"## 期刊学术发展概览（{y0}–{y1}）",
        "",
        "### 1. 总览判断",
        "",
    ]
    if len(periods) >= 2:
        first, last = periods[0], periods[-1]
        early = set(_kw_names(first, 8))
        rising = [k for k in _kw_names(last, 8) if k not in early]
        stable = [k for k in _kw_names(last, 8) if k in early]
        lines.append(
            f"从 {first.get('period')} 到 {last.get('period')}，发文规模由约 "
            f"**{first.get('paper_count')}** 篇变化至约 **{last.get('paper_count')}** 篇；"
            "研究重心呈现「稳态主题 + 阶段性新热点」并存。"
        )
        if stable:
            lines.append(f"贯穿始终的主轴包括：{'、'.join(stable)}。")
        if rising:
            lines.append(f"近期相对走强、值得跟踪的方向包括：{'、'.join(rising)}。")
        if len(periods) >= 3:
            mid = periods[1]
            mid_only = [
                k
                for k in _kw_names(mid, 6)
                if k not in early and k not in set(_kw_names(last, 8))
            ]
            if mid_only:
                lines.append(
                    f"{mid.get('period')} 一度较突出、其后回落或分化的主题："
                    f"{'、'.join(mid_only)}。"
                )
    else:
        lines.append("阶段划分不足，以下仅基于全区间结构化统计作简要研判。")
    lines.append("")

    lines.append("### 2. 阶段演进（证据摘要）")
    lines.append("")
    for p in periods:
        kws = p.get("keywords") or []
        top = "、".join(
            f"{r.get('keyword')}（{r.get('paper_count')}篇）" for r in kws[:5]
        ) or "（该阶段关键词不足）"
        lines.append(
            f"- **{p.get('period')}**（约 {p.get('paper_count')} 篇）：{top}"
        )
    lines.append("")

    lines.append("### 3. 作者与机构格局")
    lines.append("")
    if authors:
        top_a = "、".join(
            f"**{a.get('name_zh')}**（{a.get('paper_count')}篇）" for a in authors[:5]
        )
        lines.append(f"发文较活跃的作者节点：{top_a}。")
    if institutions:
        top_i = "、".join(
            f"**{i.get('institution')}**（{i.get('paper_count')}篇）"
            for i in institutions[:5]
        )
        lines.append(f"贡献突出的机构节点：{top_i}。")
    lines.append("")

    lines.append("### 4. 全区间热词与前瞻")
    lines.append("")
    if keywords:
        lines.append(
            "全区间高频主题："
            + "、".join(
                f"{r.get('keyword')}（{r.get('paper_count')}篇）"
                for r in keywords[:10]
            )
            + "。"
        )
    if periods:
        last = periods[-1]
        late_kws = _kw_names(last, 8)
        buckets = [
            (
                "作物遗传与分子",
                [
                    k
                    for k in late_kws
                    if k
                    in {
                        "水稻",
                        "基因表达",
                        "表达分析",
                        "转录组",
                        "基因克隆",
                        "原核表达",
                        "克隆",
                    }
                ],
            ),
            (
                "产量与品质",
                [k for k in late_kws if k in {"产量", "品质", "生长", "生长性能"}],
            ),
            (
                "环境与污染",
                [k for k in late_kws if k in {"镉", "重金属", "土壤"}],
            ),
            (
                "新兴交叉",
                [k for k in late_kws if k in {"肠道菌群", "番茄", "香气"}],
            ),
        ]
        foresight = [
            f"**{title}**（{'、'.join(items)}）"
            for title, items in buckets
            if items
        ]
        if foresight:
            lines.append(
                f"结合 {last.get('period')} 热词，后续可关注："
                + "；".join(foresight)
                + "。"
            )
        elif late_kws:
            lines.append(
                f"结合 {last.get('period')} 高频词继续跟踪："
                f"{'、'.join(late_kws)}。"
            )
    lines.append("")
    lines.append(
        "> 以上判断均来自本刊 SQLite 结构化统计（发文量 / 关键词 / 作者 / 机构）；"
        "机构名已合并空格变体。"
    )
    return "\n".join(lines).strip()


def try_keyword_authors_template_answer(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "keyword_authors":
        return None
    authors = sql.get("authors") or []
    kw = sql.get("keyword") or ""
    if not kw:
        return None
    total_a = sql.get("total_authors") or len(authors)
    total_p = sql.get("total_papers") or 0
    lines = [
        f"## 关键词含「{kw}」的作者",
        "",
        f"共检索到 **{total_a}** 位作者、**{total_p}** 篇相关论文"
        + (f"；下列按相关发文量展示前 {len(authors)} 位。" if total_a > len(authors) else "。"),
        "",
    ]
    if not authors:
        lines.append("未找到匹配作者。")
        return "\n".join(lines)
    for i, a in enumerate(authors, 1):
        name = (a.get("name_zh") or a.get("name_en") or a.get("author_id") or "").strip()
        lines.append(f"{i}. **{name}**（{a.get('paper_count')}篇）")
        for p in a.get("papers") or []:
            title = (p.get("title_zh") or "（无题名）").strip()
            year = p.get("year")
            doi = (p.get("doi") or "").strip()
            url = doi_url(doi) or ""
            year_bit = f"（{year}）" if year else ""
            bits = [f"   - **{title}**{year_bit}"]
            if doi:
                bits.append(f"DOI: {doi}")
            if url:
                bits.append(f"[查看全文]({url})")
            lines.append(" ".join(bits))
        lines.append("")
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_coauthored_papers_template_answer(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "coauthored_papers" and sql.get("task") != "coauthored_papers":
        return None
    a = sql.get("author_name_a") or (sql.get("author_a") or {}).get("name_zh") or "作者A"
    b = sql.get("author_name_b") or (sql.get("author_b") or {}).get("name_zh") or "作者B"
    papers = sql.get("papers") or []
    total = sql.get("total_papers") if sql.get("total_papers") is not None else len(papers)
    lines = [
        f"## {a} 与 {b} 的合著发文（共 {total} 篇）",
        "",
    ]
    if not papers:
        lines.append("未检索到两人共同署名的论文。")
        return "\n".join(lines)
    for i, p in enumerate(papers, 1):
        title = (p.get("title_zh") or p.get("title") or "（无题名）").strip()
        year = p.get("year")
        doi = (p.get("doi") or "").strip()
        url = doi_url(doi) or ""
        year_bit = f"（{year}）" if year else ""
        bits = [f"{i}. **{title}**{year_bit}"]
        if doi:
            bits.append(f"DOI: {doi}")
        if url:
            bits.append(f"[查看全文]({url})")
        lines.append(" ".join(bits))
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_author_template_answer(state: JournalState) -> Optional[str]:
    """
    Format author papers / collaborators directly from SQL evidence.
    Avoids LLM truncation and broken ** markers on long lists.
    """
    overview = try_journal_overview_template_answer(state)
    if overview:
        return overview
    co_ans = try_coauthored_papers_template_answer(state)
    if co_ans:
        return co_ans
    kw_ans = try_keyword_authors_template_answer(state)
    if kw_ans:
        return kw_ans

    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "author":
        return None
    intents = state.get("intents") or []
    rag = state.get("rag_evidence") or {}
    if "rag" in intents and rag.get("hits"):
        return None

    # Safety: pair coauthor questions must never render as single-author dump.
    q = state.get("question") or ""
    if re.search(
        r"[一-龥]{2,4}\s*(?:和|与|跟)\s*[一-龥]{2,4}.*(?:合作|合著|共著)",
        q,
    ):
        return None

    papers = sql.get("papers") or sql.get("recent_papers") or []
    collab = sql.get("collaborators") or {}
    inst = sql.get("collaborator_institutions") or {}
    people = inst.get("collaborators") or collab.get("collaborators") or []
    if not papers and not people:
        return None

    author = sql.get("author") or {}
    name = author.get("name_zh") or sql.get("author_name") or "该作者"

    ask_papers = bool(re.search(r"发文|论文|全部|著作|题名|DOI", q))
    ask_collab = bool(re.search(r"合作|机构|合著", q))
    # 纯合作/机构 → 只列合作者；纯发文 → 只列论文；兼问或含糊 → 两者都给
    if ask_collab and not ask_papers:
        show_papers, show_collab = False, bool(people)
    elif ask_papers and not ask_collab:
        show_papers, show_collab = bool(papers), False
    else:
        show_papers, show_collab = bool(papers), bool(people)
    if not show_papers and not show_collab:
        show_papers = bool(papers)
        show_collab = bool(people) and not papers

    lines: List[str] = []
    if show_papers:
        total = sql.get("total_papers") or len(papers)
        lines.append(f"## {name}全部发文（共 {total} 篇）")
        lines.append("")
        for i, p in enumerate(papers, 1):
            title = (p.get("title_zh") or p.get("title") or "（无题名）").strip()
            year = p.get("year")
            doi = (p.get("doi") or "").strip()
            url = doi_url(doi) or ""
            year_bit = f"（{year}）" if year else ""
            bits = [f"{i}. **{title}**{year_bit}"]
            if doi:
                bits.append(f"DOI: {doi}")
            if url:
                bits.append(f"[查看全文]({url})")
            lines.append(" ".join(bits))
        lines.append("")

    if show_collab:
        lines.append(f"## 主要合作者（共 {len(people)} 人）")
        lines.append("")
        for i, c in enumerate(people, 1):
            cname = (c.get("name_zh") or c.get("name_en") or c.get("author_id") or "").strip()
            co = c.get("co_papers")
            org = _org_text(c.get("institutions"))
            co_bit = f"（{co}篇）" if co is not None else ""
            lines.append(f"{i}. **{cname}**{co_bit}：{org}")

    text = _ensure_ordered_list_markdown("\n".join(lines).strip())
    return text or None


def _cleanup_answer(text: str, intents: List[str]) -> str:
    """Strip common unwanted sections the model still emits."""
    if not text:
        return text
    text = _fix_markdown_bold_glitches(text)
    text = _ensure_ordered_list_markdown(text)
    # Model sometimes emits raw / broken HTML anchors — flatten to markdown
    text = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"[\2](\1)",
        text,
        flags=re.I | re.S,
    )
    # https://url" target="blank" rel="noopener">链接
    text = re.sub(
        r'(https?://[^\s"\'<>]+)\s*["\']?\s*target\s*=\s*["\']?_?blank["\']?[^>\n]*>\s*([^\n<]*)',
        r"[\2](\1)",
        text,
        flags=re.I,
    )
    text = re.sub(r"</?(?:p|div|br|span|ul|ol|li|strong|em)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    # Fix "DOI: <url>" → keep DOI string if URL ends with a DOI path
    def _fix_doi_line(m: re.Match) -> str:
        url = m.group(1).rstrip(").,，。")
        doi = url
        for prefix in (
            "https://www.academax.com/doi/",
            "http://www.academax.com/doi/",
            "https://doi.org/",
            "http://doi.org/",
        ):
            if doi.startswith(prefix):
                doi = doi[len(prefix) :]
                break
        return f"DOI: {doi}"

    text = re.sub(
        r"DOI\s*[:：]\s*(https?://[^\s\)\]\"]+)",
        _fix_doi_line,
        text,
        flags=re.I,
    )
    # Remove 参考文献 / 参考资料 blocks
    text = re.sub(
        r"\n{0,2}#{0,3}\s*参考(?:文献|资料)[\s\S]*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Never expose routing / pipeline notes to the user
    text = re.sub(
        r"\n{0,2}#{0,3}\s*路由[：:][\s\S]*$",
        "",
        text,
    )
    text = re.sub(
        r"(?m)^\s*路由[：:].*$",
        "",
        text,
    )
    text = re.sub(
        r"(?m)^\s*(?:意图|路由意图|route(?:_reason)?)[：:].*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Remove 证据来源 section for non-rag answers
    if "rag" not in (intents or []):
        text = re.sub(
            r"\n{0,2}#{0,3}\s*证据来源[\s\S]*$",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"(?m)^\s*[-*]\s*\*?\*?链接：无.*$",
            "",
            text,
        )
        text = re.sub(
            r"(?m)^\s*[-*]\s*\*?\*?(?:内部统计|内部知识图谱).*$",
            "",
            text,
        )
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return ensure_bold_closed(text)


def build_generate_messages(state: JournalState) -> List[Dict[str, str]]:
    settings = get_settings()
    question = state.get("question") or ""
    intents = state.get("intents") or []
    reason = state.get("route_reason") or ""
    evidence = state.get("evidence_text") or "（无证据）"
    history: List[Dict[str, str]] = state.get("history") or []
    plan = state.get("query_plan") or {}
    focus = plan.get("focus") or ""
    task = plan.get("task") or ""
    ledger = build_evidence_ledger(state)

    # intents/reason are for model grounding only — never ask it to echo them.
    user_prompt = (
        f"用户问题: {question}\n"
        f"（内部参考，勿写入回答）任务={task}; 证据通道: {intents}; {reason}\n"
        f"{('任务焦点: ' + focus + chr(10)) if focus else ''}"
        f"\n以下是各 Agent 汇总证据:\n{evidence}\n\n"
        f"{ledger}\n\n"
        f"{_build_instruction(intents, evidence, task)}\n"
        "必须紧扣用户问题与任务焦点做综合推理与升维总结；"
        "禁止答非所问、套用无关全刊概览，或把证据复述成数据报表。"
        "只输出面向用户的最终答案，不要输出路由、意图或修正说明。"
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    max_h = settings.chat_max_history
    for item in history[-max_h:]:
        messages.append(item)
    messages.append({"role": "user", "content": user_prompt})
    return messages


def _chunk_text(text: str, size: int = 48) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def stream_generate(state: JournalState) -> Iterator[str]:
    templated = try_author_template_answer(state)
    if templated:
        yield from _chunk_text(enforce_evidence_constraints(templated, state))
        return
    chat = MiniMaxChat(get_settings())
    yield from chat.chat_stream(
        build_generate_messages(state),
        max_tokens=8192,
    )


def finalize_answer(
    raw: str,
    intents: List[str],
    state: Optional[JournalState] = None,
) -> str:
    text = _cleanup_answer(raw or "", intents)
    if state is not None:
        text = enforce_evidence_constraints(text, state)
    return text


def generate_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    templated = try_author_template_answer(state)
    if templated:
        return {"answer": finalize_answer(templated, intents, state)}
    chat = MiniMaxChat(get_settings())
    answer = chat.chat(build_generate_messages(state), max_tokens=8192)
    answer = finalize_answer(answer, intents, state)
    return {"answer": answer}
