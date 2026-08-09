#!/usr/bin/env python3
"""验证人文版 cleaned / rag_cleaned 是否可上传。"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

from editorial_filter import EDITORIAL_TITLE_RE, is_editorial_record

ROOT = Path(__file__).resolve().parent
CLEANED = ROOT / "cleaned"
RAG = ROOT / "rag_cleaned"

KNOWN_BAD_SNIPPETS = [
    "本刊四次蝉联",
    "本刊六次蝉联",
    "WAJCI",
    "中国科技期刊引证报告",
    "在线优先出版论文",
]


def _fail(msg: str, errors: list[str]) -> None:
    errors.append(msg)
    print(f"FAIL  {msg}")


def _ok(msg: str) -> None:
    print(f"OK    {msg}")


def main() -> int:
    errors: list[str] = []
    papers_path = CLEANED / "papers.csv"
    jsonl_path = RAG / "documents.jsonl"
    skip_kg = CLEANED / "skipped_editorial.csv"
    skip_rag = RAG / "skipped.csv"

    if not papers_path.exists():
        _fail(f"缺少 {papers_path}", errors)
        return 1
    if not jsonl_path.exists():
        _fail(f"缺少 {jsonl_path}", errors)
        return 1

    papers = list(csv.DictReader(papers_path.open(encoding="utf-8-sig")))
    dois = [p.get("doi") or "" for p in papers]
    n = len(papers)
    _ok(f"KG papers.csv = {n}")

    if n < 3000:
        _fail(f"论文数过少: {n}（预期约 3400+）", errors)
    if n > 3700:
        _fail(f"论文数异常偏多: {n}", errors)

    empty_doi = sum(1 for d in dois if not d.strip())
    if empty_doi:
        _fail(f"空 DOI: {empty_doi}", errors)
    else:
        _ok("DOI 均非空")

    dup = [d for d, c in Counter(dois).items() if d and c > 1]
    if dup:
        _fail(f"重复 DOI: {len(dup)} 例，如 {dup[:3]}", errors)
    else:
        _ok("DOI 无重复")

    residual = [
        p
        for p in papers
        if EDITORIAL_TITLE_RE.search((p.get("title_zh") or "") + (p.get("title_en") or ""))
        or is_editorial_record(p)[0]
    ]
    if residual:
        _fail(f"KG 仍含办刊/会议类 {len(residual)} 篇，例: {(residual[0].get('title_zh') or '')[:60]}", errors)
    else:
        _ok("KG 无办刊/会议残留（规则复检）")

    for snip in KNOWN_BAD_SNIPPETS:
        hit = [p for p in papers if snip in (p.get("title_zh") or "")]
        if hit:
            _fail(f"已知污染题名仍在库: {snip} ({len(hit)})", errors)
    if not any(snip in (p.get("title_zh") or "") for p in papers for snip in KNOWN_BAD_SNIPPETS):
        _ok("已知污染题名片段已清除")

    # RAG jsonl
    rag_docs = []
    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rag_docs.append(json.loads(line))
    _ok(f"RAG documents.jsonl = {len(rag_docs)}")

    if abs(len(rag_docs) - n) > 50:
        _fail(f"RAG/KG 篇数差过大: rag={len(rag_docs)} kg={n}", errors)
    else:
        _ok(f"RAG/KG 篇数接近（差 {abs(len(rag_docs) - n)}）")

    rag_residual = [
        d
        for d in rag_docs
        if EDITORIAL_TITLE_RE.search((d.get("title_zh") or "") + (d.get("title") or ""))
    ]
    if rag_residual:
        _fail(f"RAG 仍含办刊题名 {len(rag_residual)}", errors)
    else:
        _ok("RAG 无办刊题名残留")

    short = sum(1 for d in rag_docs if int(d.get("text_length") or 0) < 40)
    if short:
        _fail(f"RAG text 过短: {short}", errors)
    else:
        _ok("RAG text 长度均 ≥ 40")

    if skip_kg.exists():
        sk = list(csv.DictReader(skip_kg.open(encoding="utf-8-sig")))
        _ok(f"KG skipped_editorial = {len(sk)}")
        if len(sk) < 50:
            _fail(f"剔除清单过少 ({len(sk)})，过滤可能未生效", errors)
    else:
        _fail("缺少 cleaned/skipped_editorial.csv", errors)

    if skip_rag.exists():
        sr = list(csv.DictReader(skip_rag.open(encoding="utf-8-sig")))
        ed = sum(
            1
            for r in sr
            if str(r.get("reason") or "").startswith("editorial")
            or r.get("reason") == "no_author_no_keyword"
        )
        _ok(f"RAG skipped = {len(sr)}（办刊/会议 {ed}）")
    else:
        _fail("缺少 rag_cleaned/skipped.csv", errors)

    # 必备边表
    for name in ("authors.csv", "paper_authors.csv", "keywords.csv", "paper_keywords.csv"):
        p = CLEANED / name
        if not p.exists() or p.stat().st_size < 100:
            _fail(f"边表异常: {name}", errors)
        else:
            rows = sum(1 for _ in p.open(encoding="utf-8-sig")) - 1
            _ok(f"{name} rows≈{rows}")

    print()
    if errors:
        print(f"验证失败: {len(errors)} 项")
        return 1
    print("验证通过，可以上传。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
