#!/usr/bin/env python3
"""解析浙江大学学报（人文社会科学版）JATS XML，支持单篇或批量导出 CSV。"""

from __future__ import annotations

import argparse
import csv
import xml.etree.ElementTree as ET
from pathlib import Path

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


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


def format_person_name(name: ET.Element, lang: str) -> str:
    surname = text_of(find_first(name, "surname"))
    given = text_of(find_first(name, "given-names"))
    if lang == "zh":
        return f"{surname}{given}"
    return f"{surname} {given}".strip()


def bilingual_pair(
    primary_text: str,
    primary_lang: str,
    translated_text: str,
    translated_lang: str | None,
) -> tuple[str, str]:
    """按主语言/译文语言拆成 (zh, en)。"""
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


def author_pair(contrib: ET.Element, primary_lang: str) -> tuple[str, str]:
    zh, en = "", ""
    for name in find_all(contrib, "name"):
        lang = elem_lang(name) or primary_lang
        value = format_person_name(name, lang)
        if not value:
            continue
        if lang == "zh":
            zh = value
        else:
            en = value
    return zh, en


def is_article_xml(xml_path: Path, root: ET.Element | None = None) -> bool:
    """跳过整期 issue-xml。"""
    name = xml_path.name.lower()
    if ".issue-" in name or name.endswith(".issue.xml"):
        return False
    if root is not None and local_tag(root) != "article":
        return False
    return True


def parse_article(xml_path: Path) -> dict[str, str]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    if not is_article_xml(xml_path, root):
        raise ValueError(f"非单篇 article XML，已跳过: {xml_path.name}")

    journal_meta = find_first(root, "journal-meta")
    article_meta = find_first(root, "article-meta")
    if journal_meta is None or article_meta is None:
        raise ValueError(f"XML 缺少 journal-meta 或 article-meta: {xml_path}")

    primary_lang = elem_lang(root) or "zh"

    doi = ""
    publisher_id = ""
    for article_id in find_all(article_meta, "article-id"):
        pub_type = article_id.attrib.get("pub-id-type", "")
        if pub_type == "doi":
            doi = text_of(article_id)
        elif pub_type == "publisher-id":
            publisher_id = text_of(article_id)

    clc_subjects: list[str] = []
    dc_subjects: list[str] = []
    for group in find_all(article_meta, "subj-group"):
        group_type = group.attrib.get("subj-group-type", "")
        subjects = [text_of(s) for s in group if local_tag(s) == "subject" and text_of(s)]
        if group_type == "clc":
            clc_subjects.extend(subjects)
        elif group_type == "dc":
            dc_subjects.extend(subjects)

    authors_zh: list[str] = []
    authors_en: list[str] = []
    emails: list[str] = []
    bios: list[str] = []
    for contrib in find_all(article_meta, "contrib"):
        if contrib.attrib.get("contrib-type") != "author":
            continue
        zh, en = author_pair(contrib, primary_lang)
        if zh:
            authors_zh.append(zh)
        if en:
            authors_en.append(en)
        for email in find_all(contrib, "email"):
            value = text_of(email)
            if value and value not in emails:
                emails.append(value)
        for bio in find_all(contrib, "bio"):
            value = text_of(bio)
            if value:
                bios.append(value)

    affs_zh: list[str] = []
    affs_en: list[str] = []
    for aff in find_all(article_meta, "aff"):
        lang = elem_lang(aff) or primary_lang
        value = text_of(aff)
        if not value:
            continue
        if lang == "zh":
            affs_zh.append(value)
        else:
            affs_en.append(value)

    keywords_zh: list[str] = []
    keywords_en: list[str] = []
    for group in find_all(article_meta, "kwd-group"):
        lang = elem_lang(group) or primary_lang
        words = [text_of(k) for k in group if local_tag(k) == "kwd" and text_of(k)]
        if lang == "zh":
            keywords_zh.extend(words)
        else:
            keywords_en.extend(words)

    funding_sources: list[str] = []
    award_ids: list[str] = []
    for award in find_all(article_meta, "award-group"):
        source = text_of(find_first(award, "funding-source"))
        award_id = text_of(find_first(award, "award-id"))
        if source:
            funding_sources.append(source)
        if award_id:
            award_ids.append(award_id)

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

    pdf_uri = ""
    for uri in find_all(article_meta, "self-uri"):
        if uri.attrib.get("content-type") == "pdf":
            pdf_uri = uri.attrib.get("{http://www.w3.org/1999/xlink}href", "") or uri.attrib.get(
                "href", ""
            )
            break

    title_primary = text_of(find_first(article_meta, "article-title"))
    trans_title_elem = find_first(article_meta, "trans-title")
    trans_title_group = find_first(article_meta, "trans-title-group")
    title_zh, title_en = bilingual_pair(
        title_primary,
        primary_lang,
        text_of(trans_title_elem),
        elem_lang(trans_title_group) or elem_lang(trans_title_elem),
    )

    abstract_primary = text_of(find_first(article_meta, "abstract"))
    trans_abstract = find_first(article_meta, "trans-abstract")
    abstract_zh, abstract_en = bilingual_pair(
        abstract_primary,
        primary_lang,
        text_of(trans_abstract),
        elem_lang(trans_abstract),
    )

    # 期刊名：无 lang 视为中文，en 为英文（与期次无关）
    journal_title_zh = ""
    journal_title_en = ""
    for title in find_all(journal_meta, "journal-title"):
        lang = elem_lang(title)
        value = text_of(title)
        if lang == "en":
            journal_title_en = value
        else:
            journal_title_zh = value

    publisher_zh = ""
    publisher_en = ""
    for name in find_all(journal_meta, "publisher-name"):
        lang = elem_lang(name)
        value = text_of(name)
        if lang == "en":
            publisher_en = value
        else:
            publisher_zh = value

    return {
        "source_xml": xml_path.name,
        "journal_id": text_of(find_first(journal_meta, "journal-id")),
        "journal_title_zh": journal_title_zh,
        "journal_title_en": journal_title_en,
        "issn": text_of(find_first(journal_meta, "issn")),
        "cn": text_of(find_first(journal_meta, "cn")),
        "publisher_zh": publisher_zh,
        "publisher_en": publisher_en,
        "doi": doi,
        "publisher_article_id": publisher_id,
        "clc": "; ".join(clc_subjects),
        "document_type": "; ".join(dc_subjects),
        "title_zh": title_zh,
        "title_en": title_en,
        "authors_zh": "; ".join(authors_zh),
        "authors_en": "; ".join(authors_en),
        "emails": "; ".join(emails),
        "author_bio": " | ".join(bios),
        "affiliations_zh": "; ".join(affs_zh),
        "affiliations_en": "; ".join(affs_en),
        "pub_date": format_date(pub_date),
        "received_date": format_date(received_date),
        "volume": text_of(find_first(article_meta, "volume")),
        "issue": text_of(find_first(article_meta, "issue")),
        "fpage": text_of(find_first(article_meta, "fpage")),
        "lpage": text_of(find_first(article_meta, "lpage")),
        "pdf": pdf_uri,
        "abstract_zh": abstract_zh,
        "abstract_en": abstract_en,
        "keywords_zh": "; ".join(keywords_zh),
        "keywords_en": "; ".join(keywords_en),
        "funding_source": "; ".join(funding_sources),
        "award_id": "; ".join(award_ids),
    }


