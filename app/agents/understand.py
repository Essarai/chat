from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.agents.state import JournalState
from app.config import get_settings
from app.services.minimax_chat import MiniMaxChat

YEAR_RANGE_RE = re.compile(r"(?:近|最近|过去|前)\s*(\d{1,2})\s*年")
YEAR_RANGE_CN_RE = re.compile(
    r"(?:近|最近|过去|前)\s*(两|二|三|四|五|六|七|八|九|十|十五|二十)\s*年"
)
_CN_YEAR_N = {
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十五": 15,
    "二十": 20,
}
YEAR_SPAN_RE = re.compile(r"(20\d{2})\s*[-~到至]\s*(20\d{2})")
# "和/与/跟 + 姓名 + 合作" 优先，避免把「和」吃进姓名
NAME_WITH_PREP_RE = re.compile(
    r"(?:和|与|跟)\s*([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?(?:有)?(?:合作关系|合作过|合作者|合作|合著|共著)"
)
NAME_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?的?(?:合作者|合作过|合作关系|合作|合著|共著|机构分布|机构)"
)
NAME_LOOSE_RE = re.compile(
    r"([一-龥]{2,4})(?:老师|教授)?(?:有合作关系|有合作|的合作|合作关系|合作过|合著|共著)"
)
AUTHOR_PREFIX_RE = re.compile(r"(?:作者|老师|教授)\s*([一-龥A-Za-z·]{2,12})")
# 刘建臻 在平台有发文吗 / 徐建明在本刊有没有论文（要求姓名与后续之间有间隔或“在/于”）
AUTHOR_HAS_PAPERS_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?"
    r"(?:\s+|(?=在|于))"
    r"(?:在|于)?(?:本刊|该刊|该期刊|期刊|学报|平台)?(?:上|中)?"
    r"(?:有没有|是否有|有无|有|是否)?"
    r"(?:发过文|发文|发表过|发表|论文)"
)
# 徐建明发文情况 / 朱军的发文统计（避免匹配「在平台有发文」）
AUTHOR_PROFILE_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?(?:的)?"
    r"(?:全部发文|发文情况|发文概况|发文统计|发文趋势|论文情况|科研情况|"
    r"发文量|发文数)"
)
# 徐建明在该期刊发表过哪些论文 / 徐建明的论文有哪些
AUTHOR_PAPERS_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?"
    r"(?:在|于).{0,16}(?:期刊|学报|本刊|该刊)?"
    r"(?:发表过|发表了|发表|刊发).{0,8}(?:哪些)?(?:论文|文章|文献)"
)
AUTHOR_PAPERS_LOOSE_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?(?:的)?"
    r"(?:全部)?(?:论文|文章)(?:有哪些|列表|清单)?"
)
AUTHOR_PUBLISHED_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?"
    r"(?:发表过|发表了|发表|刊发过).{0,8}(?:哪些|什么)?(?:论文|文章|文献)"
)
AUTHOR_TEAM_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?(?:的)?研究团队"
)
AUTHOR_TRAJECTORY_RE = re.compile(
    r"(?:分析|请分析)?([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?(?:的)?"
    r"(?:研究轨迹|学术轨迹|科研轨迹)"
)
AUTHOR_COLLAB_INST_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?(?:的)?合作(?:者|的作者)?.*"
    r"(?:所属)?(?:机构|单位)"
)
AUTHOR_COLLABORATOR_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?(?:的)?(?:主要)?"
    r"(?:合作伙伴|合作者)"
)
# 徐建明和施加春合作的发文 / 徐建明与施加春合著论文
AUTHOR_PAIR_RE = re.compile(
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?"
    r"\s*(?:和|与|跟)\s*"
    r"([一-龥A-Za-z·]{2,4})(?:老师|教授|研究员)?"
    r"(?:的)?(?:合作|合著|共著)"
)


def extract_year_window(question: str, default_last_n: Optional[int] = None):
    m = YEAR_SPAN_RE.search(question)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = YEAR_RANGE_RE.search(question)
    if m:
        n = int(m.group(1))
        end = datetime.now().year
        return end - n + 1, end
    m = YEAR_RANGE_CN_RE.search(question or "")
    if m:
        n = _CN_YEAR_N.get(m.group(1))
        if n:
            end = datetime.now().year
            return end - n + 1, end
    if default_last_n:
        end = datetime.now().year
        return end - default_last_n + 1, end
    return None, None


