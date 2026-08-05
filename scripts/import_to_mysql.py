#!/usr/bin/env python3
"""Import cleaned CSV tables into MySQL (not SQLite)."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
CLEANED = ROOT / "cleaned"
SCHEMA = Path(__file__).resolve().parent / "schema_mysql.sql"

DEFAULT_HOST = os.getenv("MYSQL_HOST", "182.92.0.163")
DEFAULT_PORT = int(os.getenv("MYSQL_PORT", "3306"))
DEFAULT_USER = os.getenv("MYSQL_USER", "root")
DEFAULT_PASSWORD = os.getenv("MYSQL_PASSWORD", "YourStrongPassword123!")
DEFAULT_DB = os.getenv("MYSQL_DATABASE", "journal_kg")
BATCH = 500


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


def connect(host, port, user, password, database=None):
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.Cursor,
    )


def apply_schema(conn):
    sql = SCHEMA.read_text(encoding="utf-8")
    # Split on semicolons carefully enough for this file
    statements = []
    buf = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    if buf:
        statements.append("\n".join(buf))

    with conn.cursor() as cur:
        for stmt in statements:
            s = stmt.strip().rstrip(";").strip()
            if not s:
                continue
            cur.execute(s)
    conn.commit()


def executemany(conn, sql: str, rows: list, label: str):
    if not rows:
        print(f"  {label}: 0")
        return
    total = 0
    with conn.cursor() as cur:
        for batch in chunks(rows, BATCH):
            cur.executemany(sql, batch)
            total += len(batch)
            print(f"  {label}: {total}/{len(rows)}", flush=True)
    conn.commit()


def import_all(host, port, user, password, database, recreate: bool):
    print(f"Connecting mysql://{user}@{host}:{port} ...")
    # Connect without DB first to create schema/database
    root_conn = connect(host, port, user, password, database=None)
    try:
        if recreate:
            print(f"Applying schema -> database `{database}` ...")
            apply_schema(root_conn)
        else:
            with root_conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    "DEFAULT CHARACTER SET utf8mb4 "
                    "DEFAULT COLLATE utf8mb4_unicode_ci"
                )
            root_conn.commit()
    finally:
        root_conn.close()

    conn = connect(host, port, user, password, database=database)
    t0 = time.time()
    try:
        with conn.cursor() as cur:
            cur.execute("SET NAMES utf8mb4")
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
        conn.commit()

        # papers
        papers = read_csv(CLEANED / "papers.csv")
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
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            paper_rows,
            "papers",
        )

        authors = read_csv(CLEANED / "authors.csv")
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
            """
            INSERT INTO authors (author_id, name_zh, name_en, email, emails, paper_count)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            author_rows,
            "authors",
        )

        institutions = read_csv(CLEANED / "institutions.csv")
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
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            inst_rows,
            "institutions",
        )

        keywords = read_csv(CLEANED / "keywords.csv")
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
            """
            INSERT INTO keywords (keyword_id, label_zh, label_en, paper_count)
            VALUES (%s,%s,%s,%s)
            """,
            kw_rows,
            "keywords",
        )

        funds = read_csv(CLEANED / "funds.csv")
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
            """
            INSERT INTO funds (fund_id, agency_norm, program_type, example_raw, paper_count)
            VALUES (%s,%s,%s,%s,%s)
            """,
            fund_rows,
            "funds",
        )

        paper_authors = read_csv(CLEANED / "paper_authors.csv")
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
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            pa_rows,
            "paper_authors",
        )

        author_inst = read_csv(CLEANED / "author_institutions.csv")
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
            """
            INSERT INTO author_institutions (doi, author_id, institution_id, author_order)
            VALUES (%s,%s,%s,%s)
            """,
            ai_rows,
            "author_institutions",
        )

        paper_kw = read_csv(CLEANED / "paper_keywords.csv")
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
            """
            INSERT INTO paper_keywords (doi, keyword_id, label_zh, label_en)
            VALUES (%s,%s,%s,%s)
            """,
            pk_rows,
            "paper_keywords",
        )

        paper_clc = read_csv(CLEANED / "paper_clc.csv")
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
            """
            INSERT INTO paper_clc (doi, clc_code, clc_parent)
            VALUES (%s,%s,%s)
            """,
            clc_rows,
            "paper_clc",
        )

        awards = read_csv(CLEANED / "paper_awards.csv")
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
            ) VALUES (%s,%s,%s,%s,%s,%s)
            """,
            award_rows,
            "paper_awards",
        )

        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
            tables = [
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
            ]
            print("\nRow counts:")
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM `{t}`")
                print(f"  {t}: {cur.fetchone()[0]}")
        conn.commit()
    finally:
        conn.close()

    print(f"\nMySQL import finished in {time.time() - t0:.1f}s")
    print(f"Database: {database} @ {host}:{port}")


def main():
    parser = argparse.ArgumentParser(description="Import cleaned CSVs into MySQL")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop/recreate tables from schema_mysql.sql before import",
    )
    args = parser.parse_args()

    if not CLEANED.exists():
        print(f"Missing cleaned dir: {CLEANED}", file=sys.stderr)
        sys.exit(1)

    import_all(
        args.host,
        args.port,
        args.user,
        args.password,
        args.database,
        recreate=args.recreate,
    )


if __name__ == "__main__":
    main()
