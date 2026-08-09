#!/usr/bin/env python3
"""Import cleaned CSV tables into local SQLite (replaces MySQL)."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANED = ROOT / "cleaned"
DEFAULT_DB = ROOT / "data" / "journal.db"
BATCH = 500

SCHEMA = """
PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS paper_awards;
DROP TABLE IF EXISTS paper_clc;
DROP TABLE IF EXISTS paper_keywords;
DROP TABLE IF EXISTS author_institutions;
DROP TABLE IF EXISTS paper_authors;
DROP TABLE IF EXISTS funds;
DROP TABLE IF EXISTS keywords;
DROP TABLE IF EXISTS institutions;
DROP TABLE IF EXISTS authors;
DROP TABLE IF EXISTS papers;

CREATE TABLE papers (
  doi TEXT NOT NULL PRIMARY KEY,
  source_xml TEXT,
  publisher_article_id TEXT,
  title_zh TEXT,
  title_en TEXT,
  abstract_zh TEXT,
  abstract_en TEXT,
  pub_date TEXT,
  received_date TEXT,
  year INTEGER,
  volume TEXT,
  issue TEXT,
  fpage TEXT,
  lpage TEXT,
  pdf TEXT,
  document_type TEXT,
  issn TEXT,
  journal_id TEXT,
  journal_title_zh TEXT,
  author_count INTEGER DEFAULT 0,
  keyword_count INTEGER DEFAULT 0,
  has_funding INTEGER DEFAULT 0
);

CREATE TABLE authors (
  author_id TEXT NOT NULL PRIMARY KEY,
  name_zh TEXT,
  name_en TEXT,
  email TEXT,
  emails TEXT,
  paper_count INTEGER DEFAULT 0
);

CREATE TABLE institutions (
  institution_id TEXT NOT NULL PRIMARY KEY,
  name_norm TEXT,
  full_norm TEXT,
  name_zh TEXT,
  name_en TEXT,
  postcode TEXT,
  paper_count INTEGER DEFAULT 0
);

CREATE TABLE keywords (
  keyword_id TEXT NOT NULL PRIMARY KEY,
  label_zh TEXT,
  label_en TEXT,
  paper_count INTEGER DEFAULT 0
);

CREATE TABLE funds (
  fund_id TEXT NOT NULL PRIMARY KEY,
  agency_norm TEXT,
  program_type TEXT,
  example_raw TEXT,
  paper_count INTEGER DEFAULT 0
);

CREATE TABLE paper_authors (
  doi TEXT NOT NULL,
  author_id TEXT NOT NULL,
  author_order INTEGER NOT NULL DEFAULT 0,
  name_zh TEXT,
  name_en TEXT,
  is_corresponding INTEGER DEFAULT 0,
  institution_ids TEXT,
  aff_xml_ids TEXT,
  PRIMARY KEY (doi, author_order)
);

CREATE TABLE author_institutions (
  doi TEXT NOT NULL,
  author_id TEXT NOT NULL,
  institution_id TEXT NOT NULL,
  author_order INTEGER DEFAULT 0,
  PRIMARY KEY (doi, author_id, institution_id)
);

CREATE TABLE paper_keywords (
  doi TEXT NOT NULL,
  keyword_id TEXT NOT NULL,
  label_zh TEXT,
  label_en TEXT,
  PRIMARY KEY (doi, keyword_id)
);

CREATE TABLE paper_clc (
  doi TEXT NOT NULL,
  clc_code TEXT NOT NULL,
  clc_parent TEXT,
  PRIMARY KEY (doi, clc_code)
);

CREATE TABLE paper_awards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doi TEXT NOT NULL,
  fund_id TEXT NOT NULL,
  agency_norm TEXT,
  program_type TEXT,
  funding_raw TEXT,
  award_id TEXT
);

CREATE INDEX idx_papers_year ON papers(year);
CREATE INDEX idx_authors_name_zh ON authors(name_zh);
CREATE INDEX idx_keywords_label_zh ON keywords(label_zh);
CREATE INDEX idx_pa_author ON paper_authors(author_id);
CREATE INDEX idx_pk_keyword ON paper_keywords(keyword_id);