def _clean_name(name: str) -> str:
    name = re.sub(r"^(和|与|跟)", "", name or "")
    name = re.sub(r"(老师|教授|研究员|的|有)$", "", name)
    return name.strip()


_BAD_AUTHOR_NAMES = {
    "请问",
    "查询",
    "检索",
    "分析",
    "统计",
    "近十",
    "近五",
    "近三",
    "近两",
    "本刊",
    "哪些",
    "什么",
    "如何",
    "怎么",
    "浙江",
    "大学",
    "农业",
    "相关",
    "哪些相关",
    "热门",
    "主要",
    "核心",
    "代表",
    "表性",
    "表性研究",  # from「代表性研究机构」false positive
    "研究",
    "机构",
    "阶段",
    "历程",
    "方向",
    "演变",
    "趋势",
    "期刊",
    "学术",
    "发展",
    "该刊",
    "变化",
    "主题",
    "题变",
    "化和",
    "变化和",
    "题变化和",
    "作者变",
    "合作作",
    "团队",
    "领域",
    "水稻",
    "数量最多",
    "发文量",
    "发文数",
    "最多的",
    "平台",
    "在平台",
    "本刊",
    "该刊",
    "学报",
}


def _looks_like_person_name(name: str) -> bool:
    if not name or name in _BAD_AUTHOR_NAMES:
        return False
    if name.endswith("年") or re.match(r"^近\d", name):
        return False
    # reject abstract nouns commonly glued before 机构/研究
    if re.search(
        r"(研究|机构|单位|方向|趋势|历程|阶段|学科|期刊|论文|作者|核心|代表|表性|"
        r"演变|发展|变化|主题|领域|团队|网络|结构|全部|平台|情况|统计|数量|相关)",
        name,
    ):
        return False
    return 2 <= len(name) <= 4


def extract_author_pair(question: str) -> Optional[tuple]:
    """Return (name_a, name_b) for「A和B合作/合著…」questions."""
    m = AUTHOR_PAIR_RE.search(question or "")
    if not m:
        return None
    a = _clean_name(m.group(1))
    b = _clean_name(m.group(2))
    if not (_looks_like_person_name(a) and _looks_like_person_name(b)):
        return None
    if a == b:
        return None
    return a, b


def extract_author_name(question: str) -> Optional[str]:
    # Macro journal questions never imply a person author
    if re.search(
        r"(发展历程|演变趋势|研究方向|学术发展|过去\d+年|近\d+年).*(期刊|本刊|学报)?|"
        r"(核心作者|代表性研究机构|各阶段)",
        question or "",
    ) and not re.search(r"[一-龥]{2,4}(?:老师|教授)", question or ""):
        # still allow explicit「某某老师」below via patterns; skip loose 机构 matches
        pass

    # Pair questions are handled separately; do not collapse to one name.
    if extract_author_pair(question):
        return None

    for pattern in (
        AUTHOR_TRAJECTORY_RE,
        AUTHOR_TEAM_RE,
        AUTHOR_PAPERS_RE,
        AUTHOR_PUBLISHED_RE,
        AUTHOR_PAPERS_LOOSE_RE,
        AUTHOR_PROFILE_RE,
        AUTHOR_HAS_PAPERS_RE,
        AUTHOR_COLLABORATOR_RE,
        NAME_WITH_PREP_RE,
        AUTHOR_COLLAB_INST_RE,
        NAME_LOOSE_RE,
        NAME_RE,
        AUTHOR_PREFIX_RE,
    ):
        m = pattern.search(question.strip())
        if not m:
            continue
        name = _clean_name(m.group(1))
        if not _looks_like_person_name(name):
            continue
        # For NAME_RE (…机构), require real person context, not「代表性研究机构」
        if pattern is NAME_RE and re.search(r"代表性|研究机构|核心作者|主题变化|合作作者变化", question or ""):
            continue
        return name
    return None


