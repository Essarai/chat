#!/usr/bin/env python3
"""基于 articles_metadata.csv 做 RAG 语料清洗，输出到 rag_cleaned/。"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from editorial_filter import is_editorial_record


def clean_space(s: str) -> str:
    s = (s or "").replace("\u3000", " ").replace("　", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def clean_aff_field(s: str) -> str:
    """单位字段：去序号前缀、统一分隔。"""
    parts = []
    for raw in re.split(r"\s*;\s*", clean_space(s)):
        if not raw:
            continue
        raw = re.sub(r"^\d+\s*[\.、．\)）]\s*", "", raw)
        raw = re.sub(r"^[\(（]?\d+[\)）]\s*", "", raw)
        raw = clean_space(raw)
        if raw:
            parts.append(raw)
    return "; ".join(parts)


def clean_multi(s: str) -> str:
    parts = [clean_space(p) for p in re.split(r"\s*;\s*", s or "")]
    return "; ".join(p for p in parts if p)


def year_of(pub_date: str) -> str:
    pub_date = clean_space(pub_date)
    return pub_date[:4] if len(pub_date) >= 4 and pub_date[:4].isdigit() else ""


def pages_of(fpage: str, lpage: str) -> str:
    fpage, lpage = clean_space(fpage), clean_space(lpage)
    if fpage and lpage and fpage != lpage:
        return f"{fpage}-{lpage}"
    return fpage or lpage


def build_text(row: dict[str, str]) -> str:
    """供向量检索的主文本：题名/作者/单位/关键词/摘要/基金。"""
    title_zh = clean_space(row.get("title_zh", ""))
    title_en = clean_space(row.get("title_en", ""))
    authors_zh = clean_multi(row.get("authors_zh", ""))
    authors_en = clean_multi(row.get("authors_en", ""))
    aff_zh = clean_aff_field(row.get("affiliations_zh", ""))
    aff_en = clean_aff_field(row.get("affiliations_en", ""))
    kw_zh = clean_multi(row.get("keywords_zh", ""))
    kw_en = clean_multi(row.get("keywords_en", ""))
    abs_zh = clean_space(row.get("abstract_zh", ""))
    abs_en = clean_space(row.get("abstract_en", ""))
    funding = clean_multi(row.get("funding_source", ""))
    clc = clean_multi(row.get("clc", ""))
    doi = clean_space(row.get("doi", ""))

    blocks: list[str] = []

    if title_zh or title_en:
        if title_zh and title_en and title_zh != title_en:
            blocks.append(f"题名：{title_zh}\nTitle: {title_en}")
        else:
            blocks.append(f"题名：{title_zh or title_en}")

    if authors_zh or authors_en:
        if authors_zh and authors_en:
            blocks.append(f"作者：{authors_zh}\nAuthors: {authors_en}")
        else:
            blocks.append(f"作者：{authors_zh or authors_en}")

    if aff_zh or aff_en:
        if aff_zh and aff_en:
            blocks.append(f"单位：{aff_zh}\nAffiliation: {aff_en}")
        else:
            blocks.append(f"单位：{aff_zh or aff_en}")

    if kw_zh or kw_en:
        if kw_zh and kw_en:
            blocks.append(f"关键词：{kw_zh}\nKeywords: {kw_en}")
        else:
            blocks.append(f"关键词：{kw_zh or kw_en}")

    if clc:
        blocks.append(f"分类号：{clc}")

    if abs_zh:
        blocks.append(f"摘要：{abs_zh}")
    if abs_en and abs_en != abs_zh:
        blocks.append(f"Abstract: {abs_en}")

    if funding:
        blocks.append(f"基金：{funding}")

    if doi:
        blocks.append(f"DOI: {doi}")

    return "\n\n".join(blocks)


def to_rag_doc(row: dict[str, str], doc_index: int) -> dict:
    doi = clean_space(row.get("doi", ""))
    title_zh = clean_space(row.get("title_zh", ""))
    title_en = clean_space(row.get("title_en", ""))
    text = build_text(row)

    return {
        "doc_id": doi or f"doc_{doc_index:04d}",
        "chunk_id": f"{doi or f'doc_{doc_index:04d}'}#0",
        "text": text,
        "text_length": len(text),
        # 过滤/展示用元数据
        "doi": doi,
        "title": title_zh or title_en,
        "title_zh": title_zh,
        "title_en": title_en,
        "authors": clean_multi(row.get("authors_zh", ""))
        or clean_multi(row.get("authors_en", "")),
        "authors_zh": clean_multi(row.get("authors_zh", "")),
        "authors_en": clean_multi(row.get("authors_en", "")),
        "affiliations": clean_aff_field(row.get("affiliations_zh", ""))
        or clean_aff_field(row.get("affiliations_en", "")),
        "keywords": clean_multi(row.get("keywords_zh", ""))
        or clean_multi(row.get("keywords_en", "")),
        "keywords_zh": clean_multi(row.get("keywords_zh", "")),
        "keywords_en": clean_multi(row.get("keywords_en", "")),
        "clc": clean_multi(row.get("clc", "")),
        "funding": clean_multi(row.get("funding_source", "")),
        "award_id": clean_multi(row.get("award_id", "")),
        "pub_date": clean_space(row.get("pub_date", "")),
        "year": year_of(row.get("pub_date", "")),
        "volume": clean_space(row.get("volume", "")),
        "issue": clean_space(row.get("issue", "")),
        "pages": pages_of(row.get("fpage", ""), row.get("lpage", "")),
        "journal": clean_space(row.get("journal_title_zh", "")),
        "journal_id": clean_space(row.get("journal_id", "")),
        "issn": clean_space(row.get("issn", "")),
        "pdf": clean_space(row.get("pdf", "")),
        "source_xml": clean_space(row.get("source_xml", "")),
        "has_abstract": "Y"
        if clean_space(row.get("abstract_zh", "")) or clean_space(row.get("abstract_en", ""))
        else "N",
    }


CSV_FIELDS = [
    "doc_id",
    "chunk_id",
    "text",
    "text_length",
    "doi",
    "title",
    "title_zh",
    "title_en",
    "authors",
    "authors_zh",
    "authors_en",
    "affiliations",
    "keywords",
    "keywords_zh",
    "keywords_en",
    "clc",
    "funding",
    "award_id",
    "pub_date",
    "year",
    "volume",
    "issue",
    "pages",
    "journal",
    "journal_id",
    "issn",
    "pdf",
    "source_xml",
    "has_abstract",
]


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="为 RAG 清洗 articles_metadata.csv")
    parser.add_argument(
        "-i",
        "--input",
        default=str(root / "articles_metadata.csv"),
        help="输入 CSV（默认 articles_metadata.csv）",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        default=str(root / "rag_cleaned"),
        help="输出目录（默认 rag_cleaned/）",
    )
    parser.add_argument(
        "--min-text-length",
        type=int,
        default=40,
        help="过短文本丢弃阈值（默认 40）",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader(input_path.open(encoding="utf-8-sig")))
    docs: list[dict] = []
    skipped: list[dict] = []

    seen_doi: set[str] = set()
    for i, row in enumerate(rows, start=1):
        skip, reason = is_editorial_record(row)
        if skip:
            skipped.append(
                {
                    "doi": clean_space(row.get("doi", "")),
                    "reason": reason,
                    "title_zh": clean_space(row.get("title_zh", "")),
                }
            )
            continue
        doc = to_rag_doc(row, i)
        doi = doc["doi"]
        if doi and doi in seen_doi:
            stem = Path(clean_space(row.get("source_xml", ""))).stem or f"row{i}"
            new_doi = f"{doi}__{stem}"
            doc["doi"] = new_doi
            doc["doc_id"] = new_doi
            doc["chunk_id"] = f"{new_doi}#0"
            doi = new_doi
        if doi:
            seen_doi.add(doi)
        if doc["text_length"] < args.min_text_length:
            skipped.append(
                {
                    "doi": doi,
                    "reason": f"text_too_short:{doc['text_length']}",
                    "title_zh": doc["title_zh"],
                }
            )
            continue
        docs.append(doc)

    # JSONL：RAG 入库常用
    jsonl_path = out_dir / "documents.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for doc in docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    # CSV：便于人工检查
    csv_path = out_dir / "documents.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(docs)

    # 仅元数据（不含长 text），方便过滤调试
    meta_path = out_dir / "documents_meta.csv"
    meta_fields = [c for c in CSV_FIELDS if c != "text"]
    with meta_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=meta_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(docs)

    skip_path = out_dir / "skipped.csv"
    with skip_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["doi", "reason", "title_zh"])
        writer.writeheader()
        writer.writerows(skipped)

    editorial_n = sum(
        1
        for s in skipped
        if str(s.get("reason") or "").startswith("editorial")
        or s.get("reason") == "no_author_no_keyword"
    )
    lengths = [d["text_length"] for d in docs]
    readme = out_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# RAG 清洗语料",
                "",
                f"- 来源: `{input_path.name}`",
                f"- 输入行数: {len(rows)}",
                f"- 输出文档: {len(docs)}（一篇论文 = 一个 chunk）",
                f"- 跳过: {len(skipped)}（其中办刊/会议类 {editorial_n}）",
                f"- 文本长度: min={min(lengths) if lengths else 0}, "
                f"avg={round(sum(lengths)/len(lengths),1) if lengths else 0}, "
                f"max={max(lengths) if lengths else 0}",
                "",
                "## 文件",
                "",
                "| 文件 | 用途 |",
                "|---|---|",
                "| `documents.jsonl` | 向量库入库（推荐） |",
                "| `documents.csv` | 含 text 的完整表 |",
                "| `documents_meta.csv` | 不含 text 的元数据 |",
                "| `skipped.csv` | 被过滤记录 |",
                "",
                "## text 字段结构",
                "",
                "题名（中/英）→ 作者 → 单位 → 关键词 → 分类号 → 摘要 → 基金 → DOI",
                "",
                "已剔除办刊通告 / 评奖引证 / 会议新闻（见 `editorial_filter.py`）。",
                "原始 `articles_metadata.csv` 未修改。",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(f"输入: {len(rows)}")
    print(f"输出: {len(docs)} → {out_dir}")
    print(f"跳过: {len(skipped)}（办刊/会议 {editorial_n}）")
    if lengths:
        print(f"text 长度: min={min(lengths)} avg={round(sum(lengths)/len(lengths),1)} max={max(lengths)}")


if __name__ == "__main__":
    main()
