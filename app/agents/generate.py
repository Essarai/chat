from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional

from app.agents.state import JournalState
from app.config import get_settings
from app.services.minimax_chat import MiniMaxChat
from app.utils import doi_url

SYSTEM_PROMPT = """你是《浙江大学学报（农业与生命科学版）》的 AI 期刊知识助手。
请严格基于提供的多路证据（[SQL] / [KG] / [RAG]）回答。

通用要求：
1. 中文，结论优先，简洁准确；可用 Markdown（标题、加粗、表格）。
2. 凡列举论文、合作者、机构，必须使用 Markdown 有序列表，每条一行：
   `1. **名称**（补充信息）：详情`
   禁止写成「姓名」下一行再写「- 所属机构: …」这种无序号格式。
3. 数字必须严格来自证据；禁止把【全刊统计】当成某位作者的个人数据。
4. 若证据标明【作者个人统计】，所有发文量/关键词/基金数字只能用该块。
5. 不要编造论文、DOI、链接或机构人数。
6. 禁止输出「参考文献」「参考资料」章节与 HTML。
7. 禁止提及路由、意图、Agent、SQL/KG/RAG 管道等内部实现信息。

按问题类型：
- 作者发文：论文用有序列表，证据中的论文全部列出。
- 合作者/所属机构：用有序列表，格式 `1. **姓名**（N篇）：机构A / 机构B`。
- 链接：DOI 用纯文本；全文用 `[查看全文](url)`。
- 机构名称若明显为同一单位不同写法，可合并表述并说明。
"""


def _build_instruction(intents: List[str], evidence: str = "") -> str:
    has_rag = "rag" in intents
    only_struct = bool(intents) and not has_rag
    author_scoped = "【作者个人统计" in (evidence or "")
    if author_scoped:
        return (
            "本题为作者个人/合作者结构化统计。数字必须来自【作者个人统计】证据；"
            "列举论文或合作者时必须用有序序号 1. 2. 3.，每条一行，禁止「姓名」下挂「- 所属机构」；"
            "合作者格式：`1. **姓名**（N篇）：机构A / 机构B`；"
            "论文须全部列出；DOI 纯文本；链接用 [查看全文](url)；禁止 HTML。"
        )
    if only_struct:
        return (
            "本题为结构化分析（SQL/KG）。请直接回答结论与分布；"
            "凡名单/分布必须用有序序号 1. 2. 3.，每条一行；"
            "不要写参考文献；DOI 用纯文本。"
        )
    if has_rag and len(intents) == 1:
        return (
            "本题为文献内容问答。请基于 [RAG] 证据作答；"
            "文末可列相关论文（题名、DOI 纯文本、证据中的链接），不要空的参考文献，不要 HTML。"
        )
    return (
        "本题可能同时涉及结构证据与文献。优先用 SQL/KG 回答关系/统计部分；"
        "仅当证据中确有相关论文时再附 DOI/链接；DOI 用纯文本；不要 HTML。"
    )


def _fix_markdown_bold_glitches(text: str) -> str:
    """Repair common model bold mistakes like `**title**（2025）**`."""
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
    # dangling ** at end of line
    text = re.sub(r"\*\*(?=\s*$)", "", text, flags=re.M)
    return text


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
        if re.match(r"^#{1,4}\s+", line) or re.match(
            r"^(主要合作者|合作者机构|机构分布|全部发文|相关论文|关键词含)",
            line.strip(),
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


def try_author_template_answer(state: JournalState) -> Optional[str]:
    """
    Format author papers / collaborators directly from SQL evidence.
    Avoids LLM truncation and broken ** markers on long lists.
    """
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

    papers = sql.get("papers") or sql.get("recent_papers") or []
    collab = sql.get("collaborators") or {}
    inst = sql.get("collaborator_institutions") or {}
    people = inst.get("collaborators") or collab.get("collaborators") or []
    if not papers and not people:
        return None

    author = sql.get("author") or {}
    name = author.get("name_zh") or sql.get("author_name") or "该作者"
    q = state.get("question") or ""

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
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_generate_messages(state: JournalState) -> List[Dict[str, str]]:
    settings = get_settings()
    question = state.get("question") or ""
    intents = state.get("intents") or []
    reason = state.get("route_reason") or ""
    evidence = state.get("evidence_text") or "（无证据）"
    history: List[Dict[str, str]] = state.get("history") or []

    # intents/reason are for model grounding only — never ask it to echo them.
    user_prompt = (
        f"用户问题: {question}\n"
        f"（内部参考，勿写入回答）证据通道: {intents}; {reason}\n\n"
        f"以下是各 Agent 汇总证据:\n{evidence}\n\n"
        f"{_build_instruction(intents, evidence)}\n"
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
        yield from _chunk_text(templated)
        return
    chat = MiniMaxChat(get_settings())
    yield from chat.chat_stream(
        build_generate_messages(state),
        max_tokens=8192,
    )


def finalize_answer(raw: str, intents: List[str]) -> str:
    return _cleanup_answer(raw or "", intents)


def generate_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    templated = try_author_template_answer(state)
    if templated:
        return {"answer": templated}
    chat = MiniMaxChat(get_settings())
    answer = chat.chat(build_generate_messages(state), max_tokens=8192)
    answer = finalize_answer(answer, intents)
    return {"answer": answer}