def extract_keywords(question: str) -> list:
    stop = {
        "相关",
        "研究",
        "论文",
        "文献",
        "有哪些",
        "什么",
        "如何",
        "怎么",
        "请问",
        "一下",
        "哪些",
        "机构",
        "作者",
        "合作",
        "合著",
        "共著",
        "趋势",
        "热门",
        "关键词",
        "主题词",
        "近十年",
        "十年",
        "大多来自",
        "来自",
        "发布的文章",
        "包含了",
        "包含",
        "含有",
        "文章",
        "刊发",
    }
    out = []
    seen = set()

    # Prefer quoted terms: “番茄” / "番茄"
    for m in re.finditer(r"[“\"‘']([^”\"’']+)[”\"’']", question or ""):
        k = m.group(1).strip()
        if k and k not in stop and k not in seen and len(k) <= 20:
            seen.add(k)
            out.append(k)
    if out:
        return out[:5]

    cleaned = question or ""
    for s in stop:
        cleaned = cleaned.replace(s, " ")
    cleaned = re.sub(r"[？?，,。.!！、：:\s“”\"'‘’]+", " ", cleaned).strip()
    if cleaned and cleaned not in stop and len(cleaned) <= 20:
        out.append(cleaned)
    return out[:5]


def regex_extract(question: str, prior_dois: Optional[List[str]] = None) -> Dict[str, Any]:
    pair = extract_author_pair(question)
    author = extract_author_name(question)
    y0, y1 = extract_year_window(question)
    out: Dict[str, Any] = {
        "author_name": author,
        "year_start": y0,
        "year_end": y1,
        "keywords": extract_keywords(question) if not pair else [],
        "dois": list(prior_dois or []),
    }
    if pair:
        out["author_name"] = pair[0]
        out["author_name_b"] = pair[1]
        out["author_names"] = [pair[0], pair[1]]
    return out


