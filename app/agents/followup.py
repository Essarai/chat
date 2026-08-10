"""Resolve short / referential follow-ups into standalone questions."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services.minimax_chat import MiniMaxChat

_FOLLOWUP_HINT = re.compile(
    r"(他|她|他们|她们|它|这个|那个|上述|刚才|继续|再|还有|呢\s*$|吗\s*$|"
    r"详细|具体|展开|为什么|怎么|如何|对比|换个|改成|基于上|"
    r"第一|第二|第三|前者|后者|同上)",
    re.I,
)

def looks_like_followup(question: str, history: Optional[List[Dict[str, str]]]) -> bool:
    q = (question or "").strip()
    if not q or not history:
        return False
    # Clear standalone journal queries should not be rewritten
    standalone = bool(
        re.search(
            r"(近\d+|过去\d+|发文|趋势|作者|机构|关键词|适合投|研究方向|合作|论文主题)",
            q,
        )
    )
    has_ref = bool(
        re.search(r"(他|她|他们|她们|其|这个|那个|上述|刚才|该作者|该机构|该主题|呢\s*$)", q)
    )
    if standalone and not has_ref and not _FOLLOWUP_HINT.search(q):
        return False
    if has_ref:
        return True
    if _FOLLOWUP_HINT.search(q) and len(q) <= 36:
        return True
    # Very short follow-ups like「详细说说」「还有呢」
    if len(q) <= 12:
        return True
    return False


def _context_text(history: List[Dict[str, str]], limit: int = 5000) -> str:
    lines = []
    for item in history[-8:]:
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    text = "\n".join(lines)
    return text[-limit:]


def classify_followup_question(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    chat: Optional[MiniMaxChat] = None,
) -> Dict[str, Any]:
    """Classify new-vs-follow-up questions using conversation context."""
    q = (question or "").strip()
    hist = history or []
    result: Dict[str, Any] = {
        "kind": "new_question",
        "referent": None,
        "selector": None,
        "action": None,
        "rewritten_question": q,
        "source": "fallback",
    }
    if not q or not hist:
        return result

    prompt = (
        "判断用户当前问题是新问题还是对上一轮结果的追问，并在追问时改写成完整问题。\n"
        "不要因为问题中出现‘作者’、‘论文’、‘机构’等名词就判定为新问题；"
        "要结合上下文判断‘第3位作者’、‘上述论文’、‘这个主题’等指代。\n"
        "只输出 JSON，不要 Markdown，字段必须为："
        "kind（new_question|followup|ambiguous）、referent（指代对象）、"
        "selector（序号/名称/条件，没有则 null）、action（用户想做的动作）、"
        "rewritten_question（完整问题；新问题时保持原问题）。\n\n"
        f"对话上下文：\n{_context_text(hist)}\n\n"
        f"当前问题：{q}\n"
    )
    try:
        chat = chat or MiniMaxChat(get_settings())
        raw = (chat.chat(
            [
                {
                    "role": "system",
                    "content": "你是对话上下文意图识别器，只输出合法 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=256,
            temperature=0.0,
        ) or "").strip()
        raw = raw.strip().removeprefix("```json").removesuffix("```").strip()
        parsed = json.loads(raw)
        if parsed.get("kind") in {"new_question", "followup", "ambiguous"}:
            result.update({k: parsed.get(k) for k in result if k in parsed})
            result["source"] = "llm"
    except Exception as e:
        result["error"] = str(e)[:200]
        # Generic fallback only: use the action/context signal, never a
        # special case for a particular ordinal or entity type.
        result["kind"] = "followup" if looks_like_followup(q, hist) else "new_question"
    return result


def resolve_followup_question(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    chat: Optional[MiniMaxChat] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> str:
    """Return a self-contained question based on contextual follow-up intent."""
    q = (question or "").strip()
    hist = history or []
    intent = analysis or classify_followup_question(q, hist, chat=chat)
    if intent.get("kind") not in {"followup", "ambiguous"}:
        return q

    rewritten = str(intent.get("rewritten_question") or "").strip()
    if 2 <= len(rewritten) <= 240 and rewritten != q:
        return rewritten

    lines = [
        f"{'用户' if item.get('role') == 'user' else '助手'}: "
        f"{str(item.get('content') or '').strip()[:800]}"
        for item in hist[-8:]
        if str(item.get("content") or "").strip()
    ]
    if not lines:
        return q

    prompt = (
        "将用户的追问改写成一句完整、可独立检索的中文问题。"
        "保留原意与指代对象（作者/机构/主题/年份等），不要回答问题，不要解释。"
        "只输出改写后的问题本身。\n\n"
        f"对话上下文:\n{chr(10).join(lines)}\n\n"
        f"用户追问: {q}\n\n改写:"
    )
    try:
        chat = chat or MiniMaxChat(get_settings())
        out = (chat.chat(
            [
                {
                    "role": "system",
                    "content": "你是追问改写器，只输出一句完整问题。",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=256,
            temperature=0.1,
        ) or "").strip()
        out = out.strip("「」\"'").splitlines()[0].strip()
        if 2 <= len(out) <= 200 and out != q:
            return out
    except Exception:
        pass
    return q
