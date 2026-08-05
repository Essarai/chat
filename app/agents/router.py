from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from app.agents.state import JournalState
from app.config import get_settings
from app.services.minimax_chat import MiniMaxChat

SQL_RE = re.compile(
    r"(趋势|研究趋势|热门|热词|发文量|全部发文|发文情况|发文概况|年份|年度|统计|分布|排名|top|基金|资助|多少篇|数量|概况|"
    r"关键词.*(包含|含有)|包含.*关键词|主题词)",
    re.I,
)
KG_RE = re.compile(
    r"(合作|合著|共著|知识图谱|关系|邻域|机构|合作者|作者网络|collaborat)",
    re.I,
)
RAG_RE = re.compile(
    r"(研究|综述|内容|方法|结论|进展|讲了什么|如何|怎么|相关有哪些|文献)",
    re.I,
)


def rule_route(question: str, entities: Dict[str, Any]) -> tuple[List[str], str]:
    intents: List[str] = []
    reasons: List[str] = []

    if SQL_RE.search(question):
        intents.append("sql")
        reasons.append("统计/趋势类关键词")
    if KG_RE.search(question):
        intents.append("kg")
        reasons.append("关系/合作/机构类关键词")
    # author + institution/collaborators without explicit sql words still may need sql for institution agg
    if entities.get("author_name") and re.search(r"机构|单位|哪里|所属", question):
        if "kg" not in intents:
            intents.append("kg")
            reasons.append("作者+机构探索")
        if "sql" not in intents:
            intents.append("sql")
            reasons.append("机构分布需结构化聚合")

    if RAG_RE.search(question) and not intents:
        intents.append("rag")
        reasons.append("内容理解类关键词")
    elif RAG_RE.search(question) and "sql" not in intents and "kg" not in intents:
        intents.append("rag")
        reasons.append("内容理解类关键词")
    elif not intents:
        # default content search
        intents.append("rag")
        reasons.append("默认内容检索")

    # multi: keep all unique
    seen = []
    for i in intents:
        if i not in seen:
            seen.append(i)
    return seen, "; ".join(reasons)


def llm_route(question: str, chat: MiniMaxChat) -> tuple[List[str], str]:
    prompt = (
        "判断期刊知识助手问题应走哪些数据源，只返回 JSON："
        '{"intents":["sql"|"kg"|"rag",...],"reason":"..."}\n'
        "规则：统计分析→sql；合作/机构关系→kg；论文内容理解→rag；可多选。\n"
        f"问题：{question}"
    )
    raw = chat.chat(
        [
            {"role": "system", "content": "你是意图分类器，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=200,
    )
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    intents = [i for i in data.get("intents") or [] if i in {"sql", "kg", "rag"}]
    if not intents:
        intents = ["rag"]
    return intents, data.get("reason") or "llm"


def _is_keyword_author_question(question: str, entities: Dict[str, Any]) -> bool:
    if not (
        re.search(r"作者|哪些人|谁", question)
        and re.search(r"关键词|主题词|包含|含有", question)
    ):
        return False
    if entities.get("keywords"):
        return True
    return bool(re.search(r"[“\"‘'][^”\"’']+[”\"’']", question))


def router_node(state: JournalState) -> Dict[str, Any]:
    question = state.get("question") or ""
    entities = state.get("entities") or {}
    meta = entities.get("extract_meta") or {}

    # Structured keyword→author lookup must never fall through to RAG
    if _is_keyword_author_question(question, entities):
        reason = "关键词检索作者→sql"
        if meta.get("fixes"):
            reason += f"；实体修正: {meta.get('fixes')}"
        return {"intents": ["sql"], "route_reason": reason}

    # Prefer intents confirmed together with entity LLM fix
    confirmed = list(entities.get("confirmed_intents") or [])
    if confirmed:
        # Guardrails for author-centric questions
        if entities.get("author_name"):
            if re.search(r"机构|单位|哪里|所属|来自", question):
                if "kg" not in confirmed:
                    confirmed.insert(0, "kg")
                if "sql" not in confirmed:
                    confirmed.append("sql")
            if re.search(r"发文|统计|趋势|概况|情况|基金|关键词|合作者", question):
                if "sql" not in confirmed:
                    confirmed.append("sql")
                # Avoid RAG noise: semantic hits are often unrelated papers
                if re.search(r"发文|概况|情况|合作者|机构|统计|基金", question):
                    confirmed = [i for i in confirmed if i != "rag"]
        reason = "LLM确认意图"
        if meta.get("fixes"):
            reason += f"；实体修正: {meta.get('fixes')}"
        return {"intents": confirmed, "route_reason": reason}

    intents, reason = rule_route(question, entities)

    # ambiguous: only rag from default but question looks relational+stat without clear tags
    ambiguous = (
        len(intents) == 1
        and intents[0] == "rag"
        and (SQL_RE.search(question) is None)
        and bool(entities.get("author_name") or KG_RE.search(question))
    )
    if ambiguous:
        try:
            settings = get_settings()
            intents, reason = llm_route(question, MiniMaxChat(settings))
            reason = f"llm补充: {reason}"
        except Exception as e:
            reason = f"{reason}; llm失败({e})"

    if entities.get("author_name") and re.search(
        r"发文|概况|情况|合作者|机构|统计|基金", question
    ):
        intents = [i for i in intents if i != "rag"]
        if "sql" not in intents:
            intents.append("sql")

    if meta.get("fixes"):
        reason = f"{reason}; 实体修正: {meta.get('fixes')}"
    return {"intents": intents, "route_reason": reason}