PRAGMA foreign_keys = ON;
"""


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def chunks(rows: list, size: int):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def to_int(value, default=None):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def to_bool_yn(value) -> int:
    return 1 if (value or "").strip().upper() in {"Y", "YES", "TRUE", "1"} else 0


def dedupe_by_key(rows: list[dict], key: str) -> list[dict]:
    """Keep last occurrence for each key (CSV may contain duplicate primary keys)."""
    seen: dict = {}
    for r in rows:
        k = (r.get(key) or "").strip()
        if not k:
            continue
        seen[k] = r
    return list(seen.values())


def dedupe_by_keys(rows: list[dict], keys: list[str]) -> list[dict]:
    seen: dict = {}
    for r in rows:
        tup = tuple((r.get(k) or "").strip() for k in keys)
        if not all(tup):
            continue
        seen[tup] = r
    return list(seen.values())


def executemany(conn: sqlite3.Connection, sql: str, rows: list, label: str):
    if not rows:
        print(f"  {label}: 0")
        return
    total = 0
    for batch in chunks(rows, BATCH):
        conn.executemany(sql, batch)
        total += len(batch)
        print(f"  {label}: {total}/{len(rows)}", flush=True)
    conn.commit()


def import_all(db_path: Path, cleaned: Path | None = None):
    cleaned_dir = Path(cleaned) if cleaned else CLEANED
    if not cleaned_dir.exists():
        print(f"Missing cleaned dir: {cleaned_dir}", file=sys.stderr)
        sys.exit(1)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    print(f"Creating SQLite DB: {db_path}")
    print(f"Source cleaned: {cleaned_dir}")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    t0 = time.time()
    try:
        conn.executescript(SCHEMA)

        papers = dedupe_by_key(read_csv(cleaned_dir / "papers.csv"), "doi")
        paper_rows = [
            (
                r["doi"],
                r.get("source_xml") or None,
                r.get("publisher_article_id") or None,
                r.get("title_zh") or None,
                r.get("title_en") or None,
                r.get("abstract_zh") or None,
                r.get("abstract_en") or None,
                r.get("pub_date") or None,
                r.get("received_date") or None,
                to_int(r.get("year")),
                r.get("volume") or None,
                r.get("issue") or None,
                r.get("fpage") or None,
                r.get("lpage") or None,
                r.get("pdf") or None,
                r.get("document_type") or None,
                r.get("issn") or None,
                r.get("journal_id") or None,
                r.get("journal_title_zh") or None,
                to_int(r.get("author_count"), 0),
                to_int(r.get("keyword_count"), 0),
                to_bool_yn(r.get("has_funding")),
            )
            for r in papers
            if r.get("doi")
        ]
        print(f"Importing papers ({len(paper_rows)}) ...")
        executemany(
            conn,
            """
            INSERT INTO papers (
              doi, source_xml, publisher_article_id, title_zh, title_en,
              abstract_zh, abstract_en, pub_date, received_date, year,
              volume, issue, fpage, lpage, pdf, document_type, issn,
              journal_id, journal_title_zh, author_count, keyword_count, has_funding
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            paper_rows,
            "papers",
        )

        authors = dedupe_by_key(read_csv(cleaned_dir / "authors.csv"), "author_id")
        author_rows = [
            (
                r["author_id"],
                r.get("name_zh") or None,
                r.get("name_en") or None,
                r.get("email") or None,
                r.get("emails") or None,
                to_int(r.get("paper_count"), 0),
            )
            for r in authors
            if r.get("author_id")
        ]
        print(f"Importing authors ({len(author_rows)}) ...")
        executemany(
            conn,
            "INSERT INTO authors (author_id, name_zh, name_en, email, emails, paper_count) VALUES (?,?,?,?,?,?)",
            author_rows,
            "authors",
        )

        institutions = dedupe_by_key(
            read_csv(cleaned_dir / "institutions.csv"), "institution_id"
        )
        inst_rows = [
            (
                r["institution_id"],
                r.get("name_norm") or None,
                r.get("full_norm") or None,
                r.get("name_zh") or None,
                r.get("name_en") or None,
                r.get("postcode") or None,
                to_int(r.get("paper_count"), 0),
            )
            for r in institutions
            if r.get("institution_id")
        ]
        print(f"Importing institutions ({len(inst_rows)}) ...")
        executemany(
            conn,
            """
            INSERT INTO institutions (
              institution_id, name_norm, full_norm, name_zh, name_en, postcode, paper_count
            ) VALUES (?,?,?,?,?,?,?)
            """,
            inst_rows,
            "institutions",
        )

        keywords = dedupe_by_key(read_csv(cleaned_dir / "keywords.csv"), "keyword_id")
        kw_rows = [
            (
                r["keyword_id"],
                r.get("label_zh") or None,
                r.get("label_en") or None,
                to_int(r.get("paper_count"), 0),
            )
            for r in keywords
            if r.get("keyword_id")
        ]
        print(f"Importing keywords ({len(kw_rows)}) ...")
        executemany(
            conn,
            "INSERT INTO keywords (keyword_id, label_zh, label_en, paper_count) VALUES (?,?,?,?)",
            kw_rows,
            "keywords",
        )

        funds = dedupe_by_key(read_csv(cleaned_dir / "funds.csv"), "fund_id")
        fund_rows = [
            (
                r["fund_id"],
                r.get("agency_norm") or None,
                r.get("program_type") or None,
                r.get("example_raw") or None,
                to_int(r.get("paper_count"), 0),
            )
            for r in funds
            if r.get("fund_id")
        ]
        print(f"Importing funds ({len(fund_rows)}) ...")
        executemany(
            conn,
            "INSERT INTO funds (fund_id, agency_norm, program_type, example_raw, paper_count) VALUES (?,?,?,?,?)",
            fund_rows,
            "funds",
        )

        paper_authors = dedupe_by_keys(
            read_csv(cleaned_dir / "paper_authors.csv"), ["doi", "author_order"]
        )
        pa_rows = [
            (
                r["doi"],
                r["author_id"],
                to_int(r.get("author_order"), 0),
                r.get("name_zh") or None,
                r.get("name_en") or None,
                to_bool_yn(r.get("is_corresponding")),
                r.get("institution_ids") or None,
                r.get("aff_xml_ids") or None,
            )
            for r in paper_authors
            if r.get("doi") and r.get("author_id")
        ]
        print(f"Importing paper_authors ({len(pa_rows)}) ...")
        executemany(
            conn,
            """
            INSERT INTO paper_authors (
              doi, author_id, author_order, name_zh, name_en,
              is_corresponding, institution_ids, aff_xml_ids
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            pa_rows,
            "paper_authors",
        )

        author_inst = dedupe_by_keys(
            read_csv(cleaned_dir / "author_institutions.csv"),
            ["doi", "author_id", "institution_id"],
        )
        ai_rows = [
            (
                r["doi"],
                r["author_id"],
                r["institution_id"],
                to_int(r.get("author_order"), 0),
            )
            for r in author_inst
            if r.get("doi") and r.get("author_id") and r.get("institution_id")
        ]
        print(f"Importing author_institutions ({len(ai_rows)}) ...")
        executemany(
            conn,
            "INSERT INTO author_institutions (doi, author_id, institution_id, author_order) VALUES (?,?,?,?)",
            ai_rows,
            "author_institutions",
        )

        paper_kw = dedupe_by_keys(
            read_csv(cleaned_dir / "paper_keywords.csv"), ["doi", "keyword_id"]
        )
        pk_rows = [
            (
                r["doi"],
                r["keyword_id"],
                r.get("label_zh") or None,
                r.get("label_en") or None,
            )
            for r in paper_kw
            if r.get("doi") and r.get("keyword_id")
        ]
        print(f"Importing paper_keywords ({len(pk_rows)}) ...")
        executemany(
            conn,
            "INSERT INTO paper_keywords (doi, keyword_id, label_zh, label_en) VALUES (?,?,?,?)",
            pk_rows,
            "paper_keywords",
        )

        paper_clc = dedupe_by_keys(
            read_csv(cleaned_dir / "paper_clc.csv"), ["doi", "clc_code"]
        )
        clc_rows = [
            (
                r["doi"],
                (r.get("clc_code") or "").strip(),
                (r.get("clc_parent") or "").strip() or None,
            )
            for r in paper_clc
            if r.get("doi") and (r.get("clc_code") or "").strip()
        ]
        print(f"Importing paper_clc ({len(clc_rows)}) ...")
        executemany(
            conn,
            "INSERT INTO paper_clc (doi, clc_code, clc_parent) VALUES (?,?,?)",
            clc_rows,
            "paper_clc",
        )

        awards = read_csv(cleaned_dir / "paper_awards.csv")
        award_rows = [
            (
                r["doi"],
                r["fund_id"],
                r.get("agency_norm") or None,
                r.get("program_type") or None,
                r.get("funding_raw") or None,
                r.get("award_id") or None,
            )
            for r in awards
            if r.get("doi") and r.get("fund_id")
        ]
        print(f"Importing paper_awards ({len(award_rows)}) ...")
        executemany(
            conn,
            """
            INSERT INTO paper_awards (
              doi, fund_id, agency_norm, program_type, funding_raw, award_id
            ) VALUES (?,?,?,?,?,?)
            """,
            award_rows,
            "paper_awards",
        )

        print("\nRow counts:")
        for t in [
            "papers",
            "authors",
            "institutions",
            "keywords",
            "funds",
            "paper_authors",
            "author_institutions",
            "paper_keywords",
            "paper_clc",
            "paper_awards",
        ]:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {n}")
    finally:
        conn.close()

    print(f"\nSQLite import finished in {time.time() - t0:.1f}s")
    print(f"DB: {db_path}")


def main():
    parser = argparse.ArgumentParser(description="Import cleaned CSVs into SQLite")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--cleaned",
        type=Path,
        default=CLEANED,
        help="directory with papers.csv and related tables",
    )
    args = parser.parse_args()
    if not args.cleaned.exists():
        print(f"Missing cleaned dir: {args.cleaned}", file=sys.stderr)
        sys.exit(1)
    import_all(args.db, cleaned=args.cleaned)


if __name__ == "__main__":
    main()
