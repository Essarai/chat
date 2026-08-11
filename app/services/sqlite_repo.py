from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from statistics import median
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

    def related_keywords_for_topic(
        self,
        topic: str,
        limit: int = 10,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Keywords co-occurring on papers matched by a parent topic."""
        value = str(topic or "").strip()
        if not value:
            return []
        clauses = ["seed.label_zh LIKE ?", "related.label_zh IS NOT NULL", "related.label_zh <> ''"]
        params: List[Any] = [f"%{value}%"]
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        params.extend([f"%{value}%", limit])
        with self._conn() as conn:
            return self._rows(
                conn.execute(
                    f"""
                    SELECT related.label_zh AS keyword,
                           COUNT(DISTINCT related.doi) AS paper_count
                    FROM paper_keywords seed
                    JOIN papers p ON p.doi=seed.doi
                    JOIN paper_keywords related ON related.doi=seed.doi
                    WHERE {' AND '.join(clauses)}
                      AND related.label_zh NOT LIKE ?
                    GROUP BY related.label_zh
                    ORDER BY paper_count DESC,related.label_zh
                    LIMIT ?
                    """,
                    params,
                )
            )

    def keyword_growth(
        self,
        limit: int = 20,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Keyword share growth over two complete-year windows."""
        current_year = datetime.now().year
        with self._conn() as conn:
            bounds = conn.execute(
                "SELECT MIN(year), MAX(year) FROM papers WHERE year IS NOT NULL"
            ).fetchone()
        db_min = int(bounds[0]) if bounds and bounds[0] else current_year - 10
        db_max = int(bounds[1]) if bounds and bounds[1] else current_year
        raw_end = min(int(end_year or db_max), db_max)
        score_end = min(raw_end, current_year - 1)
        y0 = max(db_min, int(start_year)) if start_year is not None else max(db_min, score_end - 9)
        score_years = list(range(y0, score_end + 1))
        split = len(score_years) // 2
        early_years = score_years[:split]
        late_years = score_years[split:]
        if not early_years or not late_years:
            return {
                "scope": "keyword_growth", "start_year": y0, "end_year": raw_end,
                "score_end_year": score_end, "periods": [], "keyword_growth": [],
                "partial_year": raw_end if raw_end == current_year else None,
                "source": "sqlite",
            }
        with self._conn() as conn:
            totals = self._rows(conn.execute(
                """
                SELECT year, COUNT(DISTINCT doi) AS paper_count
                FROM papers WHERE year BETWEEN ? AND ? GROUP BY year
                """,
                (y0, raw_end),
            ))
            rows = self._rows(conn.execute(
                """
                SELECT pk.label_zh AS keyword, p.year AS year,
                       COUNT(DISTINCT p.doi) AS paper_count
                FROM paper_keywords pk JOIN papers p ON p.doi=pk.doi
                WHERE p.year BETWEEN ? AND ?
                  AND pk.label_zh IS NOT NULL AND pk.label_zh<>''
                GROUP BY pk.label_zh, p.year
                """,
                (y0, raw_end),
            ))
        totals_by_year = {int(r["year"]): int(r["paper_count"] or 0) for r in totals}
        by_kw: Dict[str, Dict[int, int]] = {}
        for row in rows:
            by_kw.setdefault(str(row["keyword"]), {})[int(row["year"])] = int(row["paper_count"] or 0)
        early_total = sum(totals_by_year.get(y, 0) for y in early_years)
        late_total = sum(totals_by_year.get(y, 0) for y in late_years)
        growth: List[Dict[str, Any]] = []
        for keyword, series in by_kw.items():
            early = sum(series.get(y, 0) for y in early_years)
            late = sum(series.get(y, 0) for y in late_years)
            if late < 2 and early < 2:
                continue
            early_share = early / early_total if early_total else 0.0
            late_share = late / late_total if late_total else 0.0
            late_active = sum(1 for y in late_years if series.get(y, 0) > 0)
            growth.append({
                "keyword": keyword,
                "first_seen_year": min(series) if series else None,
                "early_count": early,
                "late_count": late,
                "early_share_pct": round(early_share * 100, 3),
                "late_share_pct": round(late_share * 100, 3),
                "share_delta_pp": round((late_share - early_share) * 100, 3),
                "relative_growth_pct": round((late - early) / early * 100, 1) if early else None,
                "late_active_years": late_active,
                "emerging": early <= 1 and late >= 3 and late_active >= 2,
                "yearly": [{"year": y, "paper_count": series.get(y, 0)} for y in range(y0, raw_end + 1)],
            })
        growth.sort(key=lambda r: (-float(r["share_delta_pp"]), -int(r["late_count"]), r["keyword"]))
        return {
            "scope": "keyword_growth",
            "start_year": y0,
            "end_year": raw_end,
            "score_end_year": score_end,
            "partial_year": raw_end if raw_end == current_year else None,
            "periods": [
                {"label": "前期", "start_year": early_years[0], "end_year": early_years[-1], "paper_count": early_total},
                {"label": "近期", "start_year": late_years[0], "end_year": late_years[-1], "paper_count": late_total},
            ],
            "keyword_growth": growth[:limit],
            "source": "sqlite",
        }

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

    def papers_for_authors(
        self,
        author_ids: List[str],
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return papers grouped by stable author ids, preserving input order."""
        ids = [str(v).strip() for v in author_ids or [] if str(v).strip()]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        clauses = [f"pa.author_id IN ({placeholders})"]
        params: List[Any] = list(ids)
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        with self._conn() as conn:
            rows = self._rows(
                conn.execute(
                    f"""
                    SELECT DISTINCT pa.author_id AS author_id,
                           a.name_zh AS name_zh,
                           a.name_en AS name_en,
                           p.doi AS doi,
                           p.title_zh AS title_zh,
                           p.title_en AS title_en,
                           p.year AS year
                    FROM paper_authors pa
                    JOIN authors a ON a.author_id = pa.author_id
                    JOIN papers p ON p.doi = pa.doi
                    WHERE {' AND '.join(clauses)}
                    ORDER BY p.year DESC, p.doi
                    """,
                    params,
                )
            )
        by_id: Dict[str, Dict[str, Any]] = {
            aid: {"author_id": aid, "name_zh": None, "name_en": None, "papers": []}
            for aid in ids
        }
        seen: Dict[str, set] = {aid: set() for aid in ids}
        for row in rows:
            aid = row.get("author_id")
            if aid not in by_id or row.get("doi") in seen[aid]:
                continue
            seen[aid].add(row.get("doi"))
            by_id[aid]["name_zh"] = row.get("name_zh")
            by_id[aid]["name_en"] = row.get("name_en")
            by_id[aid]["papers"].append(
                {
                    "doi": row.get("doi"),
                    "title_zh": row.get("title_zh"),
                    "title_en": row.get("title_en"),
                    "year": row.get("year"),
                }
            )
        return [by_id[aid] for aid in ids]

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
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Authors whose papers carry a keyword containing `keyword`."""
        kw = (keyword or "").strip().strip("“”\"'‘’")
        if not kw:
            return {
                "scope": "keyword_authors",
                "keyword": keyword,
                "authors": [],
                "source": "sqlite",
                "start_year": start_year,
                "end_year": end_year,
            }
        like = f"%{kw}%"
        year_clauses: List[str] = []
        year_params: List[Any] = []
        if start_year is not None:
            year_clauses.append("p.year >= ?")
            year_params.append(start_year)
        if end_year is not None:
            year_clauses.append("p.year <= ?")
            year_params.append(end_year)
        year_sql = (" AND " + " AND ".join(year_clauses)) if year_clauses else ""

        with self._conn() as conn:
            authors = self._rows(
                conn.execute(
                    f"""
                    SELECT a.author_id AS author_id,
                           a.name_zh AS name_zh,
                           a.name_en AS name_en,
                           COUNT(DISTINCT pa.doi) AS paper_count
                    FROM paper_keywords pk
                    JOIN paper_authors pa ON pa.doi = pk.doi
                    JOIN authors a ON a.author_id = pa.author_id
                    JOIN papers p ON p.doi = pa.doi
                    WHERE pk.label_zh LIKE ?
                      {year_sql}
                    GROUP BY a.author_id
                    ORDER BY paper_count DESC, a.name_zh
                    LIMIT ?
                    """,
                    (like, *year_params, author_limit),
                )
            )
            total_authors = conn.execute(
                f"""
                SELECT COUNT(DISTINCT pa.author_id)
                FROM paper_keywords pk
                JOIN paper_authors pa ON pa.doi = pk.doi
                JOIN papers p ON p.doi = pa.doi
                WHERE pk.label_zh LIKE ?
                  {year_sql}
                """,
                (like, *year_params),
            ).fetchone()[0]
            total_papers = conn.execute(
                f"""
                SELECT COUNT(DISTINCT pk.doi)
                FROM paper_keywords pk
                JOIN papers p ON p.doi = pk.doi
                WHERE pk.label_zh LIKE ?
                  {year_sql}
                """,
                (like, *year_params),
            ).fetchone()[0]
            matched_labels = self._rows(
                conn.execute(
                    f"""
                    SELECT pk.label_zh AS keyword, COUNT(DISTINCT pk.doi) AS paper_count
                    FROM paper_keywords pk
                    JOIN papers p ON p.doi = pk.doi
                    WHERE pk.label_zh LIKE ?
                      {year_sql}
                    GROUP BY pk.label_zh
                    ORDER BY paper_count DESC
                    LIMIT 12
                    """,
                    (like, *year_params),
                )
            )

            result_authors: List[Dict[str, Any]] = []
            for a in authors:
                papers = self._rows(
                    conn.execute(
                        f"""
                        SELECT DISTINCT p.doi AS doi, p.title_zh AS title_zh,
                               p.year AS year, pk.label_zh AS matched_keyword
                        FROM paper_authors pa
                        JOIN papers p ON p.doi = pa.doi
                        JOIN paper_keywords pk ON pk.doi = pa.doi
                        WHERE pa.author_id = ? AND pk.label_zh LIKE ?
                          {year_sql}
                        ORDER BY p.year DESC, p.doi
                        LIMIT ?
                        """,
                        (a["author_id"], like, *year_params, papers_per_author),
                    )
                )
                result_authors.append({**a, "papers": papers})

        return {
            "scope": "keyword_authors",
            "task": "keyword_authors",
            "keyword": kw,
            "matched_labels": matched_labels,
            "total_authors": int(total_authors or 0),
            "total_papers": int(total_papers or 0),
            "authors": result_authors,
            "author_limit": author_limit,
            "top_n": author_limit,
            "start_year": start_year,
            "end_year": end_year,
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

    def institutions_by_topics(
        self,
        topics: List[str],
        limit: int = 15,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        terms = [str(v).strip() for v in topics or [] if str(v).strip()]
        if not terms:
            return {"scope": "keyword_institutions", "topics": [], "institutions": [], "source": "sqlite"}
        topic_sql = " OR ".join("pk.label_zh LIKE ?" for _ in terms)
        clauses = [f"({topic_sql})", "i.name_norm IS NOT NULL", "i.name_norm <> ''"]
        params: List[Any] = [f"%{term}%" for term in terms]
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        with self._conn() as conn:
            rows = self._rows(
                conn.execute(
                    f"""
                    SELECT i.name_norm AS institution,ai.doi
                    FROM paper_keywords pk
                    JOIN papers p ON p.doi=pk.doi
                    JOIN author_institutions ai ON ai.doi=pk.doi
                    JOIN institutions i ON i.institution_id=ai.institution_id
                    WHERE {' AND '.join(clauses)}
                    """,
                    params,
                )
            )
        by_institution: Dict[str, set] = {}
        for row in rows:
            name = _normalize_institution_name(str(row.get("institution") or ""))
            if name and row.get("doi"):
                by_institution.setdefault(name, set()).add(row["doi"])
        ranked = sorted(by_institution.items(), key=lambda item: (-len(item[1]), item[0]))[:limit]
        return {
            "scope": "keyword_institutions",
            "topics": terms,
            "institutions": [{"institution": name, "paper_count": len(dois)} for name, dois in ranked],
            "start_year": start_year,
            "end_year": end_year,
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
            SELECT p.doi AS doi, p.title_zh AS title_zh, p.year AS year,
                   GROUP_CONCAT(DISTINCT COALESCE(a.name_zh, a.name_en)) AS author_names
            FROM paper_keywords pk
            JOIN papers p ON p.doi = pk.doi
            LEFT JOIN paper_authors pa ON pa.doi = p.doi
            LEFT JOIN authors a ON a.author_id = pa.author_id
            WHERE {' AND '.join(clauses)}
            GROUP BY p.doi, p.title_zh, p.year
            ORDER BY p.year DESC, p.doi
            LIMIT ?
        """
        with self._conn() as conn:
            rows = self._rows(conn.execute(sql, params))
        for row in rows:
            row["authors"] = [
                name.strip()
                for name in str(row.pop("author_names", "") or "").split(",")
                if name.strip()
            ]
        return rows

    def papers_by_topics(
        self,
        topics: List[str],
        limit_per_topic: int = 20,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Traceable papers grouped by requested topic, deduplicated by DOI."""
        groups: List[Dict[str, Any]] = []
        flattened: List[Dict[str, Any]] = []
        seen = set()
        for topic in [str(v).strip() for v in topics or [] if str(v).strip()]:
            papers = self.papers_by_keyword(topic, limit_per_topic, start_year, end_year)
            groups.append({"topic": topic, "paper_count": len(papers), "papers": papers})
            for paper in papers:
                doi = paper.get("doi")
                if not doi or doi in seen:
                    continue
                seen.add(doi)
                flattened.append({**paper, "topic": topic})
        flattened.sort(key=lambda row: (-(int(row.get("year") or 0)), str(row.get("doi") or "")))
        return {
            "scope": "topic_papers",
            "topics": groups,
            "papers": flattened,
            "total_count": len(flattened),
            "start_year": start_year,
            "end_year": end_year,
            "source": "sqlite",
        }

    def representative_authors_by_topics(
        self,
        topics: List[str],
        limit: int = 10,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Authors ranked by distinct topic-matched DOI across all topics."""
        by_author: Dict[str, Dict[str, Any]] = {}
        for topic in [str(v).strip() for v in topics or [] if str(v).strip()]:
            result = self.authors_by_keyword(
                topic,
                author_limit=max(limit * 4, 30),
                papers_per_author=1000,
                start_year=start_year,
                end_year=end_year,
            )
            for author in result.get("authors") or []:
                aid = str(author.get("author_id") or "")
                if not aid:
                    continue
                row = by_author.setdefault(
                    aid,
                    {
                        "author_id": aid,
                        "name_zh": author.get("name_zh"),
                        "name_en": author.get("name_en"),
                        "topics": [],
                        "papers": {},
                    },
                )
                if topic not in row["topics"]:
                    row["topics"].append(topic)
                for paper in author.get("papers") or []:
                    if paper.get("doi"):
                        row["papers"][paper["doi"]] = paper
        rows: List[Dict[str, Any]] = []
        for row in by_author.values():
            papers = sorted(
                row.pop("papers").values(),
                key=lambda p: (-(int(p.get("year") or 0)), str(p.get("doi") or "")),
            )
            rows.append({**row, "paper_count": len(papers), "papers": papers[:5]})
        rows.sort(key=lambda row: (-int(row["paper_count"]), str(row.get("name_zh") or "")))
        return {
            "scope": "representative_authors_by_topic",
            "topics": [str(v).strip() for v in topics or [] if str(v).strip()],
            "authors": rows[:limit],
            "start_year": start_year,
            "end_year": end_year,
            "source": "sqlite",
        }

    def author_collaborators_for_ids(
        self,
        author_ids: List[str],
        limit_per_author: int = 10,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        ids = [str(v).strip() for v in author_ids or [] if str(v).strip()]
        groups: List[Dict[str, Any]] = []
        flat: List[Dict[str, Any]] = []
        with self._conn() as conn:
            for aid in ids:
                owner = conn.execute(
                    "SELECT author_id,name_zh,name_en FROM authors WHERE author_id=?", (aid,)
                ).fetchone()
                if not owner:
                    continue
                clauses = ["pa1.author_id = ?"]
                params: List[Any] = [aid]
                if start_year is not None:
                    clauses.append("p.year >= ?")
                    params.append(start_year)
                if end_year is not None:
                    clauses.append("p.year <= ?")
                    params.append(end_year)
                params.append(limit_per_author)
                collaborators = self._rows(
                    conn.execute(
                        f"""
                        SELECT a.author_id,a.name_zh,a.name_en,
                               COUNT(DISTINCT pa2.doi) AS co_papers
                        FROM paper_authors pa1
                        JOIN paper_authors pa2 ON pa2.doi=pa1.doi AND pa2.author_id<>pa1.author_id
                        JOIN authors a ON a.author_id=pa2.author_id
                        JOIN papers p ON p.doi=pa1.doi
                        WHERE {' AND '.join(clauses)}
                        GROUP BY a.author_id,a.name_zh,a.name_en
                        ORDER BY co_papers DESC,a.name_zh
                        LIMIT ?
                        """,
                        params,
                    )
                )
                for collaborator in collaborators:
                    paper_clauses = ["pa1.author_id = ?", "pa2.author_id = ?"]
                    paper_params: List[Any] = [aid, collaborator.get("author_id")]
                    if start_year is not None:
                        paper_clauses.append("p.year >= ?")
                        paper_params.append(start_year)
                    if end_year is not None:
                        paper_clauses.append("p.year <= ?")
                        paper_params.append(end_year)
                    collaborator["papers"] = self._rows(
                        conn.execute(
                            f"""
                            SELECT DISTINCT p.doi,p.title_zh,p.year
                            FROM paper_authors pa1
                            JOIN paper_authors pa2 ON pa2.doi=pa1.doi
                            JOIN papers p ON p.doi=pa1.doi
                            WHERE {' AND '.join(paper_clauses)}
                            ORDER BY p.year DESC,p.doi
                            """,
                            paper_params,
                        )
                    )
                owner_dict = dict(owner)
                groups.append({"author": owner_dict, "collaborators": collaborators})
                for collaborator in collaborators:
                    flat.append({**collaborator, "source_author_id": aid})
        return {
            "scope": "author_collaborators",
            "authors": groups,
            "collaborators": flat,
            "query_completed": True,
            "start_year": start_year,
            "end_year": end_year,
            "source": "sqlite",
        }

    def author_network(
        self,
        topic: Optional[str] = None,
        limit: int = 30,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        clauses = ["pa1.author_id < pa2.author_id"]
        params: List[Any] = []
        join_topic = ""
        if topic:
            join_topic = "JOIN paper_keywords pk ON pk.doi=p.doi"
            clauses.append("pk.label_zh LIKE ?")
            params.append(f"%{topic}%")
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        with self._conn() as conn:
            rows = self._rows(
                conn.execute(
                    f"""
                    SELECT pa1.author_id AS source_id,a1.name_zh AS source_name,
                           pa2.author_id AS target_id,a2.name_zh AS target_name,
                           p.doi,p.title_zh,p.year
                    FROM paper_authors pa1
                    JOIN paper_authors pa2 ON pa2.doi=pa1.doi
                    JOIN authors a1 ON a1.author_id=pa1.author_id
                    JOIN authors a2 ON a2.author_id=pa2.author_id
                    JOIN papers p ON p.doi=pa1.doi
                    {join_topic}
                    WHERE {' AND '.join(clauses)}
                    """,
                    params,
                )
            )
        by_edge: Dict[tuple, Dict[str, Any]] = {}
        for row in rows:
            key = (row["source_id"], row["target_id"])
            edge = by_edge.setdefault(
                key,
                {
                    "source_id": row["source_id"],
                    "source_name": row.get("source_name"),
                    "target_id": row["target_id"],
                    "target_name": row.get("target_name"),
                    "papers": {},
                },
            )
            edge["papers"][row["doi"]] = {
                "doi": row["doi"], "title_zh": row.get("title_zh"), "year": row.get("year")
            }
        edges = []
        for edge in by_edge.values():
            papers = sorted(edge.pop("papers").values(), key=lambda p: (-(int(p.get("year") or 0)), p["doi"]))
            edges.append({**edge, "paper_count": len(papers), "papers": papers})
        edges.sort(key=lambda edge: (-int(edge["paper_count"]), str(edge.get("source_name") or ""), str(edge.get("target_name") or "")))
        edges = edges[:limit]
        nodes: Dict[str, Dict[str, Any]] = {}
        for edge in edges:
            nodes[edge["source_id"]] = {"id": edge["source_id"], "name": edge.get("source_name")}
            nodes[edge["target_id"]] = {"id": edge["target_id"], "name": edge.get("target_name")}
        return {
            "scope": "author_network",
            "topic": topic,
            "nodes": list(nodes.values()),
            "edges": edges,
            "query_completed": True,
            "start_year": start_year,
            "end_year": end_year,
            "source": "sqlite",
        }

    def institution_network(
        self,
        topic: Optional[str] = None,
        limit: int = 30,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        clauses = ["ai1.institution_id < ai2.institution_id"]
        params: List[Any] = []
        join_topic = ""
        if topic:
            join_topic = "JOIN paper_keywords pk ON pk.doi=p.doi"
            clauses.append("pk.label_zh LIKE ?")
            params.append(f"%{topic}%")
        if start_year is not None:
            clauses.append("p.year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("p.year <= ?")
            params.append(end_year)
        with self._conn() as conn:
            rows = self._rows(
                conn.execute(
                    f"""
                    SELECT ai1.institution_id AS source_id,i1.name_norm AS source_name,
                           ai2.institution_id AS target_id,i2.name_norm AS target_name,
                           p.doi,p.title_zh,p.year
                    FROM author_institutions ai1
                    JOIN author_institutions ai2 ON ai2.doi=ai1.doi
                    JOIN institutions i1 ON i1.institution_id=ai1.institution_id
                    JOIN institutions i2 ON i2.institution_id=ai2.institution_id
                    JOIN papers p ON p.doi=ai1.doi
                    {join_topic}
                    WHERE {' AND '.join(clauses)}
                    """,
                    params,
                )
            )
        by_edge: Dict[tuple, Dict[str, Any]] = {}
        for row in rows:
            source_name = _normalize_institution_name(str(row.get("source_name") or ""))
            target_name = _normalize_institution_name(str(row.get("target_name") or ""))
            if not source_name or not target_name or source_name == target_name:
                continue
            if source_name > target_name:
                source_name, target_name = target_name, source_name
            source_id = f"normalized:{source_name}"
            target_id = f"normalized:{target_name}"
            key = (source_id, target_id)
            edge = by_edge.setdefault(
                key,
                {
                    "source_id": source_id,
                    "source_name": source_name,
                    "target_id": target_id,
                    "target_name": target_name,
                    "papers": {},
                },
            )
            edge["papers"][row["doi"]] = {
                "doi": row["doi"], "title_zh": row.get("title_zh"), "year": row.get("year")
            }
        edges = []
        for edge in by_edge.values():
            papers = sorted(edge.pop("papers").values(), key=lambda p: (-(int(p.get("year") or 0)), p["doi"]))
            edges.append({**edge, "paper_count": len(papers), "papers": papers})
        edges.sort(key=lambda edge: (-int(edge["paper_count"]), str(edge.get("source_name") or ""), str(edge.get("target_name") or "")))
        return {
            "scope": "institution_network",
            "topic": topic,
            "edges": edges[:limit],
            "query_completed": True,
            "start_year": start_year,
            "end_year": end_year,
            "source": "sqlite",
        }

    def representative_papers_by_institutions(
        self,
        institutions: List[str],
        papers_per_institution: int = 5,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        groups: List[Dict[str, Any]] = []
        flattened: List[Dict[str, Any]] = []
        seen = set()
        for institution in [str(v).strip() for v in institutions or [] if str(v).strip()]:
            result = self.institution_authors_with_papers(
                institution,
                top_authors=5,
                papers_per_author=papers_per_institution,
                start_year=start_year,
                end_year=end_year,
            )
            papers: List[Dict[str, Any]] = []
            local_seen = set()
            for author in result.get("authors") or []:
                for paper in author.get("papers") or []:
                    doi = paper.get("doi")
                    if not doi or doi in local_seen:
                        continue
                    local_seen.add(doi)
                    item = {**paper, "institution": institution}
                    papers.append(item)
                    if doi not in seen:
                        seen.add(doi)
                        flattened.append(item)
                    if len(papers) >= papers_per_institution:
                        break
                if len(papers) >= papers_per_institution:
                    break
            groups.append({"institution": institution, "papers": papers})
        return {
            "scope": "representative_papers_by_institution",
            "institutions": groups,
            "papers": flattened,
            "start_year": start_year,
            "end_year": end_year,
            "source": "sqlite",
        }

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

    def topic_period_compare(
        self,
        topics: List[str],
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Compare normalized topic/keyword shares between two periods."""
        requested = [str(t).strip() for t in topics or [] if str(t).strip()]
        growth = self.keyword_growth(
            max(limit * 3, 40) if requested else 100000,
            start_year,
            end_year,
        )
        periods = growth.get("periods") or []
        rows = list(growth.get("keyword_growth") or [])
        if requested:
            selected: List[Dict[str, Any]] = []
            for topic in requested:
                stats = self.topic_keyword_stats(
                    [topic],
                    periods[0]["start_year"] if periods else start_year,
                    periods[-1]["end_year"] if periods else end_year,
                )
                series = (stats.get("yearly_by_keyword") or {}).get(topic) or []
                counts = {int(r["year"]): int(r.get("paper_count") or 0) for r in series}
                if len(periods) >= 2:
                    early_years = range(periods[0]["start_year"], periods[0]["end_year"] + 1)
                    late_years = range(periods[1]["start_year"], periods[1]["end_year"] + 1)
                    early = sum(counts.get(y, 0) for y in early_years)
                    late = sum(counts.get(y, 0) for y in late_years)
                    early_total = int(periods[0].get("paper_count") or 0)
                    late_total = int(periods[1].get("paper_count") or 0)
                    es = early / early_total if early_total else 0
                    ls = late / late_total if late_total else 0
                    selected.append({
                        "keyword": topic, "early_count": early, "late_count": late,
                        "early_share_pct": round(es * 100, 3),
                        "late_share_pct": round(ls * 100, 3),
                        "share_delta_pp": round((ls - es) * 100, 3),
                    })
            rows = selected
        enhanced = sorted(
            [r for r in rows if float(r.get("share_delta_pp") or 0) > 0],
            key=lambda r: -float(r.get("share_delta_pp") or 0),
        )
        weakened = sorted(
            [r for r in rows if float(r.get("share_delta_pp") or 0) < 0],
            key=lambda r: float(r.get("share_delta_pp") or 0),
        )
        displayed = rows[:limit] if requested else enhanced[:limit] + weakened[:limit]
        return {
            "scope": "topic_period_compare",
            "start_year": growth.get("start_year"),
            "end_year": growth.get("end_year"),
            "partial_year": growth.get("partial_year"),
            "periods": periods,
            "topics": displayed,
            "enhanced": enhanced[:limit],
            "weakened": weakened[:limit],
            "new": [r for r in rows if int(r.get("early_count") or 0) == 0 and int(r.get("late_count") or 0) > 0][:limit],
            "disappeared": [r for r in rows if int(r.get("early_count") or 0) > 0 and int(r.get("late_count") or 0) == 0][:limit],
            "source": "sqlite",
        }

    @staticmethod
    def _three_periods(start_year: int, end_year: int) -> List[tuple]:
        years = list(range(start_year, end_year + 1))
        if not years:
            return []
        size = max(1, len(years) // 3)
        chunks = [years[:size], years[size: size * 2], years[size * 2:]]
        labels = ["早期", "中期", "近期"]
        return [(labels[i], chunk[0], chunk[-1]) for i, chunk in enumerate(chunks) if chunk]

    def author_direction_evolution(
        self,
        name: Optional[str] = None,
        author_id: Optional[str] = None,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        author = None
        if author_id:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT author_id,name_zh,name_en FROM authors WHERE author_id=?", (author_id,)
                ).fetchone()
                author = dict(row) if row else None
        if not author and name:
            author = self.resolve_author(name)
        if not author:
            return {"scope": "author_direction_evolution", "author": None, "periods": [], "total_papers": 0, "source": "sqlite"}
        aid = author["author_id"]
        with self._conn() as conn:
            bounds = conn.execute(
                """SELECT MIN(p.year),MAX(p.year),COUNT(DISTINCT p.doi)
                   FROM paper_authors pa JOIN papers p ON p.doi=pa.doi
                   WHERE pa.author_id=? AND p.year IS NOT NULL""", (aid,)
            ).fetchone()
        if not bounds or bounds[0] is None:
            return {"scope": "author_direction_evolution", "author": author, "periods": [], "total_papers": 0, "source": "sqlite"}
        y0 = max(int(bounds[0]), int(start_year)) if start_year is not None else int(bounds[0])
        y1 = min(int(bounds[1]), int(end_year)) if end_year is not None else int(bounds[1])
        periods: List[Dict[str, Any]] = []
        for label, a, b in self._three_periods(y0, y1):
            with self._conn() as conn:
                papers = self._rows(conn.execute(
                    """SELECT DISTINCT p.doi,p.title_zh,p.year
                       FROM paper_authors pa JOIN papers p ON p.doi=pa.doi
                       WHERE pa.author_id=? AND p.year BETWEEN ? AND ?
                       ORDER BY p.year DESC,p.doi""", (aid, a, b)
                ))
                keywords = self._rows(conn.execute(
                    """SELECT pk.label_zh AS keyword,COUNT(DISTINCT p.doi) AS paper_count
                       FROM paper_authors pa JOIN papers p ON p.doi=pa.doi
                       JOIN paper_keywords pk ON pk.doi=p.doi
                       WHERE pa.author_id=? AND p.year BETWEEN ? AND ?
                         AND pk.label_zh IS NOT NULL AND pk.label_zh<>''
                       GROUP BY pk.label_zh ORDER BY paper_count DESC,keyword LIMIT 10""",
                    (aid, a, b),
                ))
            total = len(papers)
            for kw in keywords:
                kw["share_pct"] = round(int(kw.get("paper_count") or 0) / total * 100, 1) if total else 0
            periods.append({"label": label, "start_year": a, "end_year": b, "paper_count": total, "keywords": keywords, "papers": papers[:5]})
        nonempty = [p for p in periods if p["paper_count"]]
        early_map = {r["keyword"]: float(r.get("share_pct") or 0) for r in (nonempty[0]["keywords"] if nonempty else [])}
        late_map = {r["keyword"]: float(r.get("share_pct") or 0) for r in (nonempty[-1]["keywords"] if nonempty else [])}
        changes = []
        for kw in sorted(set(early_map) | set(late_map)):
            delta = round(late_map.get(kw, 0) - early_map.get(kw, 0), 1)
            changes.append({"keyword": kw, "early_share_pct": early_map.get(kw, 0), "late_share_pct": late_map.get(kw, 0), "share_delta_pp": delta, "change": "新增" if kw not in early_map else ("减弱" if kw not in late_map or delta < 0 else "增强")})
        changes.sort(key=lambda r: -abs(float(r["share_delta_pp"])))
        return {
            "scope": "author_direction_evolution", "author": author,
            "start_year": y0, "end_year": y1, "total_papers": sum(p["paper_count"] for p in periods),
            "periods": periods, "changes": changes[:15], "source": "sqlite",
        }

    def author_direction_diversity(
        self,
        limit: int = 10,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        authors = self.author_keywords_sample(max(limit * 3, 20), 20, start_year, end_year)
        rows = []
        for author in authors:
            kws = [r for r in author.get("keywords") or [] if int(r.get("paper_count") or 0) >= 2]
            total_mentions = sum(int(r.get("paper_count") or 0) for r in kws)
            max_mentions = max([int(r.get("paper_count") or 0) for r in kws] or [0])
            rows.append({
                **author,
                "direction_count": len(kws),
                "topic_concentration": round(max_mentions / total_mentions, 3) if total_mentions else 1.0,
                "keywords": kws[:8],
            })
        rows.sort(key=lambda r: (-int(r["direction_count"]), float(r["topic_concentration"]), -int(r.get("paper_count") or 0)))
        return {"scope": "author_direction_diversity", "authors": rows[:limit], "start_year": start_year, "end_year": end_year, "source": "sqlite"}

    def institution_stability(
        self,
        limit: int = 10,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        annual_top_n: int = 10,
    ) -> Dict[str, Any]:
        current_year = datetime.now().year
        with self._conn() as conn:
            bounds = conn.execute("SELECT MIN(year),MAX(year) FROM papers WHERE year IS NOT NULL").fetchone()
        db_min = int(bounds[0]) if bounds and bounds[0] else current_year - 10
        db_max = int(bounds[1]) if bounds and bounds[1] else current_year
        y1 = min(int(end_year or db_max), current_year - 1, db_max)
        y0 = max(db_min, int(start_year)) if start_year is not None else max(db_min, y1 - 9)
        years = list(range(y0, y1 + 1))
        by_inst: Dict[str, Dict[int, set]] = {}
        for year in years:
            with self._conn() as conn:
                rows = self._rows(conn.execute(
                    """SELECT i.name_norm AS institution,ai.doi
                       FROM author_institutions ai JOIN institutions i ON i.institution_id=ai.institution_id
                       JOIN papers p ON p.doi=ai.doi
                       WHERE p.year=? AND i.name_norm IS NOT NULL AND i.name_norm<>''""", (year,)
                ))
            for row in rows:
                key = _normalize_institution_name(str(row.get("institution") or ""))
                if key and row.get("doi"):
                    by_inst.setdefault(key, {}).setdefault(year, set()).add(row["doi"])
        top_by_year: Dict[int, set] = {}
        for year in years:
            ranked = sorted(((inst, len(data.get(year, set()))) for inst, data in by_inst.items()), key=lambda x: (-x[1], x[0]))
            top_by_year[year] = {inst for inst, count in ranked[:annual_top_n] if count > 0}
        out = []
        for inst, data in by_inst.items():
            counts = [len(data.get(y, set())) for y in years]
            active = [y for y, count in zip(years, counts) if count > 0]
            longest = run = 0
            for count in counts:
                run = run + 1 if count > 0 else 0
                longest = max(longest, run)
            top_years = sum(1 for y in years if inst in top_by_year[y])
            active_rate = len(active) / len(years) if years else 0
            top_rate = top_years / len(years) if years else 0
            out.append({
                "institution": inst, "institution_id": f"normalized:{inst}",
                "total_papers": sum(counts), "active_years": len(active),
                "active_year_rate": round(active_rate, 3), "top10_years": top_years,
                "top10_year_rate": round(top_rate, 3),
                "median_annual_papers": float(median(counts)) if counts else 0,
                "max_consecutive_active_years": longest,
                "stable_high_output": active_rate >= 0.8 and top_rate >= 0.5,
                "yearly": [{"year": y, "paper_count": count} for y, count in zip(years, counts)],
            })
        out.sort(key=lambda r: (-int(r["top10_years"]), -int(r["active_years"]), -int(r["total_papers"]), r["institution"]))
        return {
            "scope": "institution_stability",
            "start_year": y0,
            "end_year": y1,
            "years": years,
            "thresholds": {
                "active_year_rate": 0.8,
                "active_year_rate_pct": 80,
                "top10_year_rate": 0.5,
                "top10_year_rate_pct": 50,
                "minimum_years": 5,
            },
            "institutions": out[:limit],
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


    @staticmethod
    def _is_institution_meta_title(title: str, institution: str) -> bool:
        """校史/办学纪念类题名：不宜作为「学科代表成果」样例。"""
        t = title or ""
        if not t:
            return False
        if re.search(
            r"西迁|校史|办学时期|名刊工程|史地研究所|求是书院|"
            r"临时校务|治校模式|导师群体|优秀师资",
            t,
        ):
            return True
        # 以本校人物/办学制度为对象的纪念、校史研究
        if re.search(
            r"(竺可桢|叶笃正|马寅初|蔡邦华|张其昀).{0,16}(浙江大学|浙大)|"
            r"(浙江大学|浙大).{0,16}(竺可桢|叶笃正|马寅初|研究院的创立|师资|师生关系|"
            r"研究生教育|导师制|优秀师资|办学|临时校务|治校)|"
            r"(与|任|在)(浙江大学|浙大)",
            t,
        ):
            return True
        inst = (institution or "").strip()
        short = "浙大" if inst == "浙江大学" else (
            inst.replace("大学", "") if inst.endswith("大学") else inst
        )
        if short and len(short) >= 2 and re.search(
            rf"{re.escape(short)}.{{0,12}}(西迁|史地|办学|导师|师资|治校)",
            t,
        ):
            return True
        return False

    def institution_authors_with_papers(
        self,
        institution: str,
        top_authors: int = 8,
        papers_per_author: int = 3,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Authors affiliated with an institution (name substring) + sample papers.

        Affiliation is taken from author_institutions on the same paper — i.e.
        papers *by* that institution's authors, not papers *about* the institution.
        Prefer non-institutional-history titles as「代表成果」samples.
        """
        inst = (institution or "").strip()
        if not inst:
            return {
                "scope": "institution_authors",
                "institution": "",
                "authors": [],
                "papers": [],
                "total_authors": 0,
                "total_papers": 0,
            }
        like = f"%{inst}%"
        year_clauses = []
        year_params: List[Any] = []
        if start_year is not None:
            year_clauses.append("p.year >= ?")
            year_params.append(start_year)
        if end_year is not None:
            year_clauses.append("p.year <= ?")
            year_params.append(end_year)
        year_sql = (" AND " + " AND ".join(year_clauses)) if year_clauses else ""
        # Over-fetch authors so we can demote those who only write institutional history
        fetch_authors = max(top_authors * 3, top_authors + 12)

        with self._conn() as conn:
            author_rows = self._rows(
                conn.execute(
                    f"""
                    SELECT a.author_id AS author_id,
                           a.name_zh AS name_zh,
                           COUNT(DISTINCT pa.doi) AS paper_count
                    FROM authors a
                    JOIN paper_authors pa ON pa.author_id = a.author_id
                    JOIN author_institutions ai
                      ON ai.doi = pa.doi AND ai.author_id = a.author_id
                    JOIN institutions i ON i.institution_id = ai.institution_id
                    JOIN papers p ON p.doi = pa.doi
                    WHERE i.name_norm LIKE ?
                      AND a.name_zh IS NOT NULL AND a.name_zh <> ''
                      {year_sql}
                    GROUP BY a.author_id
                    ORDER BY paper_count DESC, a.name_zh
                    LIMIT ?
                    """,
                    [like, *year_params, fetch_authors],
                )
            )
            total_authors = conn.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT a.author_id
                    FROM authors a
                    JOIN paper_authors pa ON pa.author_id = a.author_id
                    JOIN author_institutions ai
                      ON ai.doi = pa.doi AND ai.author_id = a.author_id
                    JOIN institutions i ON i.institution_id = ai.institution_id
                    JOIN papers p ON p.doi = pa.doi
                    WHERE i.name_norm LIKE ?
                      AND a.name_zh IS NOT NULL AND a.name_zh <> ''
                      {year_sql}
                    GROUP BY a.author_id
                )
                """,
                [like, *year_params],
            ).fetchone()[0]
            total_papers = conn.execute(
                f"""
                SELECT COUNT(DISTINCT ai.doi)
                FROM author_institutions ai
                JOIN institutions i ON i.institution_id = ai.institution_id
                JOIN papers p ON p.doi = ai.doi
                WHERE i.name_norm LIKE ? {year_sql}
                """,
                [like, *year_params],
            ).fetchone()[0]

            ranked: List[Dict[str, Any]] = []
            for a in author_rows:
                aid = a.get("author_id")
                candidates = self._rows(
                    conn.execute(
                        f"""
                        SELECT DISTINCT p.doi AS doi,
                               p.title_zh AS title_zh,
                               p.year AS year
                        FROM paper_authors pa
                        JOIN papers p ON p.doi = pa.doi
                        JOIN author_institutions ai
                          ON ai.doi = pa.doi AND ai.author_id = pa.author_id
                        JOIN institutions i ON i.institution_id = ai.institution_id
                        WHERE pa.author_id = ?
                          AND i.name_norm LIKE ?
                          {year_sql}
                        ORDER BY p.year DESC, p.doi
                        LIMIT ?
                        """,
                        [aid, like, *year_params, max(papers_per_author * 4, 12)],
                    )
                )
                non_meta = [
                    p
                    for p in candidates
                    if not self._is_institution_meta_title(
                        str(p.get("title_zh") or ""), inst
                    )
                ]
                meta = [
                    p
                    for p in candidates
                    if self._is_institution_meta_title(
                        str(p.get("title_zh") or ""), inst
                    )
                ]
                papers = (non_meta + meta)[:papers_per_author]
                # Prefer authors who have at least one non-meta paper for showcase
                score = (
                    1 if non_meta else 0,
                    int(a.get("paper_count") or 0),
                )
                ranked.append({**a, "papers": papers, "_score": score})

            ranked.sort(
                key=lambda r: (-r["_score"][0], -r["_score"][1], r.get("name_zh") or "")
            )
            with_research = [r for r in ranked if r["_score"][0] == 1]
            meta_only = [r for r in ranked if r["_score"][0] == 0]
            selected = with_research[:top_authors]
            if len(selected) < top_authors:
                selected.extend(meta_only[: top_authors - len(selected)])

            authors_out = []
            flat_papers: List[Dict[str, Any]] = []
            seen_doi: set[str] = set()
            for a in selected:
                a.pop("_score", None)
                authors_out.append(a)
                for p in a.get("papers") or []:
                    doi = (p.get("doi") or "").lower()
                    if doi and doi not in seen_doi:
                        seen_doi.add(doi)
                        flat_papers.append(p)

        return {
            "scope": "institution_authors",
            "task": "institution_authors",
            "institution": inst,
            "authors": authors_out,
            "papers": flat_papers,
            "total_authors": int(total_authors or 0),
            "total_papers": int(total_papers or 0),
            "top_n_authors": top_authors,
            "papers_per_author": papers_per_author,
            "start_year": start_year,
            "end_year": end_year,
            "source": "sqlite",
        }


# Backward-compatible alias
MySQLRepo = SQLiteRepo
