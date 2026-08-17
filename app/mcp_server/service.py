from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

from app.config import DEFAULT_JOURNAL_ID, JOURNAL_TITLES, get_corpus_settings
from app.mcp_server.contracts import (
    CompareSet,
    EntityType,
    EvidenceRef,
    JournalId,
    PublicationScope,
    ResearchDescription,
    ToolResponse,
)
from app.services.sqlite_repo import SQLiteRepo, _normalize_institution_name
from app.utils import doi_url


SemanticSearcher = Callable[..., Dict[str, Any]]

_METHOD_TERMS = (
    "机器学习",
    "深度学习",
    "大语言模型",
    "神经网络",
    "机器视觉",
    "文本分析",
    "内容分析",
    "实证分析",
    "案例研究",
    "问卷调查",
    "访谈",
    "回归分析",
    "结构方程",
    "实验",
    "测序",
    "转录组",
    "代谢组",
    "蛋白组",
    "PCR",
    "CRISPR",
    "基因编辑",
    "色谱",
    "质谱",
)

_GENERIC_METHOD_TERMS = {
    "人工智能",
    "机器学习",
    "深度学习",
    "大语言模型",
    "神经网络",
    "实证分析",
    "案例研究",
    "问卷调查",
    "访谈",
    "回归分析",
}


