from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.config import Settings, get_settings


class SQLiteRepo:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.db_path = Path(self.settings.sqlite_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"SQLite DB not found: {self.db_path}. "
                "Run: python3 scripts/import_to_sqlite.py"
            )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _rows(cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
        return [dict(r) for r in cur.fetchall()]

    def get_paper(self, doi: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT doi, title_zh, title_en, year, volume, issue, fpage, lpage,
                       journal_title_zh, abstract_zh, abstract_en, has_funding
                FROM papers WHERE doi=?
                """,
                (doi,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def yearly_counts(
        self, start_year: Optional[int] = None, end_year: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        clauses = ["year IS NOT NULL"]
        params: List[Any] = []
        if start_year is not None:
            clauses.append("year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("year <= ?")
            params.append(end_year)
        sql = f"""
            SELECT year, COUNT(*) AS paper_count
            FROM papers
            WHERE {' AND '.join(clauses)}
            GROUP BY year
            ORDER BY year
        """
        with self._conn() as conn:
            return self._rows(conn.execute(sql, params))

    def top_keywords(
        self,
        limit: int = 20,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["pk.label_zh IS NOT NULL", "pk.label_zh <> ''"]
        params: List[Any] = []
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        params.append(limit)
        sql = f"""
            SELECT pk.label_zh AS keyword, COUNT(*) AS paper_count
            FROM paper_keywords pk
            JOIN papers p ON p.doi = pk.doi
            WHERE {' AND '.join(clauses)}
            GROUP BY pk.label_zh
            ORDER BY paper_count DESC
            LIMIT ?
        """
        with self._conn() as conn:
            return self._rows(conn.execute(sql, params))

    def top_funds(self, limit: int = 15) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            return self._rows(
                conn.execute(
                    """
                    SELECT agency_norm, paper_count
                    FROM funds
                    WHERE agency_norm IS NOT NULL AND agency_norm <> ''
                    ORDER BY paper_count DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def authors_by_keyword(
        self,
        keyword: str,
        author_limit: int = 30,
        papers_per_author: int = 8,
    ) -> Dict[str, Any]:
        """Authors whose papers carry a keyword containing `keyword`."""
        kw = (keyword or "").strip().strip("“”\"'‘’")
        if not kw:
            return {
                "scope": "keyword_authors",
                "keyword": keyword,
                "authors": [],
                "source": "sqlite",
            }
        like = f"%{kw}%"
        with self._conn() as conn:
            authors = self._rows(
                conn.execute(
                    """
                    SELECT a.author_id AS author_id,
                           a.name_zh AS name_zh,
                           a.name_en AS name_en,
                           COUNT(DISTINCT pa.doi) AS paper_count
                    FROM paper_keywords pk
                    JOIN paper_authors pa ON pa.doi = pk.doi
                    JOIN authors a ON a.author_id = pa.author_id
                    WHERE pk.label_zh LIKE ?
                    GROUP BY a.author_id
                    ORDER BY paper_count DESC, a.name_zh
                    LIMIT ?
                    """,
                    (like, author_limit),
                )
            )
            total_authors = conn.execute(
                """
                SELECT COUNT(DISTINCT pa.author_id)
                FROM paper_keywords pk
                JOIN paper_authors pa ON pa.doi = pk.doi
                WHERE pk.label_zh LIKE ?
                """,
                (like,),
            ).fetchone()[0]
            total_papers = conn.execute(
                """
                SELECT COUNT(DISTINCT pk.doi)
                FROM paper_keywords pk
                WHERE pk.label_zh LIKE ?
                """,
                (like,),
            ).fetchone()[0]
            matched_labels = self._rows(
                conn.execute(
                    """
                    SELECT pk.label_zh AS keyword, COUNT(DISTINCT pk.doi) AS paper_count
                    FROM paper_keywords pk
                    WHERE pk.label_zh LIKE ?
                    GROUP BY pk.label_zh
                    ORDER BY paper_count DESC
                    LIMIT 12
                    """,
                    (like,),
                )
            )

            result_authors: List[Dict[str, Any]] = []
            for a in authors:
                papers = self._rows(
                    conn.execute(
                        """
                        SELECT DISTINCT p.doi AS doi, p.title_zh AS title_zh,
                               p.year AS year, pk.label_zh AS matched_keyword
                        FROM paper_authors pa
                        JOIN papers p ON p.doi = pa.doi
                        JOIN paper_keywords pk ON pk.doi = pa.doi
                        WHERE pa.author_id = ? AND pk.label_zh LIKE ?
                        ORDER BY p.year DESC, p.doi
                        LIMIT ?
                        """,
                        (a["author_id"], like, papers_per_author),
                    )
                )
                result_authors.append({**a, "papers": papers})

        return {
            "scope": "keyword_authors",
            "keyword": kw,
            "matched_labels": matched_labels,
            "total_authors": int(total_authors or 0),
            "total_papers": int(total_papers or 0),
            "authors": result_authors,
            "author_limit": author_limit,
            "source": "sqlite",
        }

    def search_authors_by_name(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        like = f"%{name}%"
        with self._conn() as conn:
            return self._rows(
                conn.execute(
                    """
                    SELECT author_id, name_zh, name_en, paper_count, email
                    FROM authors
                    WHERE name_zh LIKE ? OR name_en LIKE ?
                    ORDER BY paper_count DESC
                    LIMIT ?
                    """,
                    (like, like, limit),
                )
            )

    def resolve_author(self, name: str) -> Optional[Dict[str, Any]]:
        authors = self.search_authors_by_name(name, limit=5)
        return authors[0] if authors else None

    def author_profile(self, name: str) -> Dict[str, Any]:
        """Author-scoped stats only — never journal-wide aggregates."""
        author = self.resolve_author(name)
        if not author:
            return {"author": None, "scope": "author", "source": "sqlite"}
        aid = author["author_id"]
        with self._conn() as conn:
            yearly = self._rows(
                conn.execute(
                    """
                    SELECT p.year AS year, COUNT(DISTINCT p.doi) AS paper_count
                    FROM paper_authors pa
                    JOIN papers p ON p.doi = pa.doi
                    WHERE pa.author_id = ? AND p.year IS NOT NULL
                    GROUP BY p.year
                    ORDER BY p.year
                    """,
                    (aid,),
                )
            )
            keywords = self._rows(
                conn.execute(
                    """
                    SELECT pk.label_zh AS keyword, COUNT(DISTINCT pa.doi) AS paper_count
                    FROM paper_authors pa
                    JOIN paper_keywords pk ON pk.doi = pa.doi
                    WHERE pa.author_id = ?
                      AND pk.label_zh IS NOT NULL AND pk.label_zh <> ''
                    GROUP BY pk.label_zh
                    ORDER BY paper_count DESC
                    LIMIT 20
                    """,
                    (aid,),
                )
            )
            funds = self._rows(
                conn.execute(
                    """
                    SELECT agency AS agency_norm, COUNT(DISTINCT doi) AS paper_count
                    FROM (
                      SELECT pa.doi AS doi,
                             COALESCE(NULLIF(pa_aw.agency_norm, ''), f.agency_norm) AS agency
                      FROM paper_authors pa
                      JOIN paper_awards pa_aw ON pa_aw.doi = pa.doi
                      LEFT JOIN funds f ON f.fund_id = pa_aw.fund_id
                      WHERE pa.author_id = ?
                    )
                    WHERE agency IS NOT NULL AND agency <> ''
                    GROUP BY agency
                    ORDER BY paper_count DESC
                    LIMIT 12
                    """,
                    (aid,),
                )
            )
            papers = self._rows(
                conn.execute(
                    """
                    SELECT p.doi, p.title_zh, p.year
                    FROM paper_authors pa
                    JOIN papers p ON p.doi = pa.doi
                    WHERE pa.author_id = ?
                    ORDER BY p.year DESC, p.doi
                    """,
                    (aid,),
                )
            )
            year_min = yearly[0]["year"] if yearly else None
            year_max = yearly[-1]["year"] if yearly else None
            total = len(papers)
            # Prefer live count over possibly stale authors.paper_count
            author = dict(author)
            author["paper_count"] = total
        return {
            "scope": "author",
            "source": "sqlite",
            "author": author,
            "total_papers": total,
            "year_min": year_min,
            "year_max": year_max,
            "yearly": yearly,
            "keywords": keywords,
            "funds": funds,
            "papers": papers,
            # backward-compatible alias
            "recent_papers": papers,
        }

    def author_collaborators(self, name: str, limit: int = 20) -> Dict[str, Any]:
        authors = self.search_authors_by_name(name, limit=5)
        if not authors:
            return {"authors": [], "collaborators": [], "source": "sqlite"}
        author = authors[0]
        with self._conn() as conn:
            collaborators = self._rows(
                conn.execute(
                    """
                    SELECT a.author_id, a.name_zh, a.name_en,
                           COUNT(DISTINCT pa2.doi) AS co_papers
                    FROM paper_authors pa1
                    JOIN paper_authors pa2
                      ON pa1.doi = pa2.doi AND pa1.author_id <> pa2.author_id
                    JOIN authors a ON a.author_id = pa2.author_id
                    WHERE pa1.author_id = ?
                    GROUP BY a.author_id, a.name_zh, a.name_en
                    ORDER BY co_papers DESC
                    LIMIT ?
                    """,
                    (author["author_id"], limit),
                )
            )
        return {
            "author": author,
            "candidates": authors,
            "collaborators": collaborators,
            "source": "sqlite",
        }

    def collaborator_institutions(self, name: str, limit: int = 50) -> Dict[str, Any]:
        """Collaborators of an author grouped by institution (co-authored papers)."""
        authors = self.search_authors_by_name(name, limit=5)
        if not authors:
            return {
                "author": None,
                "by_institution": [],
                "collaborators": [],
                "source": "sqlite",
            }
        author = authors[0]
        with self._conn() as conn:
            people = self._rows(
                conn.execute(
                    """
                    WITH collab AS (
                      SELECT pa2.author_id AS collaborator_id, pa2.doi
                      FROM paper_authors pa1
                      JOIN paper_authors pa2
                        ON pa1.doi = pa2.doi AND pa1.author_id <> pa2.author_id
                      WHERE pa1.author_id = ?
                    )
                    SELECT a.author_id, a.name_zh, a.name_en,
                           COUNT(DISTINCT c.doi) AS co_papers,
                           GROUP_CONCAT(DISTINCT i.name_norm) AS institutions
                    FROM collab c
                    JOIN authors a ON a.author_id = c.collaborator_id
                    LEFT JOIN author_institutions ai
                      ON ai.author_id = a.author_id AND ai.doi = c.doi
                    LEFT JOIN institutions i ON i.institution_id = ai.institution_id
                    GROUP BY a.author_id
                    ORDER BY co_papers DESC, a.name_zh
                    LIMIT ?
                    """,
                    (author["author_id"], limit),
                )
            )

        inst_map: Dict[str, List[str]] = {}
        collaborators = []
        for r in people:
            insts = []
            seen = set()
            for x in (r.get("institutions") or "").split(","):
                x = x.strip()
                if x and x not in seen:
                    seen.add(x)
                    insts.append(x)
            label = r.get("name_zh") or r.get("name_en") or r["author_id"]
            collaborators.append(
                {
                    "author_id": r["author_id"],
                    "name_zh": r.get("name_zh"),
                    "name_en": r.get("name_en"),
                    "co_papers": r.get("co_papers"),
                    "institutions": insts,
                }
            )
            for inst in insts or ["(无机构信息)"]:
                inst_map.setdefault(inst, []).append(f"{label}({r.get('co_papers')})")

        by_institution = [
            {"institution": inst, "collaborators": people_list, "count": len(people_list)}
            for inst, people_list in sorted(
                inst_map.items(), key=lambda kv: (-len(kv[1]), kv[0])
            )
        ]
        return {
            "author": author,
            "collaborators": collaborators,
            "by_institution": by_institution,
            "source": "sqlite",
        }


# Backward-compatible alias
MySQLRepo = SQLiteRepo
