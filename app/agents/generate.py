from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from app.agents.evidence_guard import (
    build_evidence_ledger,
    enforce_evidence_constraints,
)
from app.agents.state import JournalState
from app.config import bind_corpus, get_settings
from app.services.minimax_chat import MiniMaxChat
from app.utils import doi_url

_SYSTEM_PROMPT_BODY = """请严格基于多路证据（[SQL] / [KG] / [RAG]）与「证据约束」清单综合推理后作答。

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


def build_system_prompt(journal_title: str | None = None) -> str:
    title = journal_title or get_settings().journal_title
    return f"你是《{title}》的资深学术分析助手。\n{_SYSTEM_PROMPT_BODY}"


# Backward-compatible default (农生)
SYSTEM_PROMPT = build_system_prompt("浙江大学学报（农业与生命科学版）")


def _build_instruction(
    intents: List[str],
    evidence: str = "",
    task: str = "",
    analysis_plan: Optional[Dict[str, Any]] = None,
) -> str:
    has_rag = "rag" in intents
    only_struct = bool(intents) and not has_rag
    author_scoped = "【作者个人统计" in (evidence or "")
    analysis_plan = analysis_plan or {}
    shape = analysis_plan.get("answer_shape") or ""
    subgoals = analysis_plan.get("subgoals") or []
    analysis_hint = ""
    if subgoals:
        analysis_hint = (
            "必须按分析规划组织回答，覆盖这些子任务："
            + "；".join(f"[{s.get('type')}]{s.get('desc')}" for s in subgoals[:6])
            + "。"
        )
        if shape:
            analysis_hint += f"回答形态优先遵循 answer_shape={shape}。"

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
            + analysis_hint
        )
    if task in {
        "topic_evolution",
        "keyword_collab",
        "journal_overview",
        "yearly_growth",
        "hotspot_compare",
        "top_institutions",
        "top_authors",
        "institution_authors",
        "topic_coverage",
        "submission_fit",
        "topic_evolution",
        "author_profile",
        "top_directions_with_papers",
    }:
        extra = ""
        if task in {
            "yearly_growth",
            "hotspot_compare",
            "top_institutions",
            "top_authors",
            "institution_authors",
            "topic_coverage",
            "submission_fit",
            "topic_evolution",
            "author_profile",
            "top_directions_with_papers",
        }:
            extra = "本题为 SQL 结构化任务：禁止编造 DOI；禁止引用证据外论文；名单必须来自证据。"
        if task == "top_directions_with_papers":
            extra += (
                "研究方向=证据中的热门关键词/directions，按发文量归纳学科主题；"
                "严禁把在线优先出版、影响因子、CSSCI排名、获奖、办刊通告当作研究方向。"
            )
        if task == "institution_authors":
            extra += (
                "成果=该机构署名作者在本刊的论文；"
                "严禁把校史、纪念、写该大学的文章当作作者代表成果。"
            )
        return (
            f"本题任务={task}。{insight}{extra}{analysis_hint}"
            "凡必须点名的名单用有序序号；不要写参考文献；DOI 用纯文本。"
        )
    if only_struct:
        return (
            f"本题为结构化分析（SQL/KG）。{insight}{analysis_hint}"
            "凡名单/分布必须用有序序号 1. 2. 3.，每条一行；"
            "不要写参考文献；DOI 用纯文本。"
        )
    if has_rag and len(intents) == 1:
        return (
            "本题为文献内容问答。请基于 [RAG] 证据作答并提炼观点；"
            "文末可列相关论文（题名、DOI 纯文本、证据中的链接），不要空的参考文献，不要 HTML。"
            "DOI/年份必须落在证据约束清单内。"
            + analysis_hint
        )
    return (
        f"本题可能同时涉及结构证据与文献。{insight}{analysis_hint}"
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
    y0, y1 = sql.get("start_year"), sql.get("end_year")
    top_n = sql.get("top_n") or sql.get("author_limit") or len(authors)
    year_bit = ""
    if y0 or y1:
        year_bit = f"；区间 {y0 or '不限'}–{y1 or '不限'}"
    lines = [
        f"## 主题「{kw}」相关发文 Top{min(top_n, len(authors) or top_n)} 作者",
        "",
        f"共检索到 **{total_a}** 位作者、**{total_p}** 篇相关论文{year_bit}"
        + (
            f"；下列按相关发文量展示前 {len(authors)} 位。"
            if total_a > len(authors)
            else "。"
        ),
        "",
    ]
    if not authors:
        lines.append("未找到匹配作者。")
        return "\n".join(lines)
    # 主题作者榜的问题同时需要可核验的论文明细。此前作者数超过 12
    # 时会隐藏 papers，导致“作者统计”与“相关论文”之间无法对应。
    show_papers = True
    for i, a in enumerate(authors, 1):
        name = (a.get("name_zh") or a.get("name_en") or a.get("author_id") or "").strip()
        lines.append(f"{i}. **{name}**（{a.get('paper_count')}篇）")
        if show_papers:
            for p in a.get("papers") or []:
                title = (p.get("title_zh") or "（无题名）").strip()
                year = p.get("year")
                doi = (p.get("doi") or "").strip()
                url = doi_url(doi) or ""
                yb = f"（{year}）" if year else ""
                bits = [f"   - **{title}**{yb}"]
                if doi:
                    bits.append(f"DOI: {doi}")
                if url:
                    bits.append(f"[查看全文]({url})")
                lines.append(" ".join(bits))
            lines.append("")
    if not show_papers:
        lines.append("")
    lines.append("> 统计口径：论文关键词字段匹配该主题；非全库无主题过滤的高产作者榜。")
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_unsupported_citations_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    plan = state.get("query_plan") or {}
    if (
        sql.get("scope") != "unsupported"
        and sql.get("task") != "unsupported_citations"
        and plan.get("task") != "unsupported_citations"
    ):
        return None
    reason = sql.get("reason") or (
        "本库 papers 表无被引/引用次数字段，无法按被引用次数排名高被引论文。"
    )
    return (
        f"## 无法完成高被引排名\n\n"
        f"{reason}\n\n"
        "如需了解高影响力研究，可改问：近 N 年热门关键词、高产作者/机构，"
        "或某主题的代表论文（基于题名/摘要检索）。"
    )


def try_hotspot_compare_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "hotspot_compare" and sql.get("task") != "hotspot_compare":
        return None
    periods = sql.get("periods") or []
    if len(periods) < 2:
        return None
    prior, recent = periods[0], periods[1]
    prior_map = {
        r.get("keyword"): int(r.get("paper_count") or 0)
        for r in (prior.get("keywords") or [])
        if r.get("keyword")
    }
    recent_map = {
        r.get("keyword"): int(r.get("paper_count") or 0)
        for r in (recent.get("keywords") or [])
        if r.get("keyword")
    }
    risen = [
        k
        for k in recent_map
        if k not in prior_map or recent_map[k] > prior_map.get(k, 0)
    ][:8]
    fallen = [
        k
        for k in prior_map
        if k not in recent_map or prior_map[k] > recent_map.get(k, 0)
    ][:8]
    lines = [
        "## 研究热点对比（关键词频次）",
        "",
        f"### {prior.get('period')}（约 {prior.get('paper_count')} 篇）",
        "",
    ]
    for i, r in enumerate((prior.get("keywords") or [])[:12], 1):
        lines.append(f"{i}. **{r.get('keyword')}**（{r.get('paper_count')}篇）")
    lines += ["", f"### {recent.get('period')}（约 {recent.get('paper_count')} 篇）", ""]
    for i, r in enumerate((recent.get("keywords") or [])[:12], 1):
        lines.append(f"{i}. **{r.get('keyword')}**（{r.get('paper_count')}篇）")
    lines += ["", "### 变化摘要", ""]
    if risen:
        lines.append("- 近窗相对走强或新晋：" + "、".join(risen))
    if fallen:
        lines.append("- 前窗更突出、近窗回落或淡出：" + "、".join(fallen))
    lines.append("")
    lines.append("> 以上均来自本刊 SQLite 关键词统计，非主观抽样。")
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_hot_topics_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "top_directions":
        return None
    directions = sql.get("directions") or []
    if not directions:
        return None
    adjacent = sql.get("match_scope") == "adjacent"
    lines = [
        "## 相邻主题线索（非专题直接证据）" if adjacent else "## 期刊研究热点",
        "",
        f"统计区间：{sql.get('start_year') or '不限'}–{sql.get('end_year') or '不限'}",
        "",
    ]
    if adjacent:
        lines += [
            f"> 直接主题「{sql.get('primary_topic')}」未命中；以下仅来自相邻检索词，"
            "不能据此认定专题已有稳定发文基础。",
            "",
        ]
    for index, direction in enumerate(directions, 1):
        lines.extend(
            [
                f"### {index}. {direction.get('keyword')}（{direction.get('paper_count')}篇）",
                "",
            ]
        )
        for paper_index, paper in enumerate((direction.get("papers") or [])[:3], 1):
            title = paper.get("title_zh") or paper.get("title") or "（无题名）"
            year = paper.get("year")
            doi = (paper.get("doi") or "").strip()
            url = doi_url(doi) or ""
            bits = [f"{paper_index}. **{title}**" + (f"（{year}）" if year else "")]
            if doi:
                bits.append(f"DOI: {doi}")
            if url:
                bits.append(f"[查看全文]({url})")
            lines.append(" ".join(bits))
        lines.append("")
    lines.append(
        "> 相邻线索按结构化关键词关联论文数排序。"
        if adjacent
        else "> 热点按结构化关键词关联论文数排序；“热点”本身未作为检索关键词。"
    )
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_top_institutions_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "top_institutions" and sql.get("task") != "top_institutions":
        return None
    insts = sql.get("institutions") or []
    if not insts:
        return None
    top_n = sql.get("top_n") or len(insts)
    y0, y1 = sql.get("start_year"), sql.get("end_year")
    lines = [
        f"## 机构发文量 Top{top_n}",
        "",
        f"统计区间：{y0 or '不限'}–{y1 or '不限'}（按论文–机构关联去重计数）",
        "",
    ]
    for i, inst in enumerate(insts[:top_n], 1):
        lines.append(
            f"{i}. **{inst.get('institution')}**（{inst.get('paper_count')}篇）"
        )
    lines.append("")
    lines.append(
        "> 说明：机构名按库内规范化字段统计；无法确认等价关系的学院级单位仍可能分列。"
    )
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_top_authors_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "top_authors" and sql.get("task") != "top_authors":
        return None
    authors = sql.get("authors") or []
    if not authors:
        return None
    top_n = sql.get("top_n") or len(authors)
    y0, y1 = sql.get("start_year"), sql.get("end_year")
    lines = [
        f"## 发文量 Top{top_n} 作者",
        "",
        f"统计区间：{y0 or '不限'}–{y1 or '不限'}（按作者署名论文去重计数）",
        "",
    ]
    for i, a in enumerate(authors[:top_n], 1):
        name = (a.get("name_zh") or a.get("name") or "").strip() or "（无名）"
        author_id = str(a.get("author_id") or "")
        id_bit = f"；author_id: `{author_id}`" if author_id else ""
        lines.append(f"{i}. **{name}**（{a.get('paper_count')}篇{id_bit}）")
    lines.append("")
    lines.append("> 以上来自本刊结构化发文统计；按稳定 author_id 去重，同名作者不自动合并。")
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_authors_papers_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "authors_papers":
        return None
    authors = sql.get("authors") or []
    y0, y1 = sql.get("start_year"), sql.get("end_year")
    offset = int(sql.get("offset") or 0)
    shown = int(sql.get("shown_count") or 0)
    total = int(sql.get("total_count") or 0)
    lines = [
        "## 作者具体论文",
        "",
        f"统计区间：{y0 or '不限'}–{y1 or '不限'}；"
        f"本次展示第 {offset + 1 if shown else 0}–{offset + shown} 条，共 {total} 条作者–论文记录。",
        "",
    ]
    if not authors:
        lines.append("在该范围内未找到相关论文。")
        return "\n".join(lines)
    for author in authors:
        name = (author.get("name_zh") or author.get("name_en") or author.get("author_id") or "（未知作者）").strip()
        papers = author.get("papers") or []
        lines.extend([f"### {name}（本次 {len(papers)} 篇）", ""])
        for index, paper in enumerate(papers, 1):
            title = (paper.get("title_zh") or paper.get("title_en") or "（无题名）").strip()
            year = paper.get("year")
            doi = (paper.get("doi") or "").strip()
            url = doi_url(doi) or ""
            bits = [f"{index}. **{title}**" + (f"（{year}）" if year else "")]
            if doi:
                bits.append(f"DOI: {doi}")
            if url:
                bits.append(f"[查看全文]({url})")
            lines.append(" ".join(bits))
        lines.append("")
    if sql.get("has_more"):
        lines.append(
            f"> 内容较长，已展示 {offset + shown}/{total} 条。输入“继续”可列出剩余论文。"
        )
    return _ensure_ordered_list_markdown("\n".join(lines).strip())


def try_clarification_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "clarification":
        return None
    return str(sql.get("message") or "请补充需要查询的对象。").strip()


def try_institution_authors_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if (
        sql.get("scope") != "institution_authors"
        and sql.get("task") != "institution_authors"
    ):
        return None
    authors = sql.get("authors") or []
    inst = sql.get("institution") or "该机构"
    if not authors:
        return (
            f"## {inst} 相关作者代表成果\n\n"
            f"未检索到署名单位含「{inst}」的作者发文记录。"
        )
    y0, y1 = sql.get("start_year"), sql.get("end_year")
    lines = [
        f"## {inst} 相关作者代表成果",
        "",
        f"- **匹配作者数：** {sql.get('total_authors') or len(authors)}",
        f"- **机构关联发文：** {sql.get('total_papers') or 0} 篇",
        f"- **区间：** {y0 or '不限'}–{y1 or '不限'}",
        "",
        "以下按该机构署名作者在本刊发文量排序，并列出其代表论文"
        "（单位字段含该机构，非「以该机构为主题」的文章）。",
        "",
    ]
    for i, a in enumerate(authors, 1):
        name = (a.get("name_zh") or "").strip() or "（无名）"
        lines.append(f"### {i}. {name}（{a.get('paper_count')}篇）")
        lines.append("")
        papers = a.get("papers") or []
        if not papers:
            lines.append("- （暂无论文样例）")
        else:
            for j, p in enumerate(papers, 1):
                title = (p.get("title_zh") or p.get("title") or "（无题名）").strip()
                year = p.get("year")
                doi = (p.get("doi") or "").strip()
                url = doi_url(doi) or ""
                year_bit = f"（{year}）" if year else ""
                bits = [f"{j}. **{title}**{year_bit}"]
                if doi:
                    bits.append(f"DOI: {doi}")
                if url:
                    bits.append(f"[查看全文]({url})")
                lines.append(" ".join(bits))
        lines.append("")
    lines.append(
        "> 统计口径：作者在该篇论文上的机构署名含目标机构；"
        "勿与校史、纪念、会议报道类文献混淆。"
    )
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_topic_evolution_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    plan = state.get("query_plan") or {}
    if plan.get("task") != "topic_evolution" and sql.get("task") != "topic_evolution":
        return None
    topic = sql.get("topic_keywords") or []
    yearly_by = sql.get("yearly_by_keyword") or {}
    queried = plan.get("keywords") or [
        r.get("keyword") for r in topic if r.get("keyword")
    ]
    lines = [
        "## 专题发展与命中统计",
        "",
        f"查询主题：{'、'.join(str(k) for k in queried if k) or '（无）'}",
        f"区间：{sql.get('start_year') or '不限'}–{sql.get('end_year') or '不限'}",
        "",
    ]
    if not topic or all(int(r.get("paper_count") or 0) == 0 for r in topic):
        lines += [
            "本刊结构化关键词中，上述主题命中很少或为 0；以下不使用全刊发文趋势冒充。",
            "",
        ]
        return _ensure_ordered_list_markdown("\n".join(lines))
    lines += ["### 主题词命中", ""]
    for i, r in enumerate(topic, 1):
        lines.append(f"{i}. **{r.get('keyword')}**（{r.get('paper_count')}篇）")
    lines.append("")
    if yearly_by:
        lines += ["### 逐年命中", ""]
        for term, series in yearly_by.items():
            if not series:
                continue
            bits = "、".join(
                f"{r.get('year')}:{r.get('paper_count')}" for r in series[-12:]
            )
            lines.append(f"- **{term}**：{bits}")
        lines.append("")
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_topic_coverage_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    if sql.get("scope") != "topic_coverage" and sql.get("task") != "topic_coverage":
        return None
    topic = sql.get("topic_keywords") or []
    papers = sql.get("papers") or []
    total = int(sql.get("total_hits") or 0)
    queried = sql.get("keywords_queried") or []
    thin = (not topic) or all(int(r.get("paper_count") or 0) == 0 for r in topic) or total == 0
    lines = [
        "## 专题覆盖与投稿建议",
        "",
        f"查询关键词：{'、'.join(queried) or '（无）'}",
        f"区间：{sql.get('start_year') or '不限'}–{sql.get('end_year') or '不限'}",
        "",
    ]
    if thin:
        lines += [
            "### 判断：覆盖偏薄，不宜以该主题作为主投稿方向",
            "",
            "本刊结构化关键词中，上述专题词命中很少或为 0。"
            "若研究方向高度聚焦基因编辑/CRISPR，建议改投更对口期刊，"
            "或先检索相邻主题（如基因表达、转基因、分子育种）再决定。",
            "",
        ]
    else:
        lines += [
            "### 判断：有一定相关基础，可作参考投稿，但需核对栏目匹配度",
            "",
            "关键词命中：",
            "",
        ]
        for i, r in enumerate(topic, 1):
            lines.append(f"{i}. **{r.get('keyword')}**（{r.get('paper_count')}篇）")
        lines.append("")
    if papers:
        lines += ["### 可参考的本刊相关论文（仅证据内）", ""]
        for i, p in enumerate(papers[:10], 1):
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


def try_submission_fit_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    plan = state.get("query_plan") or {}
    if sql.get("scope") != "submission_fit" and sql.get("task") != "submission_fit":
        if plan.get("task") != "submission_fit":
            return None
        if sql.get("scope") not in {"submission_fit", "topic_coverage"}:
            return None
    topic = sql.get("topic_keywords") or []
    papers = sql.get("sample_papers") or sql.get("papers") or []
    generic_papers = sql.get("generic_only_papers") or []
    total = int(sql.get("total_hits") or 0)
    queried = sql.get("keywords_queried") or plan.get("keywords") or []
    phrase = plan.get("topic_phrase") or ""
    fit = (sql.get("fit_label") or "weak").strip().lower()
    cov = sql.get("coverage_summary") or {}
    rising = bool(cov.get("rising_recently"))
    score_basis = (cov.get("score_basis") or "").strip()
    specific_hits = int(cov.get("specific_hits") or 0)
    generic_hits = int(cov.get("generic_hits") or 0)
    effective = cov.get("effective_hits")
    primary_topic = str(sql.get("primary_topic") or "")
    direct_hits = int(sql.get("direct_hits") or 0)
    adjacent_topics = list(sql.get("adjacent_topics") or [])
    adjacent_hits = int(sql.get("adjacent_hits") or 0)
    planned_operations = {
        str(op.get("type"))
        for op in (plan.get("operations") or [])
        if isinstance(op, dict)
    }
    label_zh = {"strong": "较强", "moderate": "中等", "weak": "偏弱"}.get(fit, fit)
    lines = [
        "## 投稿适配评估",
        "",
    ]
    if phrase:
        lines.append(f"论文主题：{phrase}")
    lines += [
        f"查询关键词：{'、'.join(str(k) for k in queried) or '（无）'}",
        f"区间：{sql.get('start_year') or '不限'}–{sql.get('end_year') or '不限'}",
        "",
        "### 覆盖结论",
        "",
        f"- 适合度标签：**{label_zh}**（`{fit}`）",
        f"- 关键词命中篇数（按词累加，可重叠）：**{total}**",
    ]
    if primary_topic:
        lines.append(f"- 直接主题「{primary_topic}」命中：**{direct_hits}**")
        if adjacent_topics:
            lines.append(
                f"- 相邻检索词「{'、'.join(adjacent_topics)}」命中：**{adjacent_hits}**；"
                "该部分只作为相邻证据，不等同于直接命中。"
            )
    if score_basis == "domain_facet" or score_basis == "generic_only":
        lines.append(
            f"- 主题专指面命中：**{specific_hits}**；宽泛技术词命中：**{generic_hits}**"
            + (
                f"；有效计分命中：**{effective}**"
                if effective is not None and score_basis == "domain_facet"
                else ""
            )
        )
    lines += [
        f"- 近年是否上升：{'是' if rising else '否/不明显'}",
        "",
    ]
    if fit == "strong":
        lines += [
            f"**判断：** 本刊对「{phrase or '、'.join(map(str, queried)) or '该主题'}」"
            "相关关键词有较稳定命中，**可作为投稿参考**；仍需核对栏目与学科匹配度。",
            "",
            "注意：命中≠录用。请勿把相邻技术（如仅信息化/遥感综述）直接等同于你的主题已充分覆盖。",
            "",
        ]
    elif fit == "moderate":
        lines += [
            f"**判断：** 本刊有一定相关基础，**可作参考投稿，但匹配度一般**；"
            "建议对照下列代表论文，确认问题意识与方法是否同频。",
            "",
        ]
    elif score_basis == "generic_only":
        lines += [
            f"**判断：不建议以「{phrase or '、'.join(map(str, queried)) or '该主题'}」"
            "作为向本刊投稿的主方向。**",
            "",
            "依据：用户主题的专指面关键词命中为 0；"
            f"「人工智能」等宽泛技术词虽有 {generic_hits} 篇命中，"
            "可能来自其他研究语境，**不能**等同于用户的复合主题已覆盖。",
            "",
            "可选下一步：核对期刊当前征稿范围，并优先考察历史上持续发表该专指主题的期刊；"
            "若继续考虑本刊，应先确认论文问题意识与现有相邻研究是否真正一致。",
            "",
        ]
    else:
        lines += [
            f"**判断：不建议以「{phrase or '、'.join(map(str, queried)) or '该主题'}」"
            "作为向本刊投稿的主方向。**",
            "",
            "依据：结构化关键词命中偏少或为 0。以下不会用证据外旧文、办刊通告，"
            "或仅因“期刊对新技术开放”来论证适合投稿。",
            "",
            "可选下一步：检索证据中实际出现的相邻主题，判断是否存在真实契合点；"
            "也可比较其他历史上持续发表该主题的期刊。",
            "",
        ]
    if topic and any(int(r.get("paper_count") or 0) > 0 for r in topic):
        lines += ["### 证据热词 / 篇数", ""]
        for i, r in enumerate(topic, 1):
            lines.append(f"{i}. **{r.get('keyword')}**（{r.get('paper_count')}篇）")
        lines.append("")
    if papers and "topic_papers" not in planned_operations:
        lines += ["### 本刊相关论文（仅证据内，可作参考）", ""]
        for i, p in enumerate(papers[:10], 1):
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
    elif generic_papers and score_basis == "generic_only":
        lines += [
            "### 仅匹配宽泛技术词的论文（不计为主题适配证据）",
            "",
            f"下列论文只命中宽泛检索词，不能证明与「{phrase or primary_topic or '目标主题'}」直接相关：",
            "",
        ]
        for i, p in enumerate(generic_papers[:6], 1):
            title = (p.get("title_zh") or p.get("title") or "（无题名）").strip()
            year = p.get("year")
            doi = (p.get("doi") or "").strip()
            year_bit = f"（{year}）" if year else ""
            lines.append(
                f"{i}. **{title}**{year_bit}" + (f" DOI: {doi}" if doi else "")
            )
        lines.append("")
    # Do not surface weak RAG hits as “适合投稿”论据；only optional adjacent clues
    rag = state.get("rag_evidence") or {}
    rag_hits = rag.get("hits") or []
    if fit == "weak" and rag_hits:
        lines += [
            "### 相邻语义线索（不计入适合度）",
            "",
            "以下来自语义检索，**不能**单独证明适合投稿；名单仍以 SQL 关键词为准：",
            "",
        ]
        for i, h in enumerate(rag_hits[:3], 1):
            meta = h.get("metadata") or h
            title = (meta.get("title_zh") or meta.get("title") or "（无题名）").strip()
            doi = (meta.get("doi") or "").strip()
            lines.append(f"{i}. **{title}**" + (f" DOI: {doi}" if doi else ""))
        lines.append("")
    lines += [
        "### 投稿注意",
        "",
        "- 结论仅基于本库关键词与代表论文，不代表审稿或录用承诺。",
        "- 禁止引用证据外 DOI；禁止用校史/办刊通告/影响因子类文献充当研究覆盖。",
        "- 复合主题按「专指面」计分：仅宽泛技术词命中不会抬高适合度。",
    ]
    return _ensure_ordered_list_markdown("\n".join(lines))


def try_yearly_growth_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    plan = state.get("query_plan") or {}
    if sql.get("task") != "yearly_growth" and plan.get("task") != "yearly_growth":
        return None
    rows = sql.get("yoy") or []
    if not rows:
        return None
    fg = sql.get("fastest_growth")
    # growth / decline streaks (ignore incomplete last year if sharp drop)
    growth_years = [
        r for r in rows if r.get("delta") is not None and int(r.get("delta") or 0) > 0
    ]
    decline_years = [
        r for r in rows if r.get("delta") is not None and int(r.get("delta") or 0) < 0
    ]
    last = rows[-1] if rows else None
    incomplete_note = ""
    if last and last.get("yoy_pct") is not None and float(last["yoy_pct"]) <= -40:
        incomplete_note = (
            f"\n\n> 注意：{last.get('year')} 年发文 {last.get('paper_count')} 篇、"
            f"同比 {last.get('yoy_pct')}%，很可能为**未完年/数据未齐**，不宜直接解读为断崖下跌。"
        )
    lines = [
        "## 年度发文趋势与增速",
        "",
        f"区间：{sql.get('start_year')}–{sql.get('end_year')}",
        "",
    ]
    if fg:
        lines.append(
            f"**增长最快年份：** {fg.get('year')} "
            f"（Δ{fg.get('delta'):+d}，同比 {fg.get('yoy_pct')}%）"
        )
        lines.append("")
    lines.append("### 逐年发文（含同比）")
    lines.append("")
    for r in rows:
        if r.get("delta") is None:
            lines.append(f"- {r.get('year')}：{r.get('paper_count')} 篇")
        else:
            pct = r.get("yoy_pct")
            pct_s = f"{pct}%" if pct is not None else "—"
            lines.append(
                f"- {r.get('year')}：{r.get('paper_count')} 篇"
                f"（Δ{r.get('delta'):+d}，同比 {pct_s}）"
            )
    lines += ["", "### 增长期与下降期（基于同比）", ""]
    if growth_years:
        lines.append(
            "- 同比增长年份："
            + "、".join(str(r.get("year")) for r in growth_years)
        )
    if decline_years:
        # exclude suspected incomplete last year from "decline period" narrative list
        shown = [
            r
            for r in decline_years
            if not (
                last
                and r.get("year") == last.get("year")
                and last.get("yoy_pct") is not None
                and float(last["yoy_pct"]) <= -40
            )
        ]
        if shown:
            lines.append(
                "- 同比下降年份：" + "、".join(str(r.get("year")) for r in shown)
            )
    lines.append(incomplete_note)
    kws = sql.get("keywords") or []
    if kws:
        lines += ["", "### 热门关键词（学科主题）", ""]
        for i, r in enumerate(kws[:15], 1):
            lines.append(
                f"{i}. **{r.get('keyword')}**（{r.get('paper_count')}篇）"
            )
        lines.append("")
        lines.append(
            "> 以上关键词来自论文标注字段的频次统计，不含办刊通告/获奖类非研究文献。"
        )
    return "\n".join(lines).strip()


def try_author_not_found_template(state: JournalState) -> Optional[str]:
    """Hard refuse when a named author is absent from this journal corpus."""
    sql = state.get("sql_evidence") or {}
    plan = state.get("query_plan") or {}
    entities = state.get("entities") or {}
    is_author_task = (
        sql.get("scope") == "author"
        or plan.get("task") == "author_profile"
        or sql.get("task") == "author_profile"
    )
    if not is_author_task:
        return None
    if sql.get("author"):
        return None
    name = (
        sql.get("author_name")
        or plan.get("author_name")
        or entities.get("author_name")
        or "该作者"
    )
    name = str(name).strip() or "该作者"
    return (
        f"## 未找到作者「{name}」\n\n"
        f"在本刊知识库中**没有**名为「{name}」的作者记录，"
        "因此**不能确认其在本刊有发文**，也无法列出论文、合作者或研究主题。\n\n"
        "请核对姓名是否有误（含异体字/曾用名），或改问本刊其他已知作者。"
    )


def try_author_trajectory_template(state: JournalState) -> Optional[str]:
    sql = state.get("sql_evidence") or {}
    q = state.get("question") or ""
    if sql.get("scope") != "author":
        return None
    if not sql.get("author"):
        return try_author_not_found_template(state)
    if not re.search(r"轨迹|首次发表|主题变化|合作作者变化|合作者变化", q):
        return None
    author = sql.get("author") or {}
    name = author.get("name_zh") or sql.get("author_name") or "该作者"
    papers = sql.get("papers") or sql.get("recent_papers") or []
    kws = sql.get("keywords") or []
    collab = sql.get("collaborators") or {}
    inst = sql.get("collaborator_institutions") or {}
    people = inst.get("collaborators") or collab.get("collaborators") or []
    lines = [
        f"## {name} 的研究轨迹",
        "",
        f"- **首次发表年份：** {sql.get('year_min') or '未知'}",
        f"- **最近发文年份：** {sql.get('year_max') or '未知'}",
        f"- **本刊发文总量：** {sql.get('total_papers') or len(papers)} 篇",
        "",
        "### 研究主题（论文关键词频次）",
        "",
    ]
    if kws:
        for i, r in enumerate(kws[:12], 1):
            lines.append(f"{i}. **{r.get('keyword')}**（{r.get('paper_count')}篇）")
    else:
        lines.append("（无关键词统计）")
    lines += ["", "### 合作作者变化（按合著篇数）", ""]
    if people:
        for i, c in enumerate(people[:15], 1):
            cname = (c.get("name_zh") or c.get("name_en") or "").strip()
            co = c.get("co_papers")
            org = _org_text(c.get("institutions"))
            co_bit = f"（{co}篇）" if co is not None else ""
            lines.append(f"{i}. **{cname}**{co_bit}：{org}")
    else:
        lines.append("（暂无合作者统计）")
    lines += ["", f"### 本刊发文列表（共 {len(papers)} 篇）", ""]
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
    lines.append("> 以上仅含该作者在本刊的结构化记录，未混入其他作者论文。")
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
    """Format structured SQL answers without a second interpretation pass."""
    clarification = try_clarification_template(state)
    if clarification:
        return clarification
    expanded = try_authors_papers_template(state)
    if expanded:
        return expanded
    refuse = try_unsupported_citations_template(state)
    if refuse:
        return refuse
    missing = try_author_not_found_template(state)
    if missing:
        return missing
    hotspot = try_hotspot_compare_template(state)
    if hotspot:
        return hotspot
    hot_topics = try_hot_topics_template(state)
    if hot_topics:
        return hot_topics
    inst = try_top_institutions_template(state)
    if inst:
        return inst
    top_a = try_top_authors_template(state)
    if top_a:
        return top_a
    inst_authors = try_institution_authors_template(state)
    if inst_authors:
        return inst_authors
    submission = try_submission_fit_template(state)
    if submission:
        return submission
    topic_evo = try_topic_evolution_template(state)
    if topic_evo:
        return topic_evo
    coverage = try_topic_coverage_template(state)
    if coverage:
        return coverage
    yearly = try_yearly_growth_template(state)
    if yearly:
        return yearly
    overview = try_journal_overview_template_answer(state)
    if overview:
        return overview
    co_ans = try_coauthored_papers_template_answer(state)
    if co_ans:
        return co_ans
    kw_ans = try_keyword_authors_template_answer(state)
    if kw_ans:
        return kw_ans
    traj = try_author_trajectory_template(state)
    if traj:
        return traj

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
    inst_data = sql.get("collaborator_institutions") or {}
    people = inst_data.get("collaborators") or collab.get("collaborators") or []
    if not papers and not people:
        return None

    author = sql.get("author") or {}
    name = author.get("name_zh") or sql.get("author_name") or "该作者"

    ask_papers = bool(re.search(r"发文|论文|全部|著作|题名|DOI|轨迹|主题", q))
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


def _render_new_operation(op_type: str, data: Dict[str, Any]) -> Optional[str]:
    if op_type == "author_profile":
        author = data.get("author") or {}
        if not author:
            return None
        name = author.get("name_zh") or author.get("name_en") or "该作者"
        return "\n".join(
            [
                "## 作者身份与发文概况",
                "",
                f"- **作者：** {name}",
                f"- **author_id：** `{author.get('author_id')}`",
                f"- **本刊去重发文量：** {data.get('total_papers')} 篇",
                f"- **发表区间：** {data.get('year_min')}–{data.get('year_max')}",
                "",
                "> 以上画像按稳定 author_id 汇总；未将同名字符串自动合并。",
            ]
        )
    if op_type == "yearly_counts":
        rows = data.get("yearly") or []
        if not rows:
            return None
        lines = [f"## 年度发文量（{data.get('start_year')}–{data.get('end_year')}）", ""]
        lines += [f"- {row.get('year')}：{row.get('paper_count')} 篇" for row in rows]
        if int(data.get("end_year") or 0) == datetime.now().year:
            lines += ["", f"> {datetime.now().year} 年为未完年，当前数量不宜与完整年份直接比较。"]
        return "\n".join(lines)
    if op_type == "yoy_growth":
        rows = data.get("yoy") or []
        if not rows:
            return None
        fastest = data.get("fastest_growth") or {}
        lines = ["## 年度变化与同比", ""]
        if fastest:
            lines += [
                f"增长最快年份：**{fastest.get('year')}**（增加 {fastest.get('delta')} 篇，"
                f"同比 {fastest.get('yoy_pct')}%）。",
                "",
            ]
        for row in rows[1:]:
            lines.append(
                f"- {row.get('year')}：变化 {int(row.get('delta') or 0):+d} 篇，"
                f"同比 {row.get('yoy_pct')}%"
            )
        return "\n".join(lines)
    if op_type == "top_keywords":
        rows = data.get("keywords") or []
        if not rows:
            return None
        lines = ["## 热门关键词", ""]
        lines += [f"{i}. **{r.get('keyword')}**（{r.get('paper_count')}篇）" for i, r in enumerate(rows, 1)]
        return "\n".join(lines)
    if op_type == "institutions_by_keyword":
        rows = data.get("institutions") or []
        if not rows:
            return None
        title = (
            f"相邻检索词相关机构（直接主题「{data.get('primary_topic')}」未命中）"
            if data.get("match_scope") == "adjacent"
            else f"主题「{data.get('keyword') or ''}」相关机构"
        )
        lines = [f"## {title}", ""]
        lines += [f"{i}. **{r.get('institution')}**（{r.get('paper_count')}篇）" for i, r in enumerate(rows, 1)]
        return "\n".join(lines)
    if op_type == "author_keywords_sample":
        rows = data.get("author_keywords") or []
        if not rows:
            return None
        lines = ["## 核心作者及研究方向", ""]
        for i, row in enumerate(rows, 1):
            kws = "、".join(str(k.get("keyword")) for k in (row.get("keywords") or [])[:6])
            lines.append(f"{i}. **{row.get('name_zh')}**（{row.get('paper_count')}篇）：{kws}")
        return "\n".join(lines)
    if op_type == "keyword_growth":
        rows = data.get("keyword_growth") or []
        periods = data.get("periods") or []
        if len(periods) < 2:
            return None
        lines = [
            "## 关键词增长与新兴方向", "",
            f"比较区间：{periods[0].get('start_year')}–{periods[0].get('end_year')} vs. {periods[1].get('start_year')}–{periods[1].get('end_year')}（按论文占比变化排序）", "",
        ]
        for i, row in enumerate(rows, 1):
            tag = "，新兴候选" if row.get("emerging") else ""
            lines.append(
                f"{i}. **{row.get('keyword')}**：前期 {row.get('early_count')} 篇，近期 {row.get('late_count')} 篇，"
                f"占比变化 {float(row.get('share_delta_pp') or 0):+.3f} 个百分点，"
                f"近期覆盖 {row.get('late_active_years')} 个年份{tag}"
            )
        if data.get("partial_year"):
            lines += ["", f"> {data.get('partial_year')} 年为未完年，已展示原始数据，但未参与增长排序。"]
        lines += ["", "> 这些只是基于本刊历史发表数据的候选信号，不代表全学科、政策或市场的未来趋势。"]
        return "\n".join(lines)
    if op_type == "topic_period_compare":
        periods = data.get("periods") or []
        topics = data.get("topics") or []
        if len(periods) < 2:
            return None
        title = "领域变化与阶段对照" if data.get("comparison_goal") == "field_evolution" else "主题阶段变化"
        lines = [f"## {title}", "", f"比较：{periods[0].get('start_year')}–{periods[0].get('end_year')} vs. {periods[1].get('start_year')}–{periods[1].get('end_year')}", ""]
        enhanced_names = [str(row.get("keyword")) for row in (data.get("enhanced") or [])[:3] if row.get("keyword")]
        weakened_names = [str(row.get("keyword")) for row in (data.get("weakened") or [])[:3] if row.get("keyword")]
        if enhanced_names or weakened_names:
            summary_bits = []
            if enhanced_names:
                summary_bits.append("近期占比增强的候选包括" + "、".join(enhanced_names))
            if weakened_names:
                summary_bits.append("近期占比减弱的候选包括" + "、".join(weakened_names))
            lines += ["**核心差异摘要：** " + "；".join(summary_bits) + "。", ""]
        for row in topics:
            delta = float(row.get("share_delta_pp") or 0)
            trend = "增强" if delta > 0 else ("减弱" if delta < 0 else "持平")
            lines.append(f"- **{row.get('keyword')}**：{row.get('early_count')} → {row.get('late_count')} 篇，占比{trend} {abs(delta):.3f} 个百分点")
        if data.get("include_gap_candidates"):
            gaps = list(data.get("disappeared") or [])
            for row in data.get("weakened") or []:
                if int(row.get("late_count") or 0) <= 1 and row not in gaps:
                    gaps.append(row)
            lines += ["", "## 内容缺口候选", ""]
            if gaps:
                for row in gaps[:5]:
                    lines.append(
                        f"- **{row.get('keyword')}**：近期仅 {row.get('late_count')} 篇；"
                        "可作为待核查的低覆盖方向，不代表存在外部市场需求。"
                    )
            else:
                lines.append("现有关键词数据未形成明确的低覆盖候选。")
            lines += [
                "",
                "> 预测限制：以上只反映本刊历史发文结构，不能据此推断政策、市场规模或全学科趋势。",
            ]
        else:
            lines += [
                "",
                "> 可比性限制：变化按论文占比计算；样本稀疏或关键词标注变化可能影响阶段比较。",
            ]
        return "\n".join(lines)
    if op_type == "author_topic_summary":
        rows = data.get("keywords") or []
        author = data.get("author") or {}
        if not rows:
            return None
        lines = [f"## {author.get('name_zh') or '该作者'}的研究主题", ""]
        lines += [f"{i}. **{r.get('keyword')}**（{r.get('paper_count')}篇）" for i, r in enumerate(rows, 1)]
        return "\n".join(lines)
    if op_type == "author_direction_evolution":
        evolutions = data.get("authors") or [data]
        if not evolutions:
            return None
        lines = ["## 作者研究方向演化", ""]
        for evolution in evolutions:
            if evolution.get("evolution"):
                evolution = evolution["evolution"]
            author = evolution.get("author") or {}
            periods = evolution.get("periods") or []
            if not periods:
                continue
            lines += [f"### {author.get('name_zh') or author.get('name_en') or '该作者'}", ""]
            for period in periods:
                kws = "、".join(f"{r.get('keyword')}（{r.get('paper_count')}篇）" for r in (period.get("keywords") or [])[:6]) or "无足够关键词"
                lines.append(
                    f"- **{period.get('label')}（{period.get('start_year')}–{period.get('end_year')}）**："
                    f"发文 {period.get('paper_count')} 篇；主要主题：{kws}"
                )
            changes = evolution.get("changes") or []
            if changes:
                change_text = "；".join(
                    f"{row.get('keyword')} {row.get('change')} {float(row.get('share_delta_pp') or 0):+.1f}pp"
                    for row in changes[:5]
                )
                lines += [f"- 前后期变化：{change_text}", ""]
        return "\n".join(lines)
    if op_type == "author_direction_diversity":
        rows = data.get("authors") or []
        if not rows:
            return None
        lines = ["## 跨研究方向作者", ""]
        for i, row in enumerate(rows, 1):
            kws = "、".join(str(k.get("keyword")) for k in (row.get("keywords") or [])[:6])
            lines.append(f"{i}. **{row.get('name_zh')}**：{row.get('direction_count')} 个有效方向；{kws}")
        return "\n".join(lines)
    if op_type == "institution_stability":
        rows = data.get("institutions") or []
        if not rows:
            return None
        lines = [
            f"## 长期高产机构（{data.get('start_year')}–{data.get('end_year')}）",
            "",
            "判定定义：至少覆盖 5 个完整年份；活跃年份率不低于 80%，且进入年度 Top10 的年份率不低于 50%。",
            "",
        ]
        for i, row in enumerate(rows, 1):
            tag = "稳定高产" if row.get("stable_high_output") else "未达到稳定阈值"
            lines.append(
                f"{i}. **{row.get('institution')}**：累计 {row.get('total_papers')} 篇，活跃 {row.get('active_years')} 年，"
                f"进入年度 Top10 {row.get('top10_years')} 年（{tag}）"
            )
        lines += ["", "> 机构名称按规范化字段合并空格与编号变体；无法确认的别名不强制合并。"]
        return "\n".join(lines)
    if op_type == "coauthored_papers":
        papers = data.get("papers") or []
        author_a = data.get("author_name_a") or (data.get("author_a") or {}).get("name_zh")
        author_b = data.get("author_name_b") or (data.get("author_b") or {}).get("name_zh")
        title = f"{author_a} 与 {author_b} 的合著论文" if author_a and author_b else "代表合作论文"
        lines = [f"## {title}", ""]
        if not papers:
            return "\n".join(lines + ["现有数据中没有可追溯的共同署名论文。"])
        for i, paper in enumerate(papers, 1):
            title = paper.get("title_zh") or paper.get("title") or "（无题名）"
            doi = str(paper.get("doi") or "")
            link = doi_url(doi)
            lines.append(
                f"{i}. **{title}**（{paper.get('year')}）；DOI: {doi}"
                + (f"；[查看全文]({link})" if link else "")
            )
        return _ensure_ordered_list_markdown("\n".join(lines))
    if op_type in {"topic_papers", "representative_papers_by_topic", "author_papers", "representative_papers_by_institution"}:
        groups = data.get("topics") or data.get("authors") or data.get("institutions") or []
        title = "作者论文" if op_type == "author_papers" else (
            "主题论文" if op_type == "topic_papers" else "代表论文"
        )
        lines: List[str] = []
        related = data.get("related_keywords") or []
        if op_type == "topic_papers" and related:
            lines += ["## 主题概览", ""]
            for group in related:
                keywords = "、".join(
                    f"{row.get('keyword')}（{row.get('paper_count')}篇）"
                    for row in (group.get("keywords") or [])[:8]
                    if row.get("keyword")
                )
                lines.append(
                    f"- **{group.get('topic')}**：相关子方向候选为 {keywords or '暂无足够的共现关键词证据'}。"
                )
            lines.append("")
        lines += [f"## {title}", ""]
        if op_type in {"topic_papers", "author_papers"}:
            shown = int(data.get("shown_count") or len(data.get("papers") or []))
            total = int(data.get("total_count") or shown)
            offset = int(data.get("offset") or 0)
            lines += [
                f"检索区间：{data.get('start_year') or '不限'}–{data.get('end_year') or '不限'}；"
                f"本次展示第 {offset + 1 if shown else 0}–{offset + shown} 条，共 {total} 篇。",
                "",
            ]
        if groups:
            missing_group_labels: List[str] = []
            for group in groups:
                label = (
                    group.get("topic") or group.get("keyword") or group.get("name_zh")
                    or group.get("author_name") or group.get("institution") or "相关结果"
                )
                papers = group.get("papers") or []
                if not papers and op_type == "representative_papers_by_institution":
                    missing_group_labels.append(str(label))
                    continue
                group_total = group.get("paper_count")
                count_label = (
                    f"本次 {len(papers)} / 共 {group_total} 篇"
                    if group_total is not None and int(group_total) != len(papers)
                    else f"{len(papers)}篇"
                )
                lines += [f"### {label}（{count_label}）", ""]
                for i, paper in enumerate(papers, 1):
                    title = paper.get("title_zh") or paper.get("title") or "（无题名）"
                    doi = str(paper.get("doi") or "")
                    link = doi_url(doi)
                    author_text = "、".join(str(name) for name in (paper.get("authors") or []) if name)
                    suffix = f"；作者：{author_text}" if author_text else ""
                    suffix += f"；DOI: {doi}" if doi else ""
                    if link:
                        suffix += f"；[查看全文]({link})"
                    lines.append(f"{i}. **{title}**（{paper.get('year')}）{suffix}")
                lines.append("")
            if missing_group_labels:
                lines += [
                    "> 以下机构在当前筛选和代表性过滤下没有可展示论文："
                    + "、".join(missing_group_labels)
                    + "。",
                    "",
                ]
        else:
            for i, paper in enumerate(data.get("papers") or [], 1):
                title = paper.get("title_zh") or paper.get("title") or "（无题名）"
                doi = str(paper.get("doi") or "")
                link = doi_url(doi)
                author_text = "、".join(str(name) for name in (paper.get("authors") or []) if name)
                suffix = f"；作者：{author_text}" if author_text else ""
                suffix += f"；DOI: {doi}" if doi else ""
                if link:
                    suffix += f"；[查看全文]({link})"
                lines.append(f"{i}. **{title}**（{paper.get('year')}）{suffix}")
        if data.get("has_more"):
            lines += [
                "",
                f"> 内容较长，已展示 {int(data.get('offset') or 0) + int(data.get('shown_count') or 0)}/"
                f"{int(data.get('total_count') or 0)} 条。输入“继续”可列出剩余论文。",
            ]
        if op_type == "topic_papers":
            lines += [
                "",
                "> 检索口径：论文关键词字段包含所查主题；未写入关键词字段的相关论文可能遗漏。",
            ]
        return "\n".join(lines).strip() if len(lines) > 2 else None
    if op_type in {"authors_by_keyword", "representative_authors_by_topic"}:
        rows = data.get("authors") or []
        if not rows:
            return None
        title = (
            f"相邻检索词相关作者（直接主题「{data.get('primary_topic')}」未命中）"
            if data.get("match_scope") == "adjacent"
            else f"主题「{data.get('keyword') or '、'.join(data.get('topics') or [])}」代表作者"
        )
        lines = [f"## {title}", ""]
        for i, row in enumerate(rows, 1):
            topics = "、".join(row.get("topics") or [])
            suffix = f"；涉及 {topics}" if topics else ""
            lines.append(f"{i}. **{row.get('name_zh') or row.get('name_en')}**（{row.get('paper_count')}篇）{suffix}")
        return "\n".join(lines)
    if op_type == "topic_yearly":
        series = data.get("yearly_by_keyword") or {}
        if not series:
            return None
        lines = ["## 主题年度变化", ""]
        for topic, rows in series.items():
            values = "、".join(f"{row.get('year')}年 {row.get('paper_count')}篇" for row in rows)
            lines.append(f"- **{topic}**：{values or '无命中'}")
        return "\n".join(lines)
    if op_type == "author_collaborators":
        groups = data.get("authors") or []
        if not groups:
            return None
        lines = ["## 主要合作者", ""]
        for group in groups:
            author = group.get("author") or {}
            lines += [f"### {author.get('name_zh') or author.get('name_en')}", ""]
            collaborators = group.get("collaborators") or []
            for i, row in enumerate(collaborators[:3], 1):
                lines.append(f"{i}. **{row.get('name_zh') or row.get('name_en')}**（共同 {row.get('co_papers')} 篇）")
            if len(collaborators) > 3:
                lines.append(f"> 展示前 3/{len(collaborators)} 位合作者；完整共同论文关系保留在证据结果中。")
            lines.append("")
        return "\n".join(lines)
    if op_type in {"author_network", "institution_network"}:
        rows = data.get("edges") or []
        title = "作者合作网络" if op_type == "author_network" else "机构合作网络"
        if not rows:
            if data.get("query_completed"):
                topic = data.get("topic") or "当前条件"
                return f"## {title}\n\n在主题「{topic}」的限定结果集合内，未发现可由共同署名论文证明的合作边。"
            return None
        lines = [f"## {title}", ""]
        for i, row in enumerate(rows, 1):
            lines.append(
                f"{i}. **{row.get('source_name') or row.get('source_id')} ↔ "
                f"{row.get('target_name') or row.get('target_id')}**（共同 {row.get('paper_count')} 篇）"
            )
        lines += [
            "",
            "> 网络边界：仅展示当前主题 ResultSet 内、且能由共同 DOI 回溯的署名关系；“核心”不等同于学术影响力。",
        ]
        return "\n".join(lines)
    if op_type == "submission_guidance":
        if data.get("fit_label") is None:
            return None
        labels = {"strong": "匹配度较高", "moderate": "有一定匹配", "weak": "直接证据较弱"}
        label = labels.get(data.get("fit_label"), str(data.get("fit_label")))
        return (
            "## 投稿建议\n\n"
            f"基于历史发表数据，当前判断为**{label}**；检索到 {data.get('total_hits') or 0} 条关键词命中。"
            "该结论是投稿定位建议，不代表当前编辑政策或录用承诺。"
        )
    return None


def try_operation_plan_answer(state: JournalState) -> Optional[str]:
    """Compose every completed structured operation; never short-circuit."""
    results = list(state.get("operation_results") or [])
    coverage = dict(state.get("coverage_report") or {})
    if not results or coverage.get("needs_llm"):
        return None
    sections: List[str] = []
    for result in results:
        if result.get("status") != "complete":
            return None
        op_type = str(result.get("operation") or "")
        data = dict(result.get("data") or {})
        rendered = _render_new_operation(op_type, data)
        if not rendered:
            temp: JournalState = dict(state)  # type: ignore
            temp["sql_evidence"] = data
            temp["query_plan"] = {**(state.get("query_plan") or {}), "task": data.get("task") or op_type, "main_task": op_type}
            temp["operation_results"] = []
            rendered = try_author_template_answer(temp)
        if not rendered:
            return None
        if rendered not in sections:
            sections.append(rendered.strip())
    return "\n\n".join(sections) if sections else None


def ensure_answer_operation_coverage(text: str, state: JournalState) -> str:
    """Append evidence-backed fallbacks for operations omitted by synthesis."""
    from app.agents.coverage import assess_answer_coverage

    results = list(state.get("operation_results") or [])
    report = assess_answer_coverage(text, results)
    missing = set(report.get("missing_operation_ids") or [])
    if not missing:
        return text
    additions: List[str] = []
    for result in results:
        if result.get("op_id") not in missing:
            continue
        op_type = str(result.get("operation") or "")
        status = str(result.get("status") or "partial")
        data = dict(result.get("data") or {})
        if status == "complete":
            section = _render_new_operation(op_type, data)
            if not section:
                temp: JournalState = dict(state)  # type: ignore
                temp["sql_evidence"] = data
                temp["query_plan"] = {**(state.get("query_plan") or {}), "task": data.get("task") or op_type}
                temp["operation_results"] = []
                section = try_author_template_answer(temp)
            if section:
                additions.append(section)
        else:
            reasons = "；".join(result.get("missing_requirements") or []) or "现有数据不足以完整支持该目标"
            titles = {
                "topic_yearly": "主题年度变化",
                "topic_period_compare": "主题阶段对比",
                "representative_papers_by_topic": "主题代表论文",
                "topic_papers": "主题论文",
                "submission_guidance": "投稿建议",
                "coauthored_papers": "合作论文",
            }
            topics = list((state.get("query_plan") or {}).get("keywords") or [])
            scope = f"\n\n查询主题：{'、'.join(map(str, topics))}。" if topics else ""
            additions.append(
                f"## {titles.get(op_type, op_type)}{scope}\n\n证据不足：{reasons}。"
                "基于已有相邻证据只能作为可能趋势或候选判断，不能视为确定事实。"
            )
    return (text.rstrip() + "\n\n" + "\n\n".join(additions)).strip() if additions else text


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
    if state.get("journal_id"):
        bind_corpus(state.get("journal_id"))
    settings = get_settings()
    question = state.get("question") or ""
    intents = state.get("intents") or []
    reason = state.get("route_reason") or ""
    evidence = state.get("evidence_text") or "（无证据）"
    history: List[Dict[str, str]] = state.get("history") or []
    plan = state.get("query_plan") or {}
    focus = plan.get("focus") or ""
    task = plan.get("task") or ""
    analysis = state.get("analysis_plan") or {}
    analysis_shape = analysis.get("answer_shape") or plan.get("analysis_shape") or ""
    ledger = build_evidence_ledger(state)
    operation_context = json.dumps(
        {
            "operations": state.get("operation_results") or [],
            "coverage": state.get("coverage_report") or {},
        },
        ensure_ascii=False,
        default=str,
    )

    # intents/reason are for model grounding only — never ask it to echo them.
    user_prompt = (
        f"用户问题: {question}\n"
        f"（内部参考，勿写入回答）任务={task}; shape={analysis_shape}; "
        f"证据通道: {intents}; {reason}\n"
        f"{('任务焦点: ' + focus + chr(10)) if focus else ''}"
        f"\n以下是各 Agent 汇总证据:\n{evidence}\n\n"
        f"{ledger}\n\n"
        f"[必须逐项覆盖的 Operation 及验收状态]\n{operation_context}\n\n"
        f"{_build_instruction(intents, evidence, task, analysis)}\n"
        "必须紧扣用户问题与分析规划做综合推理与升维总结；"
        "必须按 operation 顺序逐项作答，任何 partial/unsupported/error 都要明确说明。"
        "允许基于相邻证据作谨慎推断，但必须显式使用“可能”“推测”或“候选”等标记；"
        "推断不得新增数字、作者、机构、论文、年份或 DOI。"
        "禁止答非所问、套用无关全刊概览，或把证据复述成数据报表。"
        "只输出面向用户的最终答案，不要输出路由、意图、分析规划原文或修正说明。"
    )
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(settings.journal_title)}
    ]
    # chat_max_history counts Q&A turns (pairs); keep that many pairs in context
    max_h = settings.chat_max_history
    for item in history[-(max_h * 2) :]:
        messages.append(item)
    messages.append({"role": "user", "content": user_prompt})
    return messages


def _chunk_text(text: str, size: int = 48) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _generation_fallback(state: JournalState, error: Exception) -> str:
    """Return accepted structured evidence when optional synthesis is unavailable."""
    errors = list(state.get("errors") or [])
    errors.append(f"generation_degraded: {type(error).__name__}: {error}"[:500])
    state["errors"] = errors
    answer = ensure_answer_operation_coverage("", state)
    note = "\n\n> 生成模型暂不可用；以上按现有结构化证据直接呈现，综合解释可能不完整。"
    return (answer.rstrip() + note).strip()


def stream_generate(state: JournalState) -> Iterator[str]:
    templated = try_operation_plan_answer(state) or try_author_template_answer(state)
    if templated:
        raw = templated
    else:
        chat = MiniMaxChat(get_settings())
        # The model response is buffered; the caller only sees chunks after
        # coverage and evidence gates have accepted (or replaced) the answer.
        try:
            raw = chat.chat(build_generate_messages(state), max_tokens=8192)
        except Exception as error:  # optional wording must not discard accepted evidence
            raw = _generation_fallback(state, error)
    answer = finalize_answer(raw, state.get("intents") or [], state)
    yield from _chunk_text(answer)


def finalize_answer(
    raw: str,
    intents: List[str],
    state: Optional[JournalState] = None,
) -> str:
    text = _cleanup_answer(raw or "", intents)
    if state is not None:
        text = enforce_evidence_constraints(text, state)
        text = ensure_answer_operation_coverage(text, state)
        from app.agents.coverage import assess_answer_coverage, assess_quality

        quality = assess_quality(text, state)
        unsafe = any(
            str(violation).startswith(
                (
                    "unsupported_doi:",
                    "unsupported_number:",
                    "unsupported_entity:",
                    "unsupported_paper_title:",
                    "unsupported_paper_year:",
                    "answer_missing_operation:",
                )
            )
            for violation in quality.get("hard_gate_violations") or []
        )
        if unsafe:
            fallback = ensure_answer_operation_coverage("", state)
            text = enforce_evidence_constraints(fallback, state)
            quality = assess_quality(text, state)
        state["answer_coverage"] = assess_answer_coverage(
            text, state.get("operation_results") or []
        )
        state["quality_report"] = quality
    return text


def generate_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    templated = try_operation_plan_answer(state) or try_author_template_answer(state)
    if templated:
        return {"answer": finalize_answer(templated, intents, state)}
    chat = MiniMaxChat(get_settings())
    try:
        answer = chat.chat(build_generate_messages(state), max_tokens=8192)
    except Exception as error:  # keep the non-streaming graph equally resilient
        answer = _generation_fallback(state, error)
    answer = finalize_answer(answer, intents, state)
    return {"answer": answer}