class JournalMCPService:
    """Read-only business-semantic facade over the journal data stores.

    The service deliberately exposes no SQL, Cypher, vector-store, planning, or
    answer-generation primitives. A general-purpose agent composes these methods;
    this class owns data scope, deterministic calculations, and evidence.
    """

    def __init__(
        self,
        *,
        semantic_searcher: Optional[SemanticSearcher] = None,
        repos: Optional[Dict[str, SQLiteRepo]] = None,
    ) -> None:
        self._semantic_searcher = semantic_searcher
        self._repos: Dict[str, SQLiteRepo] = repos or {}

    def _repo(self, journal_id: str) -> SQLiteRepo:
        if journal_id not in JOURNAL_TITLES:
            raise ValueError(f"unsupported journal_id: {journal_id}")
        if journal_id not in self._repos:
            self._repos[journal_id] = SQLiteRepo(get_corpus_settings(journal_id))
        return self._repos[journal_id]

    @staticmethod
    def _data_as_of(repo: SQLiteRepo) -> Optional[str]:
        try:
            return datetime.fromtimestamp(Path(repo.db_path).stat().st_mtime).date().isoformat()
        except OSError:
            return None

    def _response(
        self,
        repo: SQLiteRepo,
        *,
        status: Literal["complete", "partial", "ambiguous", "unsupported", "error"] = "complete",
        scope: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        evidence_refs: Optional[List[EvidenceRef]] = None,
        assumptions: Optional[List[str]] = None,
        limitations: Optional[List[str]] = None,
        missing_inputs: Optional[List[str]] = None,
        unsupported_claims: Optional[List[str]] = None,
        next_cursor: Optional[int] = None,
    ) -> ToolResponse:
        return ToolResponse(
            status=status,
            scope=scope or {},
            data=data or {},
            evidence_refs=evidence_refs or [],
            assumptions=assumptions or [],
            limitations=limitations or [],
            missing_inputs=missing_inputs or [],
            unsupported_claims=unsupported_claims or [],
            data_as_of=self._data_as_of(repo),
            next_cursor=next_cursor,
        )

    @staticmethod
    def _paper_evidence(rows: Iterable[Dict[str, Any]], source: str = "sqlite") -> List[EvidenceRef]:
        out: List[EvidenceRef] = []
        seen = set()
        for row in rows:
            doi = str(row.get("doi") or "").strip()
            if not doi or doi in seen:
                continue
            seen.add(doi)
            year = row.get("year")
            try:
                parsed_year = int(year) if year is not None else None
            except (TypeError, ValueError):
                parsed_year = None
            out.append(
                EvidenceRef(
                    evidence_type="paper",
                    source=source,
                    ref_id=doi,
                    title=row.get("title") or row.get("title_zh"),
                    year=parsed_year,
                    url=row.get("url") or doi_url(doi),
                )
            )
        return out

    @staticmethod
    def _scope_where(scope: PublicationScope) -> Tuple[List[str], List[Any]]:
        clauses: List[str] = ["1=1"]
        params: List[Any] = []
        if scope.year_start is not None:
            clauses.append("p.year >= ?")
            params.append(scope.year_start)
        if scope.year_end is not None:
            clauses.append("p.year <= ?")
            params.append(scope.year_end)
        topics = [value for value in scope.topics if value]
        if topics:
            topic_sql = " OR ".join("pk_scope.label_zh LIKE ?" for _ in topics)
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_keywords pk_scope "
                f"WHERE pk_scope.doi=p.doi AND ({topic_sql}))"
            )
            params.extend(f"%{value}%" for value in topics)
        if scope.author:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_authors pa_scope "
                "JOIN authors a_scope ON a_scope.author_id=pa_scope.author_id "
                "WHERE pa_scope.doi=p.doi AND "
                "(a_scope.name_zh LIKE ? OR a_scope.name_en LIKE ? OR a_scope.author_id=?))"
            )
            params.extend([f"%{scope.author}%", f"%{scope.author}%", scope.author])
        if scope.institution:
            clauses.append(
                "EXISTS (SELECT 1 FROM author_institutions ai_scope "
                "JOIN institutions i_scope ON i_scope.institution_id=ai_scope.institution_id "
                "WHERE ai_scope.doi=p.doi AND "
                "(i_scope.name_norm LIKE ? OR i_scope.institution_id=?))"
            )
            params.extend([f"%{scope.institution}%", scope.institution])
        if scope.dois:
            placeholders = ",".join("?" for _ in scope.dois)
            clauses.append(f"p.doi IN ({placeholders})")
            params.extend(scope.dois)
        return clauses, params

    @staticmethod
    def _scope_limitations(scope: PublicationScope) -> List[str]:
        if scope.article_type:
            return [
                "当前期刊库没有可靠的文章类型字段，article_type 条件未参与过滤。"
            ]
        return []

    def _matching_paper_count(self, repo: SQLiteRepo, scope: PublicationScope) -> int:
        clauses, params = self._scope_where(scope)
        with repo._conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(DISTINCT p.doi) FROM papers p WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def _search_rows(
        self,
        repo: SQLiteRepo,
        scope: PublicationScope,
        *,
        limit: int,
        offset: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        clauses, params = self._scope_where(scope)
        where = " AND ".join(clauses)
        with repo._conn() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(DISTINCT p.doi) FROM papers p WHERE {where}", params
            ).fetchone()
            rows = repo._rows(
                conn.execute(
                    f"""
                    SELECT p.doi,p.title_zh,p.title_en,p.year,p.volume,p.issue,
                           GROUP_CONCAT(DISTINCT COALESCE(a.name_zh,a.name_en)) AS author_names,
                           GROUP_CONCAT(DISTINCT pk.label_zh) AS keyword_names
                    FROM papers p
                    LEFT JOIN paper_authors pa ON pa.doi=p.doi
                    LEFT JOIN authors a ON a.author_id=pa.author_id
                    LEFT JOIN paper_keywords pk ON pk.doi=p.doi
                    WHERE {where}
                    GROUP BY p.doi,p.title_zh,p.title_en,p.year,p.volume,p.issue
                    ORDER BY p.year DESC,p.doi
                    LIMIT ? OFFSET ?
                    """,
                    [*params, limit, offset],
                )
            )
        for row in rows:
            row["authors"] = self._split_group(row.pop("author_names", ""))
            row["keywords"] = self._split_group(row.pop("keyword_names", ""))
            row["url"] = doi_url(row.get("doi"))
        return rows, int(total_row[0] or 0) if total_row else 0

    @staticmethod
    def _split_group(value: Any) -> List[str]:
        return list(
            dict.fromkeys(
                part.strip()
                for part in str(value or "").split(",")
                if part.strip()
            )
        )

    def _paper_details(self, repo: SQLiteRepo, doi: str) -> Optional[Dict[str, Any]]:
        paper = repo.get_paper(doi)
        if not paper:
            return None
        with repo._conn() as conn:
            authors = repo._rows(
                conn.execute(
                    """
                    SELECT a.author_id,a.name_zh,a.name_en,pa.author_order
                    FROM paper_authors pa JOIN authors a ON a.author_id=pa.author_id
                    WHERE pa.doi=? ORDER BY pa.author_order,a.author_id
                    """,
                    (doi,),
                )
            )
            institutions = repo._rows(
                conn.execute(
                    """
                    SELECT DISTINCT i.institution_id,i.name_norm AS institution
                    FROM author_institutions ai
                    JOIN institutions i ON i.institution_id=ai.institution_id
                    WHERE ai.doi=? ORDER BY i.name_norm
                    """,
                    (doi,),
                )
            )
            keywords = repo._rows(
                conn.execute(
                    """
                    SELECT DISTINCT label_zh AS keyword FROM paper_keywords
                    WHERE doi=? AND label_zh IS NOT NULL AND label_zh<>''
                    ORDER BY label_zh
                    """,
                    (doi,),
                )
            )
        paper.update(
            {
                "authors": authors,
                "institutions": institutions,
                "keywords": [row["keyword"] for row in keywords],
                "url": doi_url(doi),
            }
        )
        return paper

    def _semantic_search(self, **kwargs: Any) -> Dict[str, Any]:
        if self._semantic_searcher is None:
            from app.capabilities.rag_capability import semantic_search

            self._semantic_searcher = semantic_search
        return self._semantic_searcher(**kwargs)

    def get_journal_data_scope(self, journal_id: JournalId = DEFAULT_JOURNAL_ID) -> ToolResponse:
        repo = self._repo(journal_id)
        with repo._conn() as conn:
            bounds = conn.execute(
                "SELECT MIN(year),MAX(year),COUNT(DISTINCT doi) FROM papers WHERE year IS NOT NULL"
            ).fetchone()
            counts = {
                table: int(
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0
                )
                for table in ("papers", "authors", "institutions", "paper_keywords")
            }
        settings = repo.settings
        sources = {
            "sqlite": {"available": True, "role": "结构化论文、作者、机构和统计"},
            "chroma": {
                "configured": bool(
                    settings.chroma_collection
                    and (
                        settings.chroma_cloud_api_key
                        if settings.chroma_uses_cloud
                        else settings.chroma_host
                    )
                ),
                "role": "论文语义检索",
            },
            "neo4j": {
                "configured": bool(
                    settings.neo4j_uri
                    and settings.neo4j_user
                    and settings.neo4j_password
                ),
                "role": "作者、机构和论文关系；不可用时使用 SQLite 回退",
            },
        }
        data = {
            "journal_id": journal_id,
            "journal_title": JOURNAL_TITLES[journal_id],
            "covered_years": {
                "start": int(bounds[0]) if bounds and bounds[0] else None,
                "end": int(bounds[1]) if bounds and bounds[1] else None,
            },
            "record_counts": counts,
            "sources": sources,
            "supported_capabilities": [
                "结构化论文检索",
                "语义相似论文检索",
                "主题聚合与趋势",
                "作者和机构画像",
                "合作网络",
                "基于本刊历史内容的投稿匹配",
            ],
            "unsupported_or_data_dependent": [
                "录用概率预测",
                "拒稿原因和审稿周期分析",
                "基于引用数据的影响力预测",
                "相对于全球学术文献的创新性证明",
            ],
        }
        return self._response(
            repo,
            scope={"journal_id": journal_id},
            data=data,
            evidence_refs=[
                EvidenceRef(
                    evidence_type="data_scope",
                    source="sqlite",
                    ref_id=journal_id,
                    title=JOURNAL_TITLES[journal_id],
                    details={"covered_years": data["covered_years"], "record_counts": counts},
                )
            ],
            unsupported_claims=data["unsupported_or_data_dependent"],
        )

    def resolve_academic_entity(
        self,
        entity_type: EntityType,
        text: str,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        limit: int = 10,
    ) -> ToolResponse:
        repo = self._repo(journal_id)
        value = str(text or "").strip()
        if not value:
            return self._response(
                repo,
                status="ambiguous",
                scope={"journal_id": journal_id, "entity_type": entity_type},
                missing_inputs=["text"],
            )
        candidates: List[Dict[str, Any]] = []
        with repo._conn() as conn:
            if entity_type == "author":
                authors = repo.search_authors_by_name(
                    value, min(max(limit, 1), 30)
                )
                candidates = [
                    {
                        "identifier": row.get("name_zh") or row.get("name_en"),
                        "name_zh": row.get("name_zh"),
                        "name_en": row.get("name_en"),
                        "paper_count": row.get("paper_count"),
                        "email": row.get("email"),
                    }
                    for row in authors
                    if row.get("name_zh") or row.get("name_en")
                ]
            elif entity_type == "institution":
                candidates = repo._rows(
                    conn.execute(
                        """
                        SELECT institution_id,name_norm AS name,
                               COUNT(DISTINCT ai.doi) AS paper_count
                        FROM institutions i
                        LEFT JOIN author_institutions ai USING(institution_id)
                        WHERE name_norm LIKE ? OR institution_id=?
                        GROUP BY institution_id,name_norm
                        ORDER BY paper_count DESC,name_norm LIMIT ?
                        """,
                        (f"%{value}%", value, min(max(limit, 1), 30)),
                    )
                )
            elif entity_type == "paper":
                candidates = repo._rows(
                    conn.execute(
                        """
                        SELECT doi,title_zh,title_en,year FROM papers
                        WHERE doi=? OR title_zh LIKE ? OR title_en LIKE ?
                        ORDER BY CASE WHEN doi=? THEN 0 ELSE 1 END,year DESC LIMIT ?
                        """,
                        (value, f"%{value}%", f"%{value}%", value, min(max(limit, 1), 30)),
                    )
                )
            elif entity_type == "topic":
                candidates = repo._rows(
                    conn.execute(
                        """
                        SELECT label_zh AS topic,COUNT(DISTINCT doi) AS paper_count
                        FROM paper_keywords WHERE label_zh LIKE ?
                        GROUP BY label_zh ORDER BY paper_count DESC,label_zh LIMIT ?
                        """,
                        (f"%{value}%", min(max(limit, 1), 30)),
                    )
                )
        for row in candidates:
            label = str(
                row.get("identifier")
                or row.get("name_zh")
                or row.get("name")
                or row.get("title_zh")
                or row.get("topic")
                or ""
            )
            row["match"] = "exact" if value.lower() in {label.lower(), str(row.get("doi") or "").lower()} else "partial"
        exact = [row for row in candidates if row.get("match") == "exact"]
        status = "complete" if len(exact) == 1 or len(candidates) == 1 else (
            "ambiguous" if candidates else "unsupported"
        )
        evidence = [
            EvidenceRef(
                evidence_type="entity",
                source="sqlite",
                ref_id=str(
                    row.get("identifier")
                    or row.get("institution_id")
                    or row.get("doi")
                    or row.get("topic")
                    or ""
                ),
                title=row.get("name_zh") or row.get("name") or row.get("title_zh") or row.get("topic"),
                year=row.get("year"),
            )
            for row in candidates
        ]
        return self._response(
            repo,
            status=status,
            scope={"journal_id": journal_id, "entity_type": entity_type, "query": value},
            data={"candidates": candidates, "candidate_count": len(candidates)},
            evidence_refs=evidence,
            limitations=[] if candidates else ["本刊当前数据中未找到匹配实体。"],
        )

    def search_papers(
        self,
        scope: PublicationScope,
        limit: int = 20,
        cursor: int = 0,
    ) -> ToolResponse:
        repo = self._repo(scope.journal_id)
        page_size = min(max(int(limit), 1), 100)
        offset = max(int(cursor), 0)
        rows, total = self._search_rows(repo, scope, limit=page_size, offset=offset)
        next_cursor = offset + len(rows) if offset + len(rows) < total else None
        limitations = self._scope_limitations(scope)
        return self._response(
            repo,
            status="partial" if limitations else "complete",
            scope=scope.model_dump(),
            data={
                "papers": rows,
                "total_count": total,
                "shown_count": len(rows),
                "cursor": offset,
            },
            evidence_refs=self._paper_evidence(rows),
            limitations=limitations,
            next_cursor=next_cursor,
        )

    def semantic_search_papers(
        self,
        query: str,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        top_k: int = 10,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
    ) -> ToolResponse:
        repo = self._repo(journal_id)
        if not query.strip():
            return self._response(
                repo,
                status="ambiguous",
                scope={"journal_id": journal_id},
                missing_inputs=["query"],
            )
        try:
            result = self._semantic_search(
                question=query,
                top_k=min(max(int(top_k), 1), 50),
                year_start=year_start,
                year_end=year_end,
                journal_id=journal_id,
            )
            hits = result.get("hits") or []
            return self._response(
                repo,
                scope={
                    "journal_id": journal_id,
                    "query": query,
                    "year_start": year_start,
                    "year_end": year_end,
                },
                data={
                    "papers": hits,
                    "queries": result.get("queries") or [query],
                    "dropped_editorial": result.get("dropped_editorial", 0),
                    "dropped_out_of_window": result.get("dropped_out_of_window", 0),
                },
                evidence_refs=self._paper_evidence(hits, source="chroma"),
                limitations=[str(result["where_error"])] if result.get("where_error") else [],
            )
        except Exception as exc:
            return self._response(
                repo,
                status="partial",
                scope={"journal_id": journal_id, "query": query},
                limitations=[f"语义检索当前不可用：{exc}"],
                data={"papers": []},
            )

    def get_paper_details(
        self,
        doi: str,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
    ) -> ToolResponse:
        repo = self._repo(journal_id)
        paper = self._paper_details(repo, doi.strip())
        if not paper:
            return self._response(
                repo,
                status="unsupported",
                scope={"journal_id": journal_id, "doi": doi},
                limitations=["本刊当前数据中未找到该 DOI。"],
            )
        return self._response(
            repo,
            scope={"journal_id": journal_id, "doi": doi},
            data={"paper": paper},
            evidence_refs=self._paper_evidence([paper]),
        )

    def extract_research_features(
        self,
        research: ResearchDescription,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
    ) -> ToolResponse:
        repo = self._repo(journal_id)
        text = "\n".join([research.title, research.abstract, *research.keywords, *research.topics])
        explicit = list(
            dict.fromkeys(
                term.strip()
                for term in [*research.topics, *research.keywords]
                if term.strip()
            )
        )
        journal_terms = [row["keyword"] for row in repo.top_keywords(400)]
        matched = [term for term in journal_terms if len(term) >= 2 and term in text]
        methods = list(
            dict.fromkeys(
                [*research.methods, *[term for term in _METHOD_TERMS if term.lower() in text.lower()]]
            )
        )
        sentences = [
            sentence.strip()
            for sentence in re.split(r"[。！？!?；;\n]+", research.abstract)
            if sentence.strip()
        ]
        questions = [
            sentence
            for sentence in sentences
            if re.search(r"如何|是否|影响|机制|关系|问题|探讨|研究", sentence)
        ][:5]
        conclusions = [
            sentence
            for sentence in sentences
            if re.search(r"结果表明|研究发现|结论|显示|表明", sentence)
        ][:5]
        topics = list(dict.fromkeys([*explicit, *matched]))[:20]
        evidence_spans = [
            {"text": sentence, "source": "abstract"}
            for sentence in [*questions[:3], *conclusions[:3]]
        ]
        confidence = "high" if len(explicit) >= 2 or len(matched) >= 3 else (
            "medium" if explicit or matched else "low"
        )
        return self._response(
            repo,
            status="complete" if topics else "partial",
            scope={"journal_id": journal_id},
            data={
                "title": research.title,
                "topics": topics,
                "explicit_terms": explicit,
                "journal_vocabulary_matches": matched[:20],
                "research_objects": research.research_objects,
                "methods": methods,
                "research_questions": questions,
                "conclusions": conclusions,
                "evidence_spans": evidence_spans,
                "confidence": confidence,
                "extraction_method": "deterministic-v1",
            },
            limitations=[
                "该工具执行确定性特征抽取；隐含主题可能需要通用 Agent 根据原文补充。"
            ] if confidence == "low" else [],
        )

    def aggregate_publications(
        self,
        scope: PublicationScope,
        group_by: Literal["year", "topic", "author", "institution"],
        limit: int = 50,
    ) -> ToolResponse:
        repo = self._repo(scope.journal_id)
        clauses, params = self._scope_where(scope)
        where = " AND ".join(clauses)
        row_limit = min(max(int(limit), 1), 200)
        total = self._matching_paper_count(repo, scope)
        with repo._conn() as conn:
            if group_by == "year":
                rows = repo._rows(
                    conn.execute(
                        f"""
                        SELECT p.year AS group_value,COUNT(DISTINCT p.doi) AS paper_count
                        FROM papers p WHERE {where} AND p.year IS NOT NULL
                        GROUP BY p.year ORDER BY p.year
                        """,
                        params,
                    )
                )
            elif group_by == "topic":
                rows = repo._rows(
                    conn.execute(
                        f"""
                        SELECT pk.label_zh AS group_value,COUNT(DISTINCT p.doi) AS paper_count
                        FROM papers p JOIN paper_keywords pk ON pk.doi=p.doi
                        WHERE {where} AND pk.label_zh IS NOT NULL AND pk.label_zh<>''
                        GROUP BY pk.label_zh ORDER BY paper_count DESC,group_value
                        """,
                        params,
                    )
                )
            elif group_by == "author":
                rows = repo._rows(
                    conn.execute(
                        f"""
                        SELECT a.author_id AS group_id,COALESCE(a.name_zh,a.name_en) AS group_value,
                               COUNT(DISTINCT p.doi) AS paper_count
                        FROM papers p JOIN paper_authors pa ON pa.doi=p.doi
                        JOIN authors a ON a.author_id=pa.author_id
                        WHERE {where}
                        GROUP BY a.author_id,group_value
                        ORDER BY paper_count DESC,group_value
                        """,
                        params,
                    )
                )
            else:
                raw = repo._rows(
                    conn.execute(
                        f"""
                        SELECT i.institution_id AS group_id,i.name_norm AS group_value,p.doi
                        FROM papers p JOIN author_institutions ai ON ai.doi=p.doi
                        JOIN institutions i ON i.institution_id=ai.institution_id
                        WHERE {where} AND i.name_norm IS NOT NULL AND i.name_norm<>''
                        """,
                        params,
                    )
                )
                grouped: Dict[str, set] = {}
                for row in raw:
                    name = _normalize_institution_name(str(row.get("group_value") or ""))
                    if name and row.get("doi"):
                        grouped.setdefault(name, set()).add(row["doi"])
                rows = [
                    {
                        "group_id": f"normalized:{name}",
                        "group_value": name,
                        "paper_count": len(dois),
                    }
                    for name, dois in grouped.items()
                ]
                rows.sort(key=lambda row: (-int(row["paper_count"]), str(row["group_value"])))
        for row in rows:
            count = int(row.get("paper_count") or 0)
            row["paper_count"] = count
            row["coverage_share_pct"] = round(count / total * 100, 2) if total else 0.0
        concentration = sum((float(row["coverage_share_pct"]) / 100) ** 2 for row in rows)
        displayed = rows[:row_limit]
        limitations = self._scope_limitations(scope)
        return self._response(
            repo,
            status="partial" if limitations else "complete",
            scope={**scope.model_dump(), "group_by": group_by},
            data={
                "total_papers": total,
                "groups": displayed,
                "group_count": len(rows),
                "concentration_index": round(concentration, 4),
                "share_semantics": "每组覆盖论文数/范围内去重论文总数；多值维度占比之和可能超过100%",
            },
            evidence_refs=[
                EvidenceRef(
                    evidence_type="statistic",
                    source="sqlite",
                    ref_id=f"{scope.journal_id}:{group_by}",
                    details={"total_papers": total, "group_count": len(rows)},
                )
            ],
            limitations=limitations,
        )

    def analyze_publication_trend(
        self,
        scope: PublicationScope,
    ) -> ToolResponse:
        repo = self._repo(scope.journal_id)
        aggregate = self.aggregate_publications(scope, "year", limit=200)
        raw = aggregate.data.get("groups") or []
        by_year = {int(row["group_value"]): int(row["paper_count"]) for row in raw}
        if by_year:
            start = scope.year_start if scope.year_start is not None else min(by_year)
            end = scope.year_end if scope.year_end is not None else max(by_year)
        else:
            start = scope.year_start
            end = scope.year_end
        if start is None or end is None:
            return self._response(
                repo,
                status="partial",
                scope=scope.model_dump(),
                data={"yearly": [], "classification": "insufficient_data"},
                limitations=["范围内没有可用于趋势计算的年度数据。"],
            )
        series = [
            {"year": year, "paper_count": by_year.get(year, 0)}
            for year in range(int(start), int(end) + 1)
        ]
        xs = list(range(len(series)))
        ys = [float(row["paper_count"]) for row in series]
        n = len(xs)
        x_mean = sum(xs) / n if n else 0.0
        y_mean = sum(ys) / n if n else 0.0
        denominator = sum((x - x_mean) ** 2 for x in xs)
        slope = (
            sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
            if denominator
            else 0.0
        )
        normalized_slope = slope / y_mean if y_mean else 0.0
        if n < 3 or sum(ys) == 0:
            classification = "insufficient_data"
        elif normalized_slope >= 0.05:
            classification = "up"
        elif normalized_slope <= -0.05:
            classification = "down"
        else:
            classification = "stable"
        yoy: List[Dict[str, Any]] = []
        previous: Optional[int] = None
        for row in series:
            current = int(row["paper_count"])
            yoy.append(
                {
                    **row,
                    "delta": current - previous if previous is not None else None,
                    "yoy_pct": round((current - previous) / previous * 100, 2)
                    if previous
                    else None,
                }
            )
            previous = current
        first = ys[0] if ys else 0
        last = ys[-1] if ys else 0
        cagr = (
            round(((last / first) ** (1 / (n - 1)) - 1) * 100, 2)
            if n > 1 and first > 0 and last >= 0
            else None
        )
        limitations = list(aggregate.limitations)
        if classification == "insufficient_data":
            limitations.append("有效年度少于3年或发文量为0，不能形成稳定趋势判断。")
        return self._response(
            repo,
            status="partial" if limitations else "complete",
            scope=scope.model_dump(),
            data={
                "yearly": yoy,
                "slope_papers_per_year": round(slope, 4),
                "normalized_slope": round(normalized_slope, 4),
                "cagr_pct": cagr,
                "classification": classification,
                "formula_version": "linear-trend-v1",
                "thresholds": {
                    "up": "normalized_slope >= 0.05",
                    "stable": "-0.05 < normalized_slope < 0.05",
                    "down": "normalized_slope <= -0.05",
                    "minimum_years": 3,
                },
            },
            evidence_refs=aggregate.evidence_refs,
            limitations=limitations,
        )

    def compare_publication_sets(
        self,
        set_a: CompareSet,
        set_b: CompareSet,
        topic_limit: int = 30,
    ) -> ToolResponse:
        if set_a.scope.journal_id != set_b.scope.journal_id:
            repo = self._repo(set_a.scope.journal_id)
            return self._response(
                repo,
                status="unsupported",
                scope={"set_a": set_a.model_dump(), "set_b": set_b.model_dump()},
                limitations=["MVP仅支持同一期刊内的论文集比较。"],
            )
        repo = self._repo(set_a.scope.journal_id)
        result_limit = min(max(int(topic_limit), 1), 100)
        agg_a = self.aggregate_publications(set_a.scope, "topic", limit=200)
        agg_b = self.aggregate_publications(set_b.scope, "topic", limit=200)
        map_a = {row["group_value"]: row for row in agg_a.data.get("groups") or []}
        map_b = {row["group_value"]: row for row in agg_b.data.get("groups") or []}
        rows = []
        for topic in set(map_a) | set(map_b):
            a = map_a.get(topic, {})
            b = map_b.get(topic, {})
            a_count = int(a.get("paper_count") or 0)
            b_count = int(b.get("paper_count") or 0)
            a_share = float(a.get("coverage_share_pct") or 0)
            b_share = float(b.get("coverage_share_pct") or 0)
            rows.append(
                {
                    "topic": topic,
                    "set_a_count": a_count,
                    "set_b_count": b_count,
                    "count_delta": b_count - a_count,
                    "set_a_share_pct": a_share,
                    "set_b_share_pct": b_share,
                    "share_delta_pp": round(b_share - a_share, 2),
                    "change": "new" if not a_count and b_count else (
                        "disappeared" if a_count and not b_count else (
                            "strengthened" if b_share > a_share else (
                                "weakened" if b_share < a_share else "unchanged"
                            )
                        )
                    ),
                }
            )
        rows.sort(key=lambda row: (-abs(float(row["share_delta_pp"])), row["topic"]))
        rep_a, _ = self._search_rows(repo, set_a.scope, limit=5, offset=0)
        rep_b, _ = self._search_rows(repo, set_b.scope, limit=5, offset=0)
        limitations = list(dict.fromkeys([*agg_a.limitations, *agg_b.limitations]))
        return self._response(
            repo,
            status="partial" if limitations else "complete",
            scope={"set_a": set_a.model_dump(), "set_b": set_b.model_dump()},
            data={
                "set_a": {"label": set_a.label, "total_papers": agg_a.data.get("total_papers")},
                "set_b": {"label": set_b.label, "total_papers": agg_b.data.get("total_papers")},
                "topic_changes": rows[:result_limit],
                "new_topics": [row for row in rows if row["change"] == "new"][:result_limit],
                "disappeared_topics": [row for row in rows if row["change"] == "disappeared"][:result_limit],
                "representative_papers": {set_a.label: rep_a, set_b.label: rep_b},
            },
            evidence_refs=self._paper_evidence([*rep_a, *rep_b]),
            limitations=limitations,
        )

    def rank_contributors(
        self,
        entity_type: Literal["author", "institution"],
        scope: PublicationScope,
        ranking_basis: Literal["publication_count", "continuity", "topic_coverage", "collaboration"] = "publication_count",
        limit: int = 10,
    ) -> ToolResponse:
        repo = self._repo(scope.journal_id)
        top_n = min(max(int(limit), 1), 50)
        if ranking_basis == "publication_count":
            aggregate = self.aggregate_publications(scope, entity_type, limit=top_n)
            rows = aggregate.data.get("groups") or []
            limitations = list(aggregate.limitations)
        elif ranking_basis == "continuity":
            rows = self._rank_continuity(repo, entity_type, scope, top_n)
            limitations = self._scope_limitations(scope)
        elif ranking_basis == "topic_coverage":
            if not scope.topics:
                return self._response(
                    repo,
                    status="ambiguous",
                    scope={**scope.model_dump(), "entity_type": entity_type},
                    missing_inputs=["scope.topics"],
                )
            rows = self._rank_topic_coverage(repo, entity_type, scope, top_n)
            limitations = self._scope_limitations(scope)
        else:
            network = (
                repo.author_network(scope.topics[0] if scope.topics else None, 500, scope.year_start, scope.year_end)
                if entity_type == "author"
                else repo.institution_network(scope.topics[0] if scope.topics else None, 500, scope.year_start, scope.year_end)
            )
            scores: Dict[str, Dict[str, Any]] = {}
            for edge in network.get("edges") or []:
                weight = int(edge.get("paper_count") or 0)
                for side in ("source", "target"):
                    key = str(edge.get(f"{side}_id") or edge.get(f"{side}_name") or "")
                    if not key:
                        continue
                    item = scores.setdefault(
                        key,
                        {
                            "entity_id": key,
                            "name": edge.get(f"{side}_name"),
                            "weighted_collaboration_count": 0,
                            "collaborator_count": 0,
                        },
                    )
                    item["weighted_collaboration_count"] += weight
                    item["collaborator_count"] += 1
            rows = sorted(
                scores.values(),
                key=lambda row: (
                    -int(row["weighted_collaboration_count"]),
                    -int(row["collaborator_count"]),
                    str(row.get("name") or ""),
                ),
            )[:top_n]
            limitations = self._scope_limitations(scope)
        return self._response(
            repo,
            status="partial" if limitations else "complete",
            scope={**scope.model_dump(), "entity_type": entity_type},
            data={
                "contributors": rows[:top_n],
                "ranking_basis": ranking_basis,
                "ranking_basis_definition": {
                    "publication_count": "范围内去重论文数",
                    "continuity": "活跃年度数及活跃年度占比",
                    "topic_coverage": "命中的请求主题数，其次为去重论文数",
                    "collaboration": "合作边论文权重之和，其次为合作者数量",
                }[ranking_basis],
            },
            evidence_refs=[
                EvidenceRef(
                    evidence_type="statistic",
                    source="sqlite",
                    ref_id=f"{scope.journal_id}:{entity_type}:{ranking_basis}",
                    details={"returned": len(rows)},
                )
            ],
            limitations=limitations,
        )

    def _rank_continuity(
        self,
        repo: SQLiteRepo,
        entity_type: str,
        scope: PublicationScope,
        limit: int,
    ) -> List[Dict[str, Any]]:
        clauses, params = self._scope_where(scope)
        where = " AND ".join(clauses)
        if entity_type == "author":
            sql = f"""
                SELECT a.author_id AS entity_id,COALESCE(a.name_zh,a.name_en) AS name,
                       COUNT(DISTINCT p.doi) AS paper_count,
                       COUNT(DISTINCT p.year) AS active_years,MIN(p.year) AS first_year,MAX(p.year) AS last_year
                FROM papers p JOIN paper_authors pa ON pa.doi=p.doi
                JOIN authors a ON a.author_id=pa.author_id
                WHERE {where} AND p.year IS NOT NULL
                GROUP BY a.author_id,name
            """
        else:
            sql = f"""
                SELECT i.institution_id AS entity_id,i.name_norm AS name,
                       COUNT(DISTINCT p.doi) AS paper_count,
                       COUNT(DISTINCT p.year) AS active_years,MIN(p.year) AS first_year,MAX(p.year) AS last_year
                FROM papers p JOIN author_institutions ai ON ai.doi=p.doi
                JOIN institutions i ON i.institution_id=ai.institution_id
                WHERE {where} AND p.year IS NOT NULL
                GROUP BY i.institution_id,name
            """
        with repo._conn() as conn:
            rows = repo._rows(conn.execute(sql, params))
        if scope.year_start is not None and scope.year_end is not None:
            window_years = scope.year_end - scope.year_start + 1
        else:
            years = [int(row["first_year"]) for row in rows if row.get("first_year") is not None]
            ends = [int(row["last_year"]) for row in rows if row.get("last_year") is not None]
            window_years = max(ends) - min(years) + 1 if years and ends else 0
        for row in rows:
            row["active_year_rate"] = round(int(row["active_years"]) / window_years, 3) if window_years else 0
            if entity_type == "institution":
                row["name"] = _normalize_institution_name(str(row.get("name") or ""))
        rows.sort(
            key=lambda row: (
                -float(row["active_year_rate"]),
                -int(row["active_years"]),
                -int(row["paper_count"]),
                str(row.get("name") or ""),
            )
        )
        return rows[:limit]

    def _rank_topic_coverage(
        self,
        repo: SQLiteRepo,
        entity_type: str,
        scope: PublicationScope,
        limit: int,
    ) -> List[Dict[str, Any]]:
        by_entity: Dict[str, Dict[str, Any]] = {}
        for topic in scope.topics:
            if entity_type == "author":
                result = repo.authors_by_keyword(
                    topic,
                    author_limit=max(limit * 5, 50),
                    papers_per_author=100,
                    start_year=scope.year_start,
                    end_year=scope.year_end,
                )
                candidates = result.get("authors") or []
                id_key, name_key = "author_id", "name_zh"
            else:
                result = repo.institutions_by_keyword(
                    topic,
                    limit=max(limit * 5, 50),
                    start_year=scope.year_start,
                    end_year=scope.year_end,
                )
                candidates = result.get("institutions") or []
                id_key, name_key = "institution", "institution"
            for candidate in candidates:
                key = str(candidate.get(id_key) or candidate.get(name_key) or "")
                if not key:
                    continue
                item = by_entity.setdefault(
                    key,
                    {
                        "entity_id": key,
                        "name": candidate.get(name_key) or candidate.get("name_en"),
                        "topics": [],
                        "paper_count": 0,
                    },
                )
                item["topics"].append(topic)
                item["paper_count"] += int(candidate.get("paper_count") or 0)
        rows = list(by_entity.values())
        for row in rows:
            row["topic_coverage"] = len(set(row["topics"]))
            row["topics"] = list(dict.fromkeys(row["topics"]))
        rows.sort(
            key=lambda row: (
                -int(row["topic_coverage"]),
                -int(row["paper_count"]),
                str(row.get("name") or ""),
            )
        )
        return rows[:limit]

    def get_contributor_profile(
        self,
        entity_type: Literal["author", "institution"],
        identifier: str,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
    ) -> ToolResponse:
        repo = self._repo(journal_id)
        value = identifier.strip()
        if not value:
            return self._response(
                repo,
                status="ambiguous",
                scope={"journal_id": journal_id, "entity_type": entity_type},
                missing_inputs=["identifier"],
            )
        if entity_type == "author":
            # Author names are the public business identifier. Database author
            # IDs remain an internal implementation detail and are not accepted
            # as tool-chain inputs.
            name = value
            data = repo.author_profile(name)
            author_scope = PublicationScope(
                journal_id=journal_id,
                year_start=year_start,
                year_end=year_end,
                author=name,
            )
            papers, total = self._search_rows(repo, author_scope, limit=100, offset=0)
            yearly = self.aggregate_publications(author_scope, "year", limit=200)
            topics = self.aggregate_publications(author_scope, "topic", limit=20)
            data.update(
                {
                    "papers": papers,
                    "recent_papers": papers,
                    "total_papers": total,
                    "yearly": yearly.data.get("groups") or [],
                    "keywords": topics.data.get("groups") or [],
                    "result_truncated": total > len(papers),
                }
            )
        else:
            data = repo.institution_authors_with_papers(
                value.replace("normalized:", ""),
                top_authors=12,
                papers_per_author=5,
                start_year=year_start,
                end_year=year_end,
            )
            papers = data.get("papers") or []
        found = bool(data.get("author") if entity_type == "author" else data.get("total_papers"))
        return self._response(
            repo,
            status="complete" if found else "unsupported",
            scope={
                "journal_id": journal_id,
                "entity_type": entity_type,
                "identifier": identifier,
                "year_start": year_start,
                "year_end": year_end,
            },
            data={"profile": data},
            evidence_refs=self._paper_evidence(papers),
            limitations=[] if found else ["本刊当前数据中未找到该贡献者。"],
        )

    def get_collaboration_network(
        self,
        entity_type: Literal["author", "institution", "topic"],
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        identifier: Optional[str] = None,
        topic: Optional[str] = None,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
        limit: int = 30,
    ) -> ToolResponse:
        repo = self._repo(journal_id)
        top_n = min(max(int(limit), 1), 100)
        if entity_type == "author" and identifier:
            author = repo.resolve_author(identifier)
            if not author:
                return self._response(
                    repo,
                    status="unsupported",
                    scope={"journal_id": journal_id, "identifier": identifier},
                    limitations=["本刊当前数据中未找到该作者。"],
                )
            network = repo.author_collaborators_for_ids(
                [author["author_id"]], top_n, year_start, year_end
            )
            papers = [
                paper
                for row in network.get("collaborators") or []
                for paper in row.get("papers") or []
            ]
        elif entity_type == "institution":
            network = repo.institution_network(topic, max(top_n * 10, 100), year_start, year_end)
            if identifier:
                needle = identifier.replace("normalized:", "")
                network["edges"] = [
                    edge
                    for edge in network.get("edges") or []
                    if needle in str(edge.get("source_name") or "")
                    or needle in str(edge.get("target_name") or "")
                ][:top_n]
            else:
                network["edges"] = (network.get("edges") or [])[:top_n]
            papers = [
                paper
                for edge in network.get("edges") or []
                for paper in edge.get("papers") or []
            ]
        else:
            selected_topic = topic or identifier
            network = repo.author_network(selected_topic, top_n, year_start, year_end)
            papers = [
                paper
                for edge in network.get("edges") or []
                for paper in edge.get("papers") or []
            ]
        return self._response(
            repo,
            scope={
                "journal_id": journal_id,
                "entity_type": entity_type,
                "identifier": identifier,
                "topic": topic,
                "year_start": year_start,
                "year_end": year_end,
            },
            data={"network": network},
            evidence_refs=self._paper_evidence(papers),
        )

    def assess_research_fit(
        self,
        research: ResearchDescription,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
    ) -> ToolResponse:
        repo = self._repo(journal_id)
        extracted = self.extract_research_features(research, journal_id)
        terms = list(
            dict.fromkeys(
                [
                    *research.research_objects,
                    *research.topics,
                    *research.keywords,
                    *(extracted.data.get("topics") or []),
                ]
            )
        )[:12]
        if not terms:
            return self._response(
                repo,
                status="ambiguous",
                scope={"journal_id": journal_id},
                missing_inputs=["research.topics or research.keywords"],
            )
        stats = repo.topic_keyword_stats(terms, year_start, year_end)
        rows = stats.get("topic_keywords") or []
        specific_rows = [row for row in rows if row.get("keyword") not in _GENERIC_METHOD_TERMS]
        scored_rows = specific_rows or rows
        nonzero = sum(1 for row in scored_rows if int(row.get("paper_count") or 0) > 0)
        coverage = nonzero / len(scored_rows) if scored_rows else 0.0
        effective_hits = sum(int(row.get("paper_count") or 0) for row in scored_rows)
        score = round(coverage * 60 + min(effective_hits, 20) / 20 * 40, 1)
        fit_band = "strong" if score >= 70 else ("moderate" if score >= 35 else "weak")
        direct_papers: List[Dict[str, Any]] = []
        for term in terms[:6]:
            direct_papers.extend(repo.papers_by_keyword(term, 3, year_start, year_end))
        direct_papers = self._dedupe_papers(direct_papers)[:12]
        adjacent: List[Dict[str, Any]] = []
        semantic_limitation: List[str] = []
        query = " ".join(
            part for part in [research.title, *terms, *research.methods] if part
        )
        try:
            semantic = self._semantic_search(
                question=query,
                top_k=8,
                year_start=year_start,
                year_end=year_end,
                journal_id=journal_id,
            )
            adjacent = semantic.get("hits") or []
        except Exception as exc:
            semantic_limitation.append(f"语义相似论文检索不可用：{exc}")
        return self._response(
            repo,
            status="partial" if semantic_limitation else "complete",
            scope={
                "journal_id": journal_id,
                "year_start": year_start,
                "year_end": year_end,
            },
            data={
                "fit_band": fit_band,
                "fit_score": score,
                "score_version": "journal-history-fit-v1",
                "score_formula": "60%有效主题覆盖率 + 40%历史命中量（20篇封顶）",
                "effective_terms": [row.get("keyword") for row in scored_rows],
                "term_evidence": rows,
                "direct_match_papers": direct_papers,
                "adjacent_match_papers": adjacent,
                "interpretation": "仅表示与本刊历史内容的匹配程度，不表示录用概率或学术质量。",
            },
            evidence_refs=self._paper_evidence([*direct_papers, *adjacent]),
            limitations=semantic_limitation,
            unsupported_claims=["录用概率", "稿件学术质量", "相对于全球文献的创新性"],
        )

    def rank_recommended_papers(
        self,
        research: ResearchDescription,
        journal_id: JournalId = DEFAULT_JOURNAL_ID,
        goal: Literal["submission_preparation", "topic_learning", "method_reference"] = "submission_preparation",
        limit: int = 10,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
    ) -> ToolResponse:
        repo = self._repo(journal_id)
        extracted = self.extract_research_features(research, journal_id)
        terms = list(
            dict.fromkeys(
                [
                    *research.topics,
                    *research.keywords,
                    *research.methods,
                    *(extracted.data.get("topics") or []),
                ]
            )
        )[:12]
        query = " ".join(part for part in [research.title, *terms] if part)
        candidates: List[Dict[str, Any]] = []
        limitations: List[str] = []
        try:
            semantic = self._semantic_search(
                question=query,
                top_k=min(max(limit * 2, 10), 50),
                year_start=year_start,
                year_end=year_end,
                journal_id=journal_id,
            )
            for rank, hit in enumerate(semantic.get("hits") or [], 1):
                distance = hit.get("distance")
                hit = dict(hit)
                hit["recommendation_score"] = round(1 / (1 + max(float(distance), 0)), 4) if distance is not None else round(1 / rank, 4)
                hit["reason"] = "与研究描述语义相似"
                hit["match_type"] = "semantic"
                candidates.append(hit)
        except Exception as exc:
            limitations.append(f"语义排序不可用，已使用结构化主题命中回退：{exc}")
        for term in terms[:8]:
            for paper in repo.papers_by_keyword(term, 5, year_start, year_end):
                item = dict(paper)
                item.setdefault("recommendation_score", 0.5)
                item.setdefault("reason", f"命中本刊主题词：{term}")
                item.setdefault("match_type", "structured_topic")
                candidates.append(item)
        ranked = self._dedupe_papers(candidates)
        ranked.sort(
            key=lambda row: (
                -float(row.get("recommendation_score") or 0),
                -(int(row.get("year") or 0)),
                str(row.get("doi") or ""),
            )
        )
        ranked = ranked[: min(max(int(limit), 1), 50)]
        return self._response(
            repo,
            status="partial" if limitations else "complete",
            scope={
                "journal_id": journal_id,
                "goal": goal,
                "year_start": year_start,
                "year_end": year_end,
            },
            data={
                "recommended_papers": ranked,
                "ranking_goal": goal,
                "ranking_basis": "语义相似度优先，结构化主题命中作为回退",
                "query_terms": terms,
            },
            evidence_refs=self._paper_evidence(ranked),
            limitations=limitations,
        )

    @staticmethod
    def _dedupe_papers(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        positions: Dict[str, int] = {}
        for row in rows:
            key = str(row.get("doi") or row.get("title") or row.get("title_zh") or "")
            if not key:
                continue
            if key in positions:
                current = out[positions[key]]
                if float(row.get("recommendation_score") or 0) > float(
                    current.get("recommendation_score") or 0
                ):
                    out[positions[key]] = row
                continue
            positions[key] = len(out)
            out.append(row)
        return out