def _parse_json_blob(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # tolerate leading/trailing prose
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _normalize_author(name: Any) -> Optional[str]:
    if name is None:
        return None
    name = _clean_name(str(name))
    if not name or name.lower() in {"null", "none", "无", "没有"}:
        return None
    if not (2 <= len(name) <= 8):
        return None
    return name


def _normalize_year(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        y = int(value)
    except (TypeError, ValueError):
        return None
    if 1900 <= y <= 2100:
        return y
    return None


def llm_confirm_entities(
    question: str,
    draft: Dict[str, Any],
    chat: Optional[MiniMaxChat] = None,
) -> Dict[str, Any]:
    """Confirm/fix regex draft entities (+ optional intents) via MiniMax."""
    chat = chat or MiniMaxChat(get_settings())
    prompt = f"""你是期刊问答系统的实体与意图校验器。下面是正则抽取的草稿，请对照用户问题确认或修正。
只输出 JSON，不要解释：
{{
  "author_name": "中文姓名或null",
  "author_name_b": "第二作者中文姓名或null",
  "year_start": 2017或null,
  "year_end": 2026或null,
  "keywords": ["主题词"],
  "intents": ["sql","kg","rag"]中的0-3个,
  "fixes": "修正说明，无修正则空字符串"
}}

规则：
1. author_name 只能是真实人名，不要带「和/与/跟/的/老师」等虚词。
2. 若问题没有明确作者，author_name=null。
3. 若问题是「A和B合作/合著的发文/论文」，必须同时填 author_name=A、author_name_b=B，intents=["sql"]，不要只保留一人。
4. intents 可多选：
   - 统计分析/趋势/发文量/热词 → sql
   - 某位作者的发文情况/个人统计 → sql（务必抽出 author_name）
   - 按关键词查作者/查哪些人发过某主题 → sql，keywords 填主题词（如「番茄」），不要 rag
   - 合作者/合著/关系网络 → kg
   - 论文内容/主题研究 → rag
   - 问合作者「来自哪些机构/单位分布」→ 必须同时包含 kg 和 sql
5. 年份仅在问题明确提到时填写；「近十年/过去20年」可换算为起止年。
6. 若问题是「某某发文情况/概况」，author_name=该人名，intents 至少含 sql。
7. keywords 只要核心主题词（如番茄），不要整句残留。
8. 问期刊发展历程/演变趋势/各阶段核心作者与机构时：author_name=null，intents 只用 sql，不要 rag。
9. 禁止把「代表性研究机构」误抽成作者名「表性研究」。

用户问题：{question}
正则草稿：{json.dumps(draft, ensure_ascii=False)}
"""
    raw = chat.chat(
        [
            {"role": "system", "content": "你只输出合法 JSON，用于校验实体与意图。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=400,
    )
    data = _parse_json_blob(raw)

    author = _normalize_author(data.get("author_name"))
    if author and not _looks_like_person_name(author):
        author = None
    # Macro overview questions: never keep a person author
    if re.search(
        r"(发展历程|演变趋势|学术发展|各阶段|核心作者|代表性研究机构)",
        question or "",
    ):
        author = None
    y0 = _normalize_year(data.get("year_start"))
    y1 = _normalize_year(data.get("year_end"))
    if y0 and y1 and y0 > y1:
        y0, y1 = y1, y0
    # Prefer regex year window for「过去/近 N 年」
    ry0, ry1 = extract_year_window(question)
    if ry0 and ry1 and re.search(r"(过去|近|最近)\s*\d+\s*年", question or ""):
        y0, y1 = ry0, ry1

    keywords = data.get("keywords")
    if not isinstance(keywords, list):
        keywords = draft.get("keywords") or []
    keywords = [str(k).strip().strip("“”\"'‘’") for k in keywords if str(k).strip()]
    # Prefer short quoted/regex keywords over LLM leftovers like整句片段
    draft_kws = [
        str(k).strip().strip("“”\"'‘’")
        for k in (draft.get("keywords") or [])
        if str(k).strip()
    ]
    if draft_kws and (
        not keywords
        or any(len(k) > 12 or (" " in k) or ("包含" in k) for k in keywords)
    ):
        keywords = draft_kws
    keywords = [k for k in keywords if k][:5]

    intents = [
        i for i in (data.get("intents") or []) if i in {"sql", "kg", "rag"}
    ]
    # Keyword→author questions: force sql
    if re.search(r"作者|哪些人|谁", question) and re.search(
        r"关键词|主题词|包含|含有", question
    ):
        intents = ["sql"]
    # dedupe preserve order
    seen = []
    for i in intents:
        if i not in seen:
            seen.append(i)

    out = {
        "author_name": author,
        "year_start": y0,
        "year_end": y1,
        "keywords": keywords,
        "dois": draft.get("dois") or [],
        "confirmed_intents": seen,
        "extract_meta": {
            "regex_draft": {
                "author_name": draft.get("author_name"),
                "author_name_b": draft.get("author_name_b"),
                "author_names": draft.get("author_names"),
                "year_start": draft.get("year_start"),
                "year_end": draft.get("year_end"),
                "keywords": draft.get("keywords"),
            },
            "llm_confirmed": True,
            "fixes": data.get("fixes") or "",
        },
    }
    # Preserve / restore pair fields — LLM must not collapse「A和B合作」to one author.
    return _restore_author_pair(question, out, draft)


def _restore_author_pair(
    question: str, entities: Dict[str, Any], draft: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    out = dict(entities or {})
    pair = extract_author_pair(question)
    if not pair:
        draft = draft or {}
        if draft.get("author_name_b") and draft.get("author_name"):
            pair = (draft["author_name"], draft["author_name_b"])
    if not pair:
        return out
    a, b = pair
    out["author_name"] = a
    out["author_name_b"] = b
    out["author_names"] = [a, b]
    # Pair coauthor paper questions are SQL intersection lookups.
    if re.search(r"发文|论文|著作|文章|文献|哪些", question or ""):
        intents = [i for i in (out.get("confirmed_intents") or []) if i in {"sql", "kg", "rag"}]
        if "sql" not in intents:
            intents = ["sql"] + intents
        out["confirmed_intents"] = intents
        out["keywords"] = []
    return out


def understand_node(state: JournalState) -> Dict[str, Any]:
    question = state.get("question") or ""
    prior_dois = []
    if state.get("entities"):
        prior_dois = state["entities"].get("dois") or []

    draft = regex_extract(question, prior_dois)
    try:
        entities = llm_confirm_entities(question, draft)
    except Exception as e:
        entities = dict(draft)
        entities["confirmed_intents"] = []
        entities["extract_meta"] = {
            "regex_draft": draft,
            "llm_confirmed": False,
            "fixes": f"llm确认失败，沿用正则: {e}",
        }
    entities = _restore_author_pair(question, entities, draft)
    return {"entities": entities}
