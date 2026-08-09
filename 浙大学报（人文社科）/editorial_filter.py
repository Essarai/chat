"""识别办刊通告 / 会议新闻 / 期刊自我宣传等非研究文献。"""

from __future__ import annotations

import re
from typing import Any, Optional

# 题名强规则：评奖、引证、办刊动态、会议召开类
EDITORIAL_TITLE_RE = re.compile(
    r"("
    r"本刊.*(奖|蝉联|入围|位居|排名|名列|入选|考核|率先|推出|收录|举办)|"
    r"本学报.*(位居|排名|名列|入选|收录|影响)|"
    r"《浙江大学学报[（(]人文社会[^》]*》.*(已于|各项数据|学术影响力|建立双重|被美国|入选|考核|网络版)|"
    r"浙大社科学报|"
    r"中国科技论文在线优秀期刊|"
    r"中国科技期刊引证报告|"
    r"中国学术期刊综合引证报告|"
    r"中国学术期刊影响因子|"
    r"世界学术期刊学术影响力|"
    r"最具国际影响力学术期刊|"
    r"期刊数字影响力|"
    r"百强社科期刊|"
    r"WAJCI|影响因子|"
    r"在线优先出版|"
    r"名刊工程|"
    r"学术不端文献检测|"
    r"编委会|征稿启事|更正声明|"
    r"优秀期刊.*一等奖|"
    r"(研讨会|学术会议|国际会议|论坛|年会).{0,12}(召开|举行|举办|开幕|落幕)|"
    r"(召开|举行|举办|开幕).{0,30}(研讨会|学术会议|国际会议|论坛|年会)|"
    r"(成功|顺利|隆重)(召开|举办|举行|开幕|落幕)|"
    r"圆满落幕|"
    r"成立仪式|"
    r"调研考察.*学报|司长调研|"
    r"全球推广协议|国际推广计划"
    r")",
    re.I,
)


def _title_blob(title_zh: str = "", title_en: str = "") -> str:
    return f"{title_zh or ''} {title_en or ''}".strip()


def editorial_reason(
    *,
    title_zh: str = "",
    title_en: str = "",
    author_count: Optional[int] = None,
    keyword_count: Optional[int] = None,
) -> Optional[str]:
    """若应剔除则返回原因码，否则 None。"""
    title = _title_blob(title_zh, title_en)
    if title and EDITORIAL_TITLE_RE.search(title):
        return "editorial_title"
    # 无作者且无关键词：多为会议新闻 / 办刊短讯（实证几乎无学术论文）
    if author_count is not None and keyword_count is not None:
        if author_count <= 0 and keyword_count <= 0:
            return "no_author_no_keyword"
    return None


def is_editorial_record(row: dict[str, Any]) -> tuple[bool, str]:
    """兼容 metadata / cleaned papers / 解析后的 article dict。"""
    title_zh = str(row.get("title_zh") or "")
    title_en = str(row.get("title_en") or "")

    if "author_count" in row or "keyword_count" in row:
        try:
            ac = int(row.get("author_count") or 0)
        except (TypeError, ValueError):
            ac = 0
        try:
            kc = int(row.get("keyword_count") or 0)
        except (TypeError, ValueError):
            kc = 0
    else:
        authors = row.get("authors")
        if isinstance(authors, list):
            ac = len(authors)
        else:
            # metadata: authors_zh 分号分隔
            az = str(row.get("authors_zh") or row.get("authors") or "").strip()
            ac = len([x for x in re.split(r"\s*;\s*", az) if x]) if az else 0
        kws = row.get("keywords")
        if isinstance(kws, list):
            kc = len(kws)
        else:
            kz = str(row.get("keywords_zh") or row.get("keywords") or "").strip()
            kc = len([x for x in re.split(r"\s*;\s*", kz) if x]) if kz else 0

    reason = editorial_reason(
        title_zh=title_zh,
        title_en=title_en,
        author_count=ac,
        keyword_count=kc,
    )
    return (reason is not None, reason or "")