FIELDNAMES = [
    "source_xml",
    "journal_id",
    "journal_title_zh",
    "journal_title_en",
    "issn",
    "cn",
    "publisher_zh",
    "publisher_en",
    "doi",
    "publisher_article_id",
    "clc",
    "document_type",
    "title_zh",
    "title_en",
    "authors_zh",
    "authors_en",
    "emails",
    "author_bio",
    "affiliations_zh",
    "affiliations_en",
    "pub_date",
    "received_date",
    "volume",
    "issue",
    "fpage",
    "lpage",
    "pdf",
    "abstract_zh",
    "abstract_en",
    "keywords_zh",
    "keywords_en",
    "funding_source",
    "award_id",
]


def write_csv(rows: list[dict[str, str]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def collect_xml_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(p for p in input_path.glob("*.xml") if p.is_file())


def parse_many(xml_files: list[Path]) -> tuple[list[dict[str, str]], list[str], int]:
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    skipped = 0
    for xml_path in xml_files:
        if not is_article_xml(xml_path):
            skipped += 1
            continue
        try:
            rows.append(parse_article(xml_path))
        except Exception as e:  # noqa: BLE001 — 批量时记录并继续
            # 可能是 issue-xml 等非 article
            msg = str(e)
            if "非单篇" in msg or "缺少 journal-meta" in msg:
                skipped += 1
            else:
                errors.append(f"{xml_path.name}: {e}")
    return rows, errors, skipped


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="解析浙大学报 JATS XML 并导出 CSV")
    parser.add_argument(
        "input",
        nargs="?",
        default=str(root / "xmls"),
        help="单个 XML 文件，或含 XML 的目录（默认：./xmls）",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(root / "articles_metadata.csv"),
        help="输出 CSV 路径（默认：./articles_metadata.csv）",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = root / input_path
    if not input_path.exists():
        raise SystemExit(f"找不到输入路径: {input_path}")

    xml_files = collect_xml_files(input_path)
    if not xml_files:
        raise SystemExit(f"未找到 XML: {input_path}")

    rows, errors, skipped = parse_many(xml_files)

    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    write_csv(rows, output)

    print(f"输入: {input_path}（{len(xml_files)} 个 XML）")
    print(f"成功: {len(rows)} 篇")
    print(f"跳过: {skipped}（整期 issue 等）")
    print(f"失败: {len(errors)}")
    print(f"已写入: {output}")
    if errors:
        print("失败样例:")
        for line in errors[:20]:
            print(f"  {line}")
        if len(errors) > 20:
            print(f"  ... 另有 {len(errors) - 20} 条")


if __name__ == "__main__":
    main()
