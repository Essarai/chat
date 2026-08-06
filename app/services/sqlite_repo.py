from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.config import Settings, get_settings


def _normalize_institution_name(name: str) -> str:
    s = (name or "").strip()
    # leading junk: "1浙江大学…" / "（1）浙江大学"
    s = re.sub(r"^[\d０-９]+", "", s)
    s = re.sub(r"^[（(]\s*\d+\s*[)）]", "", s)
    # unify spacing so "浙江大学 环境与资源学院" ≈ "浙江大学环境与资源学院"
    s = re.sub(r"\s+", "", s)
    # re-insert a space after 浙江大学 / 杭州师范大学 for readability
    s = re.sub(r"^(浙江大学|杭州师范大学|宁夏大学|中国科学院)", r"\1 ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


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

    def top_authors(
        self,
        limit: int = 15,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["a.name_zh IS NOT NULL", "a.name_zh <> ''"]
        params: List[Any] = []
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        params.append(limit)
        sql = f"""
            SELECT a.author_id AS author_id,
                   a.name_zh AS name_zh,
                   COUNT(DISTINCT pa.doi) AS paper_count
            FROM paper_authors pa
            JOIN authors a ON a.author_id = pa.author_id
            JOIN papers p ON p.doi = pa.doi
            WHERE {' AND '.join(clauses)}
            GROUP BY a.author_id
            ORDER BY paper_count DESC, a.name_zh
            LIMIT ?
        """
        with self._conn() as conn:
            return self._rows(conn.execute(sql, params))

    def top_institutions(
        self,
        limit: int = 15,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["i.name_norm IS NOT NULL", "i.name_norm <> ''"]
        params: List[Any] = []
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        sql = f"""
            SELECT i.name_norm AS institution, ai.doi AS doi
            FROM author_institutions ai
            JOIN institutions i ON i.institution_id = ai.institution_id
            JOIN papers p ON p.doi = ai.doi
            WHERE {' AND '.join(clauses)}
        """
        with self._conn() as conn:
            rows = self._rows(conn.execute(sql, params))
        # merge aliases (leading digit junk / spacing); count distinct DOI per key
        by_inst: Dict[str, set] = {}
        for r in rows:
            key = _normalize_institution_name(str(r.get("institution") or ""))
            doi = r.get("doi")
            if not key or not doi:
                continue
            by_inst.setdefault(key, set()).add(doi)
        ordered = sorted(
            ((k, len(v)) for k, v in by_inst.items()),
            key=lambda kv: (-kv[1], kv[0]),
        )[:limit]
        return [{"institution": k, "paper_count": v} for k, v in ordered]

    def keywords_by_periods(
        self,
        periods: List[tuple],
        limit_per_period: int = 8,
    ) -> List[Dict[str, Any]]:
        """periods: list of (label, start_year, end_year)."""
        out: List[Dict[str, Any]] = []
        for label, y0, y1 in periods:
            kws = self.top_keywords(limit_per_period, y0, y1)
            out.append(
                {
                    "period": label,
                    "start_year": y0,
                    "end_year": y1,
                    "keywords": kws,
                    "paper_count": sum(
                        int(r.get("paper_count") or 0)
                        for r in self.yearly_counts(y0, y1)
                    ),
                }
            )
        return out

    def journal_overview(
        self,
        start_year: Optional[int],
        end_year: Optional[int],
    ) -> Dict[str, Any]:
        y0, y1 = start_year, end_year
        if y0 is None or y1 is None:
            # fall back to full span in DB
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT MIN(year), MAX(year) FROM papers WHERE year IS NOT NULL"
                ).fetchone()
            y0 = y0 or (int(row[0]) if row and row[0] else 2000)
            y1 = y1 or (int(row[1]) if row and row[1] else 2026)

        # split into ~3 periods
        span = max(y1 - y0 + 1, 1)
        step = max(span // 3, 1)
        p1 = (y0, y0 + step - 1)
        p2 = (y0 + step, min(y0 + 2 * step - 1, y1))
        p3 = (min(y0 + 2 * step, y1), y1)
        periods = [
            (f"{a}-{b}", a, b)
            for a, b in (p1, p2, p3)
            if a <= b
        ]
        # dedupe overlapping edges
        clean = []
        seen = set()
        for label, a, b in periods:
            key = (a, b)
            if key in seen or a > b:
                continue
            seen.add(key)
            clean.append((label, a, b))

        return {
            "scope": "journal_overview",
            "start_year": y0,
            "end_year": y1,
            "yearly": self.yearly_counts(y0, y1),
            "keywords": self.top_keywords(20, y0, y1),
            "authors": self.top_authors(15, y0, y1),
            "institutions": self.top_institutions(15, y0, y1),
            "periods": self.keywords_by_periods(clean, limit_per_period=8),
            "funds": self.top_funds(10),
            "source": "sqlite",
        }

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

    def coauthored_papers(self, name_a: str, name_b: str) -> Dict[str, Any]:
        """Papers co-authored by both authors (intersection)."""
        a = self.resolve_author(name_a)
        b = self.resolve_author(name_b)
        if not a or not b:
            return {
                "scope": "coauthored_papers",
                "source": "sqlite",
                "author_a": a,
                "author_b": b,
                "author_name_a": name_a,
                "author_name_b": name_b,
                "papers": [],
                "total_papers": 0,
            }
        with self._conn() as conn:
            papers = self._rows(
                conn.execute(
                    """
                    SELECT p.doi, p.title_zh, p.year
                    FROM papers p
                    JOIN paper_authors pa1 ON pa1.doi = p.doi AND pa1.author_id = ?
                    JOIN paper_authors pa2 ON pa2.doi = p.doi AND pa2.author_id = ?
                    ORDER BY p.year DESC, p.doi
                    """,
                    (a["author_id"], b["author_id"]),
                )
            )
        return {
            "scope": "coauthored_papers",
            "source": "sqlite",
            "author_a": a,
            "author_b": b,
            "author_name_a": a.get("name_zh") or name_a,
            "author_name_b": b.get("name_zh") or name_b,
            "papers": papers,
            "total_papers": len(papers),
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

    def institutions_by_keyword(
        self,
        keyword: str,
        limit: int = 15,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Institutions appearing on papers that carry a matching keyword."""
        kw = (keyword or "").strip().strip("“”\"'‘’")
        if not kw:
            return {
                "scope": "keyword_institutions",
                "keyword": keyword,
                "institutions": [],
                "source": "sqlite",
            }
        like = f"%{kw}%"
        clauses = ["pk.label_zh LIKE ?", "i.name_norm IS NOT NULL", "i.name_norm <> ''"]
        params: List[Any] = [like]
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        sql = f"""
            SELECT i.name_norm AS institution, ai.doi AS doi
            FROM paper_keywords pk
            JOIN papers p ON p.doi = pk.doi
            JOIN author_institutions ai ON ai.doi = pk.doi
            JOIN institutions i ON i.institution_id = ai.institution_id
            WHERE {' AND '.join(clauses)}
        """
        with self._conn() as conn:
            rows = self._rows(conn.execute(sql, params))
        by_inst: Dict[str, set] = {}
        for r in rows:
            key = _normalize_institution_name(str(r.get("institution") or ""))
            doi = r.get("doi")
            if not key or not doi:
                continue
            by_inst.setdefault(key, set()).add(doi)
        ordered = sorted(
            ((k, len(v)) for k, v in by_inst.items()),
            key=lambda kv: (-kv[1], kv[0]),
        )[:limit]
        return {
            "scope": "keyword_institutions",
            "keyword": kw,
            "start_year": start_year,
            "end_year": end_year,
            "institutions": [{"institution": k, "paper_count": v} for k, v in ordered],
            "source": "sqlite",
        }

    def papers_by_keyword(
        self,
        keyword: str,
        limit: int = 8,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        kw = (keyword or "").strip().strip("“”\"'‘’")
        if not kw:
            return []
        like = f"%{kw}%"
        clauses = ["pk.label_zh LIKE ?"]
        params: List[Any] = [like]
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        params.append(limit)
        sql = f"""
            SELECT DISTINCT p.doi AS doi, p.title_zh AS title_zh, p.year AS year
            FROM paper_keywords pk
            JOIN papers p ON p.doi = pk.doi
            WHERE {' AND '.join(clauses)}
            ORDER BY p.year DESC, p.doi
            LIMIT ?
        """
        with self._conn() as conn:
            return self._rows(conn.execute(sql, params))

    def topic_keyword_stats(
        self,
        keywords: List[str],
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Per-keyword paper counts and yearly series for a topic word list."""
        terms = [str(k).strip() for k in (keywords or []) if str(k).strip()]
        counts: List[Dict[str, Any]] = []
        yearly_by_term: Dict[str, List[Dict[str, Any]]] = {}
        for term in terms:
            like = f"%{term}%"
            clauses = ["pk.label_zh LIKE ?"]
            params: List[Any] = [like]
            if start_year is not None:
                clauses.append("p.year >= ?")
                params.append(start_year)
            if end_year is not None:
                clauses.append("p.year <= ?")
                params.append(end_year)
            with self._conn() as conn:
                total = conn.execute(
                    f"""
                    SELECT COUNT(DISTINCT pk.doi)
                    FROM paper_keywords pk
                    JOIN papers p ON p.doi = pk.doi
                    WHERE {' AND '.join(clauses)}
                    """,
                    params,
                ).fetchone()[0]
                yearly = self._rows(
                    conn.execute(
                        f"""
                        SELECT p.year AS year, COUNT(DISTINCT pk.doi) AS paper_count
                        FROM paper_keywords pk
                        JOIN papers p ON p.doi = pk.doi
                        WHERE {' AND '.join(clauses)} AND p.year IS NOT NULL
                        GROUP BY p.year
                        ORDER BY p.year
                        """,
                        params,
                    )
                )
            counts.append({"keyword": term, "paper_count": int(total or 0)})
            yearly_by_term[term] = yearly
        counts.sort(key=lambda r: (-int(r["paper_count"]), r["keyword"]))
        return {
            "scope": "topic_stats",
            "start_year": start_year,
            "end_year": end_year,
            "topic_keywords": counts,
            "yearly_by_keyword": yearly_by_term,
            "source": "sqlite",
        }

    def author_keywords_sample(
        self,
        author_limit: int = 8,
        kw_limit: int = 6,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Top authors with their top keywords in a year window."""
        authors = self.top_authors(author_limit, start_year, end_year)
        out: List[Dict[str, Any]] = []
        for a in authors:
            aid = a.get("author_id")
            clauses = [
                "pa.author_id = ?",
                "pk.label_zh IS NOT NULL",
                "pk.label_zh <> ''",
            ]
            params: List[Any] = [aid]
            if start_year is not None:
                clauses.append("p.year >= ?")
                params.append(start_year)
            if end_year is not None:
                clauses.append("p.year <= ?")
                params.append(end_year)
            params.append(kw_limit)
            with self._conn() as conn:
                kws = self._rows(
                    conn.execute(
                        f"""
                        SELECT pk.label_zh AS keyword, COUNT(DISTINCT pa.doi) AS paper_count
                        FROM paper_authors pa
                        JOIN paper_keywords pk ON pk.doi = pa.doi
                        JOIN papers p ON p.doi = pa.doi
                        WHERE {' AND '.join(clauses)}
                        GROUP BY pk.label_zh
                        ORDER BY paper_count DESC
                        LIMIT ?
                        """,
                        params,
                    )
                )
            out.append({**a, "keywords": kws})
        return out


# Backward-compatible alias
MySQLRepo = SQLiteRepo
