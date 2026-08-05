#!/usr/bin/env python3
"""Import cleaned CSV tables into Neo4j knowledge graph."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]
CLEANED = ROOT / "cleaned"

DEFAULT_URI = os.getenv("NEO4J_URI", "bolt://182.92.0.163:7687")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", "password1")
BATCH = 500


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def chunks(rows: list[dict], size: int):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def to_int(value: str | None, default=None):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def to_bool_yn(value: str | None) -> bool:
    return (value or "").strip().upper() in {"Y", "YES", "TRUE", "1"}


CONSTRAINTS = [
    "CREATE CONSTRAINT paper_doi IF NOT EXISTS FOR (n:Paper) REQUIRE n.doi IS UNIQUE",
    "CREATE CONSTRAINT author_id IF NOT EXISTS FOR (n:Author) REQUIRE n.author_id IS UNIQUE",
    "CREATE CONSTRAINT institution_id IF NOT EXISTS FOR (n:Institution) REQUIRE n.institution_id IS UNIQUE",
    "CREATE CONSTRAINT keyword_id IF NOT EXISTS FOR (n:Keyword) REQUIRE n.keyword_id IS UNIQUE",
    "CREATE CONSTRAINT fund_id IF NOT EXISTS FOR (n:Fund) REQUIRE n.fund_id IS UNIQUE",
    "CREATE CONSTRAINT clc_code IF NOT EXISTS FOR (n:CLC) REQUIRE n.code IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX paper_year IF NOT EXISTS FOR (n:Paper) ON (n.year)",
    "CREATE INDEX author_name_zh IF NOT EXISTS FOR (n:Author) ON (n.name_zh)",
    "CREATE INDEX keyword_label_zh IF NOT EXISTS FOR (n:Keyword) ON (n.label_zh)",
    "CREATE INDEX institution_name IF NOT EXISTS FOR (n:Institution) ON (n.name_norm)",
]


def run_write(session, cypher: str, rows: list[dict], label: str):
    total = 0
    for batch in chunks(rows, BATCH):
        session.run(cypher, rows=batch)
        total += len(batch)
        print(f"  {label}: {total}/{len(rows)}", flush=True)
    return total


def import_all(uri: str, user: str, password: str, clear: bool) -> None:
    print(f"Connecting {uri} as {user} ...")
    driver = GraphDatabase.driver(
        uri,
        auth=(user, password),
        connection_timeout=15,
        max_connection_lifetime=600,
    )
    driver.verify_connectivity()
    print("Connected.")

    t0 = time.time()
    with driver.session() as session:
        if clear:
            print("Clearing existing graph ...")
            session.run("MATCH (n) DETACH DELETE n")

        print("Creating constraints/indexes ...")
        for stmt in CONSTRAINTS + INDEXES:
            session.run(stmt)

        # --- Nodes ---
        papers = read_csv(CLEANED / "papers.csv")
        paper_rows = [
            {
                "doi": r["doi"],
                "title_zh": r.get("title_zh") or "",
                "title_en": r.get("title_en") or "",
                "abstract_zh": r.get("abstract_zh") or "",
                "abstract_en": r.get("abstract_en") or "",
                "pub_date": r.get("pub_date") or "",
                "year": to_int(r.get("year")),
                "volume": r.get("volume") or "",
                "issue": r.get("issue") or "",
                "fpage": r.get("fpage") or "",
                "lpage": r.get("lpage") or "",
                "pdf": r.get("pdf") or "",
                "document_type": r.get("document_type") or "",
                "issn": r.get("issn") or "",
                "journal_id": r.get("journal_id") or "",
                "journal_title_zh": r.get("journal_title_zh") or "",
                "author_count": to_int(r.get("author_count"), 0),
                "keyword_count": to_int(r.get("keyword_count"), 0),
                "has_funding": to_bool_yn(r.get("has_funding")),
                "source_xml": r.get("source_xml") or "",
            }
            for r in papers
            if r.get("doi")
        ]
        print(f"Importing Paper nodes ({len(paper_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MERGE (p:Paper {doi: row.doi})
            SET p += row
            """,
            paper_rows,
            "Paper",
        )

        authors = read_csv(CLEANED / "authors.csv")
        author_rows = [
            {
                "author_id": r["author_id"],
                "name_zh": r.get("name_zh") or "",
                "name_en": r.get("name_en") or "",
                "email": r.get("email") or "",
                "emails": r.get("emails") or "",
                "paper_count": to_int(r.get("paper_count"), 0),
            }
            for r in authors
            if r.get("author_id")
        ]
        print(f"Importing Author nodes ({len(author_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MERGE (a:Author {author_id: row.author_id})
            SET a += row
            """,
            author_rows,
            "Author",
        )

        institutions = read_csv(CLEANED / "institutions.csv")
        inst_rows = [
            {
                "institution_id": r["institution_id"],
                "name_norm": r.get("name_norm") or "",
                "full_norm": r.get("full_norm") or "",
                "name_zh": r.get("name_zh") or "",
                "name_en": r.get("name_en") or "",
                "postcode": r.get("postcode") or "",
                "paper_count": to_int(r.get("paper_count"), 0),
            }
            for r in institutions
            if r.get("institution_id")
        ]
        print(f"Importing Institution nodes ({len(inst_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MERGE (i:Institution {institution_id: row.institution_id})
            SET i += row
            """,
            inst_rows,
            "Institution",
        )

        keywords = read_csv(CLEANED / "keywords.csv")
        kw_rows = [
            {
                "keyword_id": r["keyword_id"],
                "label_zh": r.get("label_zh") or "",
                "label_en": r.get("label_en") or "",
                "paper_count": to_int(r.get("paper_count"), 0),
            }
            for r in keywords
            if r.get("keyword_id")
        ]
        print(f"Importing Keyword nodes ({len(kw_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MERGE (k:Keyword {keyword_id: row.keyword_id})
            SET k += row
            """,
            kw_rows,
            "Keyword",
        )

        funds = read_csv(CLEANED / "funds.csv")
        fund_rows = [
            {
                "fund_id": r["fund_id"],
                "agency_norm": r.get("agency_norm") or "",
                "program_type": r.get("program_type") or "",
                "example_raw": r.get("example_raw") or "",
                "paper_count": to_int(r.get("paper_count"), 0),
            }
            for r in funds
            if r.get("fund_id")
        ]
        print(f"Importing Fund nodes ({len(fund_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MERGE (f:Fund {fund_id: row.fund_id})
            SET f += row
            """,
            fund_rows,
            "Fund",
        )

        clc_rows = read_csv(CLEANED / "paper_clc.csv")
        clc_codes: dict[str, str] = {}
        for r in clc_rows:
            code = (r.get("clc_code") or "").strip()
            parent = (r.get("clc_parent") or "").strip()
            if code:
                clc_codes[code] = parent
            if parent:
                clc_codes.setdefault(parent, "")
        clc_node_rows = [
            {"code": code, "parent": parent} for code, parent in sorted(clc_codes.items())
        ]
        print(f"Importing CLC nodes ({len(clc_node_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MERGE (c:CLC {code: row.code})
            SET c.parent = row.parent
            """,
            clc_node_rows,
            "CLC",
        )

        # --- Relationships ---
        paper_authors = read_csv(CLEANED / "paper_authors.csv")
        pa_rows = [
            {
                "doi": r["doi"],
                "author_id": r["author_id"],
                "author_order": to_int(r.get("author_order"), 0),
                "is_corresponding": to_bool_yn(r.get("is_corresponding")),
                "name_zh": r.get("name_zh") or "",
                "name_en": r.get("name_en") or "",
            }
            for r in paper_authors
            if r.get("doi") and r.get("author_id")
        ]
        print(f"Importing AUTHORED_BY ({len(pa_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MATCH (p:Paper {doi: row.doi})
            MATCH (a:Author {author_id: row.author_id})
            MERGE (p)-[r:AUTHORED_BY]->(a)
            SET r.author_order = row.author_order,
                r.is_corresponding = row.is_corresponding,
                r.name_zh = row.name_zh,
                r.name_en = row.name_en
            """,
            pa_rows,
            "AUTHORED_BY",
        )

        author_inst = read_csv(CLEANED / "author_institutions.csv")
        ai_rows = [
            {
                "doi": r["doi"],
                "author_id": r["author_id"],
                "institution_id": r["institution_id"],
                "author_order": to_int(r.get("author_order"), 0),
            }
            for r in author_inst
            if r.get("doi") and r.get("author_id") and r.get("institution_id")
        ]
        print(f"Importing AFFILIATED_WITH ({len(ai_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MATCH (a:Author {author_id: row.author_id})
            MATCH (i:Institution {institution_id: row.institution_id})
            MERGE (a)-[r:AFFILIATED_WITH {doi: row.doi}]->(i)
            SET r.author_order = row.author_order
            """,
            ai_rows,
            "AFFILIATED_WITH",
        )

        # Paper -> Institution (via authors on that paper)
        print(f"Importing HAS_AFFILIATION ({len(ai_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MATCH (p:Paper {doi: row.doi})
            MATCH (i:Institution {institution_id: row.institution_id})
            MERGE (p)-[r:HAS_AFFILIATION]->(i)
            """,
            ai_rows,
            "HAS_AFFILIATION",
        )

        paper_kw = read_csv(CLEANED / "paper_keywords.csv")
        pk_rows = [
            {"doi": r["doi"], "keyword_id": r["keyword_id"]}
            for r in paper_kw
            if r.get("doi") and r.get("keyword_id")
        ]
        print(f"Importing HAS_KEYWORD ({len(pk_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MATCH (p:Paper {doi: row.doi})
            MATCH (k:Keyword {keyword_id: row.keyword_id})
            MERGE (p)-[:HAS_KEYWORD]->(k)
            """,
            pk_rows,
            "HAS_KEYWORD",
        )

        pc_rows = [
            {
                "doi": r["doi"],
                "clc_code": (r.get("clc_code") or "").strip(),
                "clc_parent": (r.get("clc_parent") or "").strip(),
            }
            for r in clc_rows
            if r.get("doi") and (r.get("clc_code") or "").strip()
        ]
        print(f"Importing HAS_CLC ({len(pc_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MATCH (p:Paper {doi: row.doi})
            MATCH (c:CLC {code: row.clc_code})
            MERGE (p)-[:HAS_CLC]->(c)
            """,
            pc_rows,
            "HAS_CLC",
        )

        # CLC hierarchy where parent exists
        parent_edges = [
            {"code": code, "parent": parent}
            for code, parent in clc_codes.items()
            if parent and parent in clc_codes and parent != code
        ]
        print(f"Importing PARENT_OF ({len(parent_edges)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MATCH (child:CLC {code: row.code})
            MATCH (parent:CLC {code: row.parent})
            MERGE (parent)-[:PARENT_OF]->(child)
            """,
            parent_edges,
            "PARENT_OF",
        )

        awards = read_csv(CLEANED / "paper_awards.csv")
        award_rows = [
            {
                "doi": r["doi"],
                "fund_id": r["fund_id"],
                "award_id": r.get("award_id") or "",
                "funding_raw": r.get("funding_raw") or "",
                "program_type": r.get("program_type") or "",
            }
            for r in awards
            if r.get("doi") and r.get("fund_id")
        ]
        print(f"Importing FUNDED_BY ({len(award_rows)}) ...")
        run_write(
            session,
            """
            UNWIND $rows AS row
            MATCH (p:Paper {doi: row.doi})
            MATCH (f:Fund {fund_id: row.fund_id})
            MERGE (p)-[r:FUNDED_BY {award_id: row.award_id}]->(f)
            SET r.funding_raw = row.funding_raw,
                r.program_type = row.program_type
            """,
            award_rows,
            "FUNDED_BY",
        )

        stats = session.run(
            """
            CALL {
              MATCH (n) RETURN count(n) AS nodes
            }
            CALL {
              MATCH ()-[r]->() RETURN count(r) AS rels
            }
            CALL {
              MATCH (p:Paper) RETURN count(p) AS papers
            }
            CALL {
              MATCH (a:Author) RETURN count(a) AS authors
            }
            CALL {
              MATCH (i:Institution) RETURN count(i) AS institutions
            }
            CALL {
              MATCH (k:Keyword) RETURN count(k) AS keywords
            }
            CALL {
              MATCH (f:Fund) RETURN count(f) AS funds
            }
            CALL {
              MATCH (c:CLC) RETURN count(c) AS clc
            }
            RETURN nodes, rels, papers, authors, institutions, keywords, funds, clc
            """
        ).single()

    driver.close()
    elapsed = time.time() - t0
    print("\nImport finished in {:.1f}s".format(elapsed))
    print("Stats:", dict(stats) if stats else {})


def main():
    parser = argparse.ArgumentParser(description="Import cleaned CSVs into Neo4j")
    parser.add_argument("--uri", default=DEFAULT_URI)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--clear",
        action="store_true",
        help="DETACH DELETE all nodes before import",
    )
    args = parser.parse_args()

    if not CLEANED.exists():
        print(f"Missing cleaned dir: {CLEANED}", file=sys.stderr)
        sys.exit(1)

    import_all(args.uri, args.user, args.password, clear=args.clear)


if __name__ == "__main__":
    main()
