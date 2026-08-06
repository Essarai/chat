#!/usr/bin/env python3
"""从 xmls/ 清洗浙大学报（人文社科）元数据，导出知识图谱友好的规范表到 cleaned/。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# 资助机构归一：按优先级匹配（人文社科常见项靠前）
FUND_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"国家社科|国家社会科学基金"), "国家社会科学基金"),
    (re.compile(r"教育部.*人文|人文社会科学研究|人文社科"), "教育部人文社会科学研究项目"),
    (re.compile(r"浙江省哲社|浙江省社会科学|省社科"), "浙江省哲学社会科学规划项目"),
    (re.compile(r"中国博士后科学基金"), "中国博士后科学基金"),
    (re.compile(r"国家自然科学基金"), "国家自然科学基金"),
    (re.compile(r"浙江省自然科学基金"), "浙江省自然科学基金"),
    (re.compile(r"国家重点研发计划"), "国家重点研发计划"),
    (re.compile(r"浙江省重点研发计划"), "浙江省重点研发计划"),
    (re.compile(r"863|高技术研究发展计划"), "国家高技术研究发展计划(863)"),
    (re.compile(r"973|国家重点基础研究发展计划"), "国家重点基础研究发展计划(973)"),
    (re.compile(r"社会科学基金"), "国家社会科学基金"),
    (re.compile(r"教育部"), "教育部科研项目"),
    (re.compile(r"浙江省科技"), "浙江省科技计划"),
]

# 关键词轻量同义（保守；人文版暂无大规模同义表）
KEYWORD_ALIASES: dict[str, str] = {}


def local_tag(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def text_of(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def elem_lang(elem: ET.Element | None) -> str | None:
    if elem is None:
        return None
    return elem.attrib.get(XML_LANG)


def find_first(parent: ET.Element, tag: str) -> ET.Element | None:
    for child in parent.iter():
        if local_tag(child) == tag:
            return child
    return None


def find_all(parent: ET.Element, tag: str) -> list[ET.Element]:
    return [child for child in parent.iter() if local_tag(child) == tag]


def format_date(date_elem: ET.Element | None) -> str:
    if date_elem is None:
        return ""
    year = text_of(find_first(date_elem, "year"))
    month = text_of(find_first(date_elem, "month"))
    day = text_of(find_first(date_elem, "day"))
    month = month.zfill(2) if month else ""
    day = day.zfill(2) if day else ""
    return "-".join(p for p in (year, month, day) if p)


def clean_space(s: str) -> str:
    s = s.replace("\u3000", " ").replace("　", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def strip_aff_label(s: str) -> str:
    s = clean_space(s)
    s = re.sub(r"^\d+\s*[\.、．\)）]\s*", "", s)
    s = re.sub(r"^[\(（]?\d+[\)）]\s*", "", s)
    return s.strip(" ,，;；")


def normalize_institution(raw: str) -> dict[str, str]:
    raw_clean = strip_aff_label(raw)
    postcode = ""
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", raw_clean)
    if m:
        postcode = m.group(1)

    # 去掉邮编后的尾巴再取机构主名
    without_pc = re.sub(r"(?<!\d)\d{6}(?!\d)", "", raw_clean)
    without_pc = clean_space(without_pc).strip(" ,，;；/")

    # 粗提机构：取第一个「大学/学院/研究所/研究院/中心/医院/公司」片段优先
    org_name = without_pc
    for sep in ["，", ",", "/", "；", ";"]:
        if sep in without_pc:
            # 若首段太短（如仅省名），保留更长主段
            parts = [p.strip() for p in without_pc.split(sep) if p.strip()]
            if parts:
                org_name = parts[0]
                for p in parts:
                    if any(k in p for k in ("大学", "学院", "研究所", "研究院", "中心", "Univ", "University", "Institute", "College")):
                        org_name = p
                        break
            break

    org_name = clean_space(org_name)
    key_src = re.sub(r"[\s\-–,，.。/、]+", "", org_name).lower()
    inst_id = "inst_" + hashlib.md5(key_src.encode("utf-8")).hexdigest()[:12]
    return {
        "institution_id": inst_id,
        "name_raw": raw,
        "name_norm": org_name,
        "full_norm": without_pc,
        "postcode": postcode,
    }


def normalize_fund(raw: str) -> dict[str, str]:
    raw_clean = clean_space(raw)
    agency = raw_clean
    program_type = ""
    for pattern, name in FUND_RULES:
        if pattern.search(raw_clean):
            agency = name
            break

    if "青年" in raw_clean:
        program_type = "青年"
    elif "重点" in raw_clean and "研发" not in agency:
        program_type = "重点"
    elif "面上" in raw_clean:
        program_type = "面上"
    elif "联合" in raw_clean:
        program_type = "联合"

    key = f"{agency}|{program_type}"
    fund_id = "fund_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    return {
        "fund_id": fund_id,
        "funding_raw": raw_clean,
        "agency_norm": agency,
        "program_type": program_type,
    }


def normalize_keyword(raw: str) -> str:
    s = clean_space(raw)
    s = s.strip("；;，,。. ")
    s = KEYWORD_ALIASES.get(s, s)
    return s


def person_name(name_elem: ET.Element, lang: str) -> str:
    surname = text_of(find_first(name_elem, "surname"))
    given = text_of(find_first(name_elem, "given-names"))
    if lang == "zh":
        return f"{surname}{given}"
    return clean_space(f"{surname} {given}")


def normalize_en_name(name: str) -> str:
    s = clean_space(name).upper()
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    return s


def author_id_from(name_zh: str, name_en: str, email: str) -> str:
    if email:
        key = "email|" + email.lower().strip()
    elif name_zh:
        key = "zh|" + re.sub(r"\s+", "", name_zh)
    elif name_en:
        key = "en|" + normalize_en_name(name_en)
    else:
        key = "unknown|" + hashlib.md5(b"x").hexdigest()[:8]
    return "auth_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def bilingual_pair(
    primary_text: str,
    primary_lang: str,
    translated_text: str,
    translated_lang: str | None,
) -> tuple[str, str]:
    zh, en = "", ""
    if primary_lang == "zh":
        zh = primary_text
    else:
        en = primary_text
    t_lang = translated_lang or ("en" if primary_lang == "zh" else "zh")
    if t_lang == "zh":
        zh = zh or translated_text
    else:
        en = en or translated_text
    return zh, en


def is_article_file(path: Path) -> bool:
    name = path.name.lower()
    return ".issue-" not in name and not name.endswith(".issue.xml")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_article(xml_path: Path) -> dict | None:
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return None
    if local_tag(root) != "article":
        return None

    journal_meta = find_first(root, "journal-meta")
    article_meta = find_first(root, "article-meta")
    if journal_meta is None or article_meta is None:
        return None

    primary_lang = elem_lang(root) or "zh"

    doi = ""
    publisher_id = ""
    for article_id in find_all(article_meta, "article-id"):
        pub_type = article_id.attrib.get("pub-id-type", "")
        if pub_type == "doi":
            doi = text_of(article_id)
        elif pub_type == "publisher-id":
            publisher_id = text_of(article_id)
    if not doi:
        return None

    clc = [
        text_of(s)
        for g in find_all(article_meta, "subj-group")
        if g.attrib.get("subj-group-type") == "clc"
        for s in g
        if local_tag(s) == "subject" and text_of(s)
    ]
    doc_type = [
        text_of(s)
        for g in find_all(article_meta, "subj-group")
        if g.attrib.get("subj-group-type") == "dc"
        for s in g
        if local_tag(s) == "subject" and text_of(s)
    ]

    # affiliations: aff-alternatives/@id or aff/@id
    aff_map: dict[str, dict[str, str]] = {}
    for aff_alt in find_all(article_meta, "aff-alternatives"):
        aff_id = aff_alt.attrib.get("id", "")
        if not aff_id:
            continue
        zh, en = "", ""
        for aff in aff_alt:
            if local_tag(aff) != "aff":
                continue
            lang = elem_lang(aff) or primary_lang
            value = text_of(aff)
            if lang == "zh":
                zh = value
            else:
                en = value
        # 若只有一种语言
        if not zh and not en:
            continue
        raw_for_norm = zh or en
        norm = normalize_institution(raw_for_norm)
        aff_map[aff_id] = {
            **norm,
            "name_zh": strip_aff_label(zh) if zh else "",
            "name_en": strip_aff_label(en) if en else "",
        }

    # 也处理孤立 aff（无 aff-alternatives）
    for aff in find_all(article_meta, "aff"):
        parent = None
        # ElementTree 无 getparent；用已收录的跳过
        aff_id = aff.attrib.get("id", "")
        if not aff_id or aff_id in aff_map:
            continue
        lang = elem_lang(aff) or primary_lang
        value = text_of(aff)
        norm = normalize_institution(value)
        aff_map[aff_id] = {
            **norm,
            "name_zh": strip_aff_label(value) if lang == "zh" else "",
            "name_en": strip_aff_label(value) if lang != "zh" else "",
        }

    authors = []
    for order, contrib in enumerate(
        [c for c in find_all(article_meta, "contrib") if c.attrib.get("contrib-type") == "author"],
        start=1,
    ):
        name_zh, name_en = "", ""
        for name in find_all(contrib, "name"):
            lang = elem_lang(name) or primary_lang
            value = person_name(name, lang)
            if not value:
                continue
            if lang == "zh":
                name_zh = value
            else:
                name_en = value

        emails = []
        for email in find_all(contrib, "email"):
            v = text_of(email).lower().strip()
            if v and v not in emails:
                emails.append(v)
        bio = text_of(find_first(contrib, "bio"))

        aff_ids = []
        for xref in contrib:
            if local_tag(xref) != "xref":
                continue
            if xref.attrib.get("ref-type") != "aff":
                continue
            rid = xref.attrib.get("rid", "")
            # rid 可能是 "AFF1 AFF2"
            for part in re.split(r"\s+", rid.strip()):
                if part and part not in aff_ids:
                    aff_ids.append(part)

        # 若无 xref 且全文仅一个单位，默认挂上
        if not aff_ids and len(aff_map) == 1:
            aff_ids = list(aff_map.keys())

        aid = author_id_from(name_zh, name_en, emails[0] if emails else "")
        authors.append(
            {
                "author_id": aid,
                "author_order": order,
                "name_zh": name_zh,
                "name_en": name_en,
                "email": emails[0] if emails else "",
                "emails": "; ".join(emails),
                "bio": bio,
                "aff_ids": aff_ids,
            }
        )

    title_zh, title_en = bilingual_pair(
        text_of(find_first(article_meta, "article-title")),
        primary_lang,
        text_of(find_first(article_meta, "trans-title")),
        elem_lang(find_first(article_meta, "trans-title-group"))
        or elem_lang(find_first(article_meta, "trans-title")),
    )
    abstract_zh, abstract_en = bilingual_pair(
        text_of(find_first(article_meta, "abstract")),
        primary_lang,
        text_of(find_first(article_meta, "trans-abstract")),
        elem_lang(find_first(article_meta, "trans-abstract")),
    )

    keywords_zh: list[str] = []
    keywords_en: list[str] = []
    for group in find_all(article_meta, "kwd-group"):
        lang = elem_lang(group) or primary_lang
        words = [normalize_keyword(text_of(k)) for k in group if local_tag(k) == "kwd"]
        words = [w for w in words if w]
        if lang == "zh":
            keywords_zh.extend(words)
        else:
            keywords_en.extend(words)

    # 中英关键词按位置对齐
    kw_pairs = []
    n = max(len(keywords_zh), len(keywords_en))
    for i in range(n):
        zh = keywords_zh[i] if i < len(keywords_zh) else ""
        en = keywords_en[i] if i < len(keywords_en) else ""
        if zh or en:
            kw_pairs.append((zh, en))

    awards = []
    for award in find_all(article_meta, "award-group"):
        source = clean_space(text_of(find_first(award, "funding-source")))
        award_id = clean_space(text_of(find_first(award, "award-id")))
        if not source and not award_id:
            continue
        fund = normalize_fund(source) if source else {
            "fund_id": "fund_unknown",
            "funding_raw": "",
            "agency_norm": "未知资助",
            "program_type": "",
        }
        awards.append({**fund, "award_id": award_id or ""})

    pub_date = None
    for date in find_all(article_meta, "pub-date"):
        if date.attrib.get("date-type") == "pub":
            pub_date = date
            break
    if pub_date is None:
        pub_date = find_first(article_meta, "pub-date")

    received_date = None
    for date in find_all(article_meta, "date"):
        if date.attrib.get("date-type") == "received":
            received_date = date
            break

    pdf = ""
    for uri in find_all(article_meta, "self-uri"):
        if uri.attrib.get("content-type") == "pdf":
            pdf = uri.attrib.get("{http://www.w3.org/1999/xlink}href", "") or uri.attrib.get("href", "")
            break

    return {
        "doi": doi,
        "source_xml": xml_path.name,
        "publisher_article_id": publisher_id,
        "title_zh": title_zh,
        "title_en": title_en,
        "abstract_zh": abstract_zh,
        "abstract_en": abstract_en,
        "pub_date": format_date(pub_date),
        "received_date": format_date(received_date),
        "volume": text_of(find_first(article_meta, "volume")),
        "issue": text_of(find_first(article_meta, "issue")),
        "fpage": text_of(find_first(article_meta, "fpage")),
        "lpage": text_of(find_first(article_meta, "lpage")),
        "pdf": pdf,
        "document_type": "; ".join(doc_type),
        "clc": clc,
        "authors": authors,
        "affiliations": aff_map,
        "keywords": kw_pairs,
        "awards": awards,
        "journal_id": _normalize_journal_id(
            text_of(find_first(journal_meta, "journal-id")),
            text_of(find_first(journal_meta, "issn")),
        ),
        "issn": text_of(find_first(journal_meta, "issn")),
        "journal_title_zh": _journal_title_zh(journal_meta),
    }


def _normalize_journal_id(journal_id: str, issn: str) -> str:
    """人文社科版统一为 ZDXBRWB（个别 XML 误标 ZJDX）。"""
    jid = (journal_id or "").strip()
    if issn.strip() == "1008-942X":
        return "ZDXBRWB"
    return jid or "ZDXBRWB"


def _journal_title_zh(journal_meta: ET.Element) -> str:
    """取中文刊名；无 lang 时默认中文。"""
    zh = ""
    fallback = ""
    for title in find_all(journal_meta, "journal-title"):
        value = text_of(title)
        if not value:
            continue
        lang = elem_lang(title)
        if lang == "en":
            continue
        if lang == "zh" or lang is None:
            zh = value
            break
        fallback = fallback or value
    return zh or fallback or "浙江大学学报（人文社会科学版）"


def merge_author_records(existing: dict, new: dict) -> dict:
    for key in ("name_zh", "name_en", "email"):
        if not existing.get(key) and new.get(key):
            existing[key] = new[key]
    # 合并邮箱集合
    emails = set()
    if existing.get("emails"):
        emails.update(x.strip() for x in existing["emails"].split(";") if x.strip())
    if new.get("emails"):
        emails.update(x.strip() for x in new["emails"].split(";") if x.strip())
    if new.get("email"):
        emails.add(new["email"])
    existing["emails"] = "; ".join(sorted(emails))
    if not existing.get("email") and emails:
        existing["email"] = sorted(emails)[0]
    existing["paper_count"] = existing.get("paper_count", 0) + 1
    return existing


def remap_author_ids(articles: list[dict]) -> None:
    """二次合并：同中文名且一方有邮箱时，统一到邮箱 author_id。"""
    zh_to_canonical: dict[str, str] = {}
    for art in articles:
        for a in art["authors"]:
            zh = re.sub(r"\s+", "", a["name_zh"] or "")
            if not zh or not a["email"]:
                continue
            # 有邮箱的作为规范 id
            zh_to_canonical[zh] = a["author_id"]

    for art in articles:
        for a in art["authors"]:
            zh = re.sub(r"\s+", "", a["name_zh"] or "")
            if zh and zh in zh_to_canonical:
                a["author_id"] = zh_to_canonical[zh]


def build_tables(articles: list[dict]) -> dict[str, list[dict]]:
    remap_author_ids(articles)

    papers = []
    authors: dict[str, dict] = {}
    institutions: dict[str, dict] = {}
    inst_papers: dict[str, set[str]] = defaultdict(set)
    fund_papers: dict[str, set[str]] = defaultdict(set)
    kw_papers: dict[str, set[str]] = defaultdict(set)
    paper_authors = []
    author_institutions = []
    paper_keywords = []
    keywords: dict[str, dict] = {}
    paper_clc = []
    funds: dict[str, dict] = {}
    paper_awards = []

    seen_author_inst = set()
    seen_paper_kw = set()
    seen_paper_clc = set()
    seen_paper_award = set()

    for art in articles:
        doi = art["doi"]
        papers.append(
            {
                "doi": doi,
                "source_xml": art["source_xml"],
                "publisher_article_id": art["publisher_article_id"],
                "title_zh": art["title_zh"],
                "title_en": art["title_en"],
                "abstract_zh": art["abstract_zh"],
                "abstract_en": art["abstract_en"],
                "pub_date": art["pub_date"],
                "received_date": art["received_date"],
                "year": art["pub_date"][:4] if art["pub_date"] else "",
                "volume": art["volume"],
                "issue": art["issue"],
                "fpage": art["fpage"],
                "lpage": art["lpage"],
                "pdf": art["pdf"],
                "document_type": art["document_type"],
                "issn": art["issn"],
                "journal_id": art["journal_id"],
                "journal_title_zh": art["journal_title_zh"],
                "author_count": len(art["authors"]),
                "keyword_count": len(art["keywords"]),
                "has_funding": "Y" if art["awards"] else "N",
            }
        )

        local_aff_to_inst: dict[str, str] = {}
        for aff_id, aff in art["affiliations"].items():
            iid = aff["institution_id"]
            local_aff_to_inst[aff_id] = iid
            if iid not in institutions:
                institutions[iid] = {
                    "institution_id": iid,
                    "name_norm": aff["name_norm"],
                    "full_norm": aff["full_norm"],
                    "name_zh": aff.get("name_zh", ""),
                    "name_en": aff.get("name_en", ""),
                    "postcode": aff.get("postcode", ""),
                    "paper_count": 0,
                }
            else:
                if not institutions[iid]["name_zh"] and aff.get("name_zh"):
                    institutions[iid]["name_zh"] = aff["name_zh"]
                if not institutions[iid]["name_en"] and aff.get("name_en"):
                    institutions[iid]["name_en"] = aff["name_en"]
            inst_papers[iid].add(doi)

        for a in art["authors"]:
            aid = a["author_id"]
            rec = {
                "author_id": aid,
                "name_zh": a["name_zh"],
                "name_en": a["name_en"],
                "email": a["email"],
                "emails": a["emails"],
                "paper_count": 1,
            }
            if aid in authors:
                authors[aid] = merge_author_records(authors[aid], rec)
            else:
                authors[aid] = rec

            paper_authors.append(
                {
                    "doi": doi,
                    "author_id": aid,
                    "author_order": a["author_order"],
                    "name_zh": a["name_zh"],
                    "name_en": a["name_en"],
                    "is_corresponding": "Y" if a["email"] else "N",
                    "institution_ids": "; ".join(
                        local_aff_to_inst[x] for x in a["aff_ids"] if x in local_aff_to_inst
                    ),
                    "aff_xml_ids": "; ".join(a["aff_ids"]),
                }
            )

            for aff_id in a["aff_ids"]:
                iid = local_aff_to_inst.get(aff_id)
                if not iid:
                    continue
                key = (aid, iid, doi)
                if key in seen_author_inst:
                    continue
                seen_author_inst.add(key)
                author_institutions.append(
                    {
                        "doi": doi,
                        "author_id": aid,
                        "institution_id": iid,
                        "author_order": a["author_order"],
                    }
                )

        for zh, en in art["keywords"]:
            kid = "kw_" + hashlib.md5(
                (("zh|" + zh) if zh else ("en|" + en.lower())).encode("utf-8")
            ).hexdigest()[:12]
            if kid not in keywords:
                keywords[kid] = {
                    "keyword_id": kid,
                    "label_zh": zh,
                    "label_en": en,
                    "paper_count": 0,
                }
            else:
                if not keywords[kid]["label_zh"] and zh:
                    keywords[kid]["label_zh"] = zh
                if not keywords[kid]["label_en"] and en:
                    keywords[kid]["label_en"] = en
            kw_papers[kid].add(doi)
            pk = (doi, kid)
            if pk not in seen_paper_kw:
                seen_paper_kw.add(pk)
                paper_keywords.append(
                    {
                        "doi": doi,
                        "keyword_id": kid,
                        "label_zh": zh,
                        "label_en": en,
                    }
                )

        for code in art["clc"]:
            code = clean_space(code)
            if not code:
                continue
            parent = code.split(".")[0] if "." in code else (code[:3] if len(code) > 3 else "")
            pc = (doi, code)
            if pc in seen_paper_clc:
                continue
            seen_paper_clc.add(pc)
            paper_clc.append({"doi": doi, "clc_code": code, "clc_parent": parent})

        for aw in art["awards"]:
            fid = aw["fund_id"]
            if fid not in funds:
                funds[fid] = {
                    "fund_id": fid,
                    "agency_norm": aw["agency_norm"],
                    "program_type": aw["program_type"],
                    "example_raw": aw["funding_raw"],
                    "paper_count": 0,
                }
            fund_papers[fid].add(doi)
            pa = (doi, aw.get("award_id", ""), fid)
            if pa in seen_paper_award:
                continue
            seen_paper_award.add(pa)
            paper_awards.append(
                {
                    "doi": doi,
                    "fund_id": fid,
                    "agency_norm": aw["agency_norm"],
                    "program_type": aw["program_type"],
                    "funding_raw": aw["funding_raw"],
                    "award_id": aw.get("award_id", ""),
                }
            )

    for iid, dois in inst_papers.items():
        institutions[iid]["paper_count"] = len(dois)
    for fid, dois in fund_papers.items():
        funds[fid]["paper_count"] = len(dois)
    for kid, dois in kw_papers.items():
        keywords[kid]["paper_count"] = len(dois)

    # 作者发文数按 paper_authors 重算，避免合并后偏差
    author_paper_counts: dict[str, set[str]] = defaultdict(set)
    for row in paper_authors:
        author_paper_counts[row["author_id"]].add(row["doi"])
    for aid, dois in author_paper_counts.items():
        if aid in authors:
            authors[aid]["paper_count"] = len(dois)

    return {
        "papers": papers,
        "authors": sorted(
            authors.values(),
            key=lambda x: (-x.get("paper_count", 0), x.get("name_zh") or x.get("name_en") or ""),
        ),
        "institutions": sorted(
            institutions.values(), key=lambda x: (-x["paper_count"], x["name_norm"])
        ),
        "paper_authors": paper_authors,
        "author_institutions": author_institutions,
        "keywords": sorted(
            keywords.values(),
            key=lambda x: (-x["paper_count"], x["label_zh"] or x["label_en"]),
        ),
        "paper_keywords": paper_keywords,
        "paper_clc": paper_clc,
        "funds": sorted(funds.values(), key=lambda x: (-x["paper_count"], x["agency_norm"])),
        "paper_awards": paper_awards,
    }


SCHEMAS = {
    "papers": [
        "doi", "source_xml", "publisher_article_id", "title_zh", "title_en",
        "abstract_zh", "abstract_en", "pub_date", "received_date", "year",
        "volume", "issue", "fpage", "lpage", "pdf", "document_type",
        "issn", "journal_id", "journal_title_zh", "author_count", "keyword_count", "has_funding",
    ],
    "authors": ["author_id", "name_zh", "name_en", "email", "emails", "paper_count"],
    "institutions": [
        "institution_id", "name_norm", "full_norm", "name_zh", "name_en", "postcode", "paper_count",
    ],
    "paper_authors": [
        "doi", "author_id", "author_order", "name_zh", "name_en",
        "is_corresponding", "institution_ids", "aff_xml_ids",
    ],
    "author_institutions": ["doi", "author_id", "institution_id", "author_order"],
    "keywords": ["keyword_id", "label_zh", "label_en", "paper_count"],
    "paper_keywords": ["doi", "keyword_id", "label_zh", "label_en"],
    "paper_clc": ["doi", "clc_code", "clc_parent"],
    "funds": ["fund_id", "agency_norm", "program_type", "example_raw", "paper_count"],
    "paper_awards": [
        "doi", "fund_id", "agency_norm", "program_type", "funding_raw", "award_id",
    ],
}


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="清洗浙大学报 XML，导出 KG 规范表")
    parser.add_argument("--xml-dir", default=str(root / "xmls"))
    parser.add_argument("--out-dir", default=str(root / "cleaned"))
    args = parser.parse_args()

    xml_dir = Path(args.xml_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    xml_files = sorted(p for p in xml_dir.glob("*.xml") if is_article_file(p))
    articles = []
    errors = 0
    for i, path in enumerate(xml_files, 1):
        art = parse_article(path)
        if art is None:
            errors += 1
            continue
        articles.append(art)
        if i % 500 == 0:
            print(f"已解析 {i}/{len(xml_files)} …")

    tables = build_tables(articles)
    for name, fields in SCHEMAS.items():
        write_csv(out_dir / f"{name}.csv", fields, tables[name])

    # 清洗报告
    report = out_dir / "README_cleaning.md"
    report.write_text(
        "\n".join(
            [
                "# 清洗结果说明",
                "",
                f"- 输入目录: `{xml_dir}`",
                f"- 单篇 XML: {len(xml_files)}",
                f"- 成功解析论文: {len(articles)}",
                f"- 跳过/失败: {errors}",
                "",
                "## 输出表",
                "",
                "| 文件 | 行数 | 用途 |",
                "|---|---:|---|",
                *[
                    f"| `{name}.csv` | {len(tables[name])} | KG 节点/边 |"
                    for name in SCHEMAS
                ],
                "",
                "## 清洗动作",
                "",
                "1. 从 XML 重建作者顺序、作者–单位 xref 关系",
                "2. 作者中英名合并；优先用 email 生成 author_id，否则用中文名/英文名",
                "3. 单位去序号前缀、统一空白，抽取邮编与 name_norm",
                "4. 基金映射到 agency_norm（国自然/省自然/重点研发等）",
                "5. 关键词去空白 + 少量同义归并；中英按位置对齐",
                "6. CLC、资助号拆成边表",
                "",
                "原始 `articles_metadata.csv` 未改动。",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(f"论文: {len(articles)}")
    print(f"作者: {len(tables['authors'])}")
    print(f"单位: {len(tables['institutions'])}")
    print(f"关键词: {len(tables['keywords'])}")
    print(f"基金: {len(tables['funds'])}")
    print(f"作者–论文边: {len(tables['paper_authors'])}")
    print(f"作者–单位边: {len(tables['author_institutions'])}")
    print(f"输出目录: {out_dir}")


if __name__ == "__main__":
    main()
