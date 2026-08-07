from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from neo4j import GraphDatabase

from app.config import Settings, get_settings
from app.utils import doi_url


class Neo4jRepo:
    _NETWORK_CACHE_TTL = 120.0
    _NETWORK_CACHE_MAX = 48

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(self.settings.neo4j_user, self.settings.neo4j_password),
            connection_timeout=8,
            max_connection_lifetime=300,
            max_connection_pool_size=32,
        )
        self._network_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def close(self) -> None:
        self._driver.close()

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        hit = self._network_cache.get(key)
        if not hit:
            return None
        ts, payload = hit
        if time.monotonic() - ts > self._NETWORK_CACHE_TTL:
            self._network_cache.pop(key, None)
            return None
        return payload

    def _cache_set(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if len(self._network_cache) >= self._NETWORK_CACHE_MAX:
            oldest = min(self._network_cache.items(), key=lambda kv: kv[1][0])[0]
            self._network_cache.pop(oldest, None)
        self._network_cache[key] = (time.monotonic(), payload)
        return payload

    def _run_query(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        with self._driver.session() as session:
            return [dict(r) for r in session.run(query, **params)]

    def _run_parallel(
        self, jobs: List[Tuple[str, Dict[str, Any]]]
    ) -> List[List[Dict[str, Any]]]:
        """Run independent Cypher jobs concurrently (one session each)."""
        if not jobs:
            return []
        if len(jobs) == 1:
            q, params = jobs[0]
            return [self._run_query(q, **params)]

        def _one(job: Tuple[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
            q, params = job
            return self._run_query(q, **params)

        workers = min(8, len(jobs))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(_one, jobs))

    def paper_neighborhood(self, doi: str) -> Dict[str, Any]:
        q = """
        MATCH (p:Paper {doi: $doi})
        OPTIONAL MATCH (p)-[ra:AUTHORED_BY]->(a:Author)
        OPTIONAL MATCH (p)-[:HAS_KEYWORD]->(k:Keyword)
        OPTIONAL MATCH (p)-[:HAS_AFFILIATION]->(i:Institution)
        OPTIONAL MATCH (p)-[:FUNDED_BY]->(f:Fund)
        OPTIONAL MATCH (p)-[:HAS_CLC]->(c:CLC)
        RETURN p.title_zh AS title, p.year AS year,
               collect(DISTINCT {
                 author_id: a.author_id, name: a.name_zh, order: ra.author_order,
                 corresponding: ra.is_corresponding
               }) AS authors,
               collect(DISTINCT k.label_zh) AS keywords,
               collect(DISTINCT i.name_norm) AS institutions,
               collect(DISTINCT f.agency_norm) AS funds,
               collect(DISTINCT c.code) AS clc
        """
        with self._driver.session() as session:
            rec = session.run(q, doi=doi).single()
        if not rec:
            return {}
        data = dict(rec)
        data["authors"] = [
            a for a in (data.get("authors") or []) if a.get("author_id")
        ]
        data["keywords"] = [k for k in (data.get("keywords") or []) if k]
        data["institutions"] = [i for i in (data.get("institutions") or []) if i]
        data["funds"] = [f for f in (data.get("funds") or []) if f]
        data["clc"] = [c for c in (data.get("clc") or []) if c]
        data["doi"] = doi
        data["url"] = doi_url(doi)
        return data

    def author_collaborators(
        self, author_id: Optional[str] = None, name: Optional[str] = None, limit: int = 20
    ) -> Dict[str, Any]:
        if not author_id and not name:
            return {}
        find_q = """
        MATCH (a:Author)
        WHERE ($author_id IS NOT NULL AND a.author_id = $author_id)
           OR ($name IS NOT NULL AND (a.name_zh CONTAINS $name OR a.name_en CONTAINS $name))
        RETURN a.author_id AS author_id, a.name_zh AS name_zh, a.name_en AS name_en,
               a.paper_count AS paper_count
        ORDER BY a.paper_count DESC
        LIMIT 5
        """
        with self._driver.session() as session:
            authors = [dict(r) for r in session.run(find_q, author_id=author_id, name=name)]
            if not authors:
                return {"authors": [], "collaborators": []}
            aid = authors[0]["author_id"]
            collab_q = """
            MATCH (a:Author {author_id: $author_id})<-[:AUTHORED_BY]-(p:Paper)-[:AUTHORED_BY]->(c:Author)
            WHERE c.author_id <> a.author_id
            RETURN c.author_id AS author_id, c.name_zh AS name_zh, c.name_en AS name_en,
                   count(DISTINCT p) AS co_papers
            ORDER BY co_papers DESC
            LIMIT $limit
            """
            collaborators = [
                dict(r) for r in session.run(collab_q, author_id=aid, limit=limit)
            ]
        return {"author": authors[0], "candidates": authors, "collaborators": collaborators}

    def keyword_related_papers(self, keyword: str, limit: int = 15) -> List[Dict[str, Any]]:
        q = """
        MATCH (k:Keyword)<-[:HAS_KEYWORD]-(p:Paper)
        WHERE k.label_zh CONTAINS $keyword OR k.label_en CONTAINS $keyword
        RETURN p.doi AS doi, p.title_zh AS title, p.year AS year, k.label_zh AS keyword
        ORDER BY p.year DESC
        LIMIT $limit
        """
        with self._driver.session() as session:
            rows = [dict(r) for r in session.run(q, keyword=keyword, limit=limit)]
        for row in rows:
            row["url"] = doi_url(row.get("doi"))
        return rows

    @staticmethod
    def _short(text: str, n: int = 16) -> str:
        t = (text or "").strip()
        return t if len(t) <= n else t[: n - 1] + "…"

    def _graph_builder(self) -> "_GraphBuilder":
        return _GraphBuilder()

    def author_network(self, name: str, limit: int = 20) -> Dict[str, Any]:
        """Author ego graph with 2-hop theme bridges: author→keyword→author."""
        cache_key = f"author:{(name or '').strip()}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        find_q = """
        MATCH (a:Author)
        WHERE a.name_zh CONTAINS $name OR a.name_en CONTAINS $name
        RETURN a.author_id AS author_id, a.name_zh AS name_zh, a.name_en AS name_en,
               a.paper_count AS paper_count
        ORDER BY a.paper_count DESC
        LIMIT 5
        """
        collab_limit = max(5, min(limit, 12))
        paper_limit = max(5, min(limit // 2 + 3, 8))
        kw_limit = max(6, min(limit, 12))
        bridge_limit = max(12, min(limit + 8, 28))
        inst_limit = 6
        fund_limit = 4

        with self._driver.session() as session:
            authors = [dict(r) for r in session.run(find_q, name=name)]
            if not authors:
                return {"nodes": [], "edges": [], "author": None, "focus": "author"}
            author = authors[0]
            aid = author["author_id"]

            collab_q = """
            MATCH (a:Author {author_id: $author_id})<-[:AUTHORED_BY]-(p:Paper)-[:AUTHORED_BY]->(c:Author)
            WHERE c.author_id <> a.author_id
            RETURN c.author_id AS author_id, c.name_zh AS name_zh, c.name_en AS name_en,
                   count(DISTINCT p) AS co_papers
            ORDER BY co_papers DESC
            LIMIT $limit
            """
            papers_q = """
            MATCH (a:Author {author_id: $author_id})<-[:AUTHORED_BY]-(p:Paper)
            RETURN p.doi AS doi, p.title_zh AS title, p.year AS year
            ORDER BY p.year DESC
            LIMIT $limit
            """
            kw_q = """
            MATCH (a:Author {author_id: $author_id})<-[:AUTHORED_BY]-(p:Paper)-[:HAS_KEYWORD]->(k:Keyword)
            RETURN k.keyword_id AS keyword_id, k.label_zh AS label,
                   count(DISTINCT p) AS paper_count
            ORDER BY paper_count DESC
            LIMIT $limit
            """
            # 2-hop: hub's keywords → other authors who also use them
            theme_peers_q = """
            MATCH (a:Author {author_id: $author_id})<-[:AUTHORED_BY]-(p:Paper)-[:HAS_KEYWORD]->(k:Keyword)
            WITH k, count(DISTINCT p) AS hub_cnt
            ORDER BY hub_cnt DESC
            LIMIT $kw_limit
            MATCH (k)<-[:HAS_KEYWORD]-(p2:Paper)-[:AUTHORED_BY]->(o:Author)
            WHERE o.author_id <> $author_id
            WITH k, o, count(DISTINCT p2) AS shared
            ORDER BY shared DESC
            LIMIT $bridge_limit
            RETURN k.keyword_id AS keyword_id, k.label_zh AS label,
                   o.author_id AS author_id, o.name_zh AS name_zh, o.name_en AS name_en,
                   shared AS paper_count
            """
            inst_q = """
            MATCH (a:Author {author_id: $author_id})<-[:AUTHORED_BY]-(p:Paper)-[:HAS_AFFILIATION]->(i:Institution)
            RETURN i.institution_id AS institution_id, i.name_norm AS name,
                   count(DISTINCT p) AS link_count
            ORDER BY link_count DESC
            LIMIT $limit
            """
            fund_q = """
            MATCH (a:Author {author_id: $author_id})<-[:AUTHORED_BY]-(p:Paper)-[:FUNDED_BY]->(f:Fund)
            RETURN f.fund_id AS fund_id, f.agency_norm AS name,
                   count(DISTINCT p) AS paper_count
            ORDER BY paper_count DESC
            LIMIT $limit
            """
            paper_links_q = """
            MATCH (a:Author {author_id: $author_id})<-[:AUTHORED_BY]-(p:Paper)
            WITH p ORDER BY p.year DESC LIMIT $paper_limit
            OPTIONAL MATCH (p)-[:HAS_KEYWORD]->(k:Keyword)
            OPTIONAL MATCH (p)-[:AUTHORED_BY]->(c:Author)
            WHERE c.author_id <> $author_id
            RETURN p.doi AS doi,
                   collect(DISTINCT k.keyword_id)[0..8] AS keyword_ids,
                   collect(DISTINCT c.author_id)[0..6] AS coauthor_ids
            """

        (
            collaborators,
            papers,
            keywords,
            theme_peers,
            institutions,
            funds,
            paper_links,
        ) = self._run_parallel(
            [
                (collab_q, {"author_id": aid, "limit": collab_limit}),
                (papers_q, {"author_id": aid, "limit": paper_limit}),
                (kw_q, {"author_id": aid, "limit": kw_limit}),
                (
                    theme_peers_q,
                    {
                        "author_id": aid,
                        "kw_limit": kw_limit,
                        "bridge_limit": bridge_limit,
                    },
                ),
                (inst_q, {"author_id": aid, "limit": inst_limit}),
                (fund_q, {"author_id": aid, "limit": fund_limit}),
                (paper_links_q, {"author_id": aid, "paper_limit": paper_limit}),
            ]
        )

        hub_label = author.get("name_zh") or author.get("name_en") or name
        g = self._graph_builder()
        hub_id = g.add_node(
            "author",
            author["author_id"],
            hub_label,
            value=max(int(author.get("paper_count") or 1), 1),
            title=f"发文 {author.get('paper_count') or 0} 篇",
            query=hub_label,
        )
        collab_ids = set()
        author_node_ids: Dict[str, str] = {}
        for c in collaborators:
            cname = c.get("name_zh") or c.get("name_en") or c["author_id"]
            cid = g.add_node(
                "collaborator",
                c["author_id"],
                cname,
                value=int(c.get("co_papers") or 1),
                title=f"合作 {c.get('co_papers') or 0} 篇",
                query=cname,
            )
            collab_ids.add(c["author_id"])
            author_node_ids[c["author_id"]] = cid
            g.add_edge(hub_id, cid, value=int(c.get("co_papers") or 1), title="共著")

        paper_ids = {}
        for p in papers:
            doi = p.get("doi")
            if not doi:
                continue
            pid = g.add_node(
                "paper",
                doi,
                self._short(p.get("title") or doi, 18),
                value=5,
                title=f"{p.get('title') or doi}\n{p.get('year') or ''}",
                query=doi,
            )
            paper_ids[doi] = pid
            g.add_edge(hub_id, pid, value=2, title="著述")

        kw_ids = {}
        for k in keywords:
            kid = k.get("keyword_id") or k.get("label")
            if not kid:
                continue
            label = k.get("label") or str(kid)
            nid = g.add_node(
                "keyword",
                str(kid),
                label,
                value=int(k.get("paper_count") or 1),
                title=f"关键词 · {k.get('paper_count') or 0} 篇",
                query=label,
            )
            kw_ids[str(kid)] = nid
            g.add_edge(hub_id, nid, value=int(k.get("paper_count") or 1), title="研究主题")

        for peer in theme_peers:
            kid = peer.get("keyword_id")
            oid = peer.get("author_id")
            if not kid or not oid:
                continue
            kn = kw_ids.get(str(kid))
            if not kn:
                label = peer.get("label") or str(kid)
                kn = g.add_node(
                    "keyword",
                    str(kid),
                    label,
                    value=1,
                    title=f"关键词 · {label}",
                    query=label,
                )
                kw_ids[str(kid)] = kn
                g.add_edge(hub_id, kn, value=1, title="研究主题")
            if oid in author_node_ids:
                onid = author_node_ids[oid]
            else:
                oname = peer.get("name_zh") or peer.get("name_en") or oid
                onid = g.add_node(
                    "author",
                    oid,
                    oname,
                    value=int(peer.get("paper_count") or 1),
                    title=f"同主题 · {peer.get('paper_count') or 0} 篇",
                    query=oname,
                )
                author_node_ids[oid] = onid
            g.add_edge(
                kn,
                onid,
                value=int(peer.get("paper_count") or 1),
                title="同主题作者",
            )

        for inst in institutions:
            iid = inst.get("institution_id") or inst.get("name")
            if not iid:
                continue
            iname = inst.get("name") or str(iid)
            nid = g.add_node(
                "institution",
                str(iid),
                self._short(iname, 18),
                value=int(inst.get("link_count") or 1),
                title=iname,
                query=iname,
            )
            g.add_edge(hub_id, nid, value=int(inst.get("link_count") or 1), title="机构")

        for f in funds:
            fid = f.get("fund_id") or f.get("name")
            if not fid:
                continue
            nid = g.add_node(
                "fund",
                str(fid),
                self._short(f.get("name") or str(fid), 18),
                value=int(f.get("paper_count") or 1),
                title=f.get("name") or str(fid),
            )
            g.add_edge(hub_id, nid, value=1, title="基金")

        for pl in paper_links:
            pid = paper_ids.get(pl.get("doi"))
            if not pid:
                continue
            for kid in pl.get("keyword_ids") or []:
                if kid is None:
                    continue
                kn = kw_ids.get(str(kid))
                if kn:
                    g.add_edge(pid, kn, value=1, title="含关键词")
            for ca in pl.get("coauthor_ids") or []:
                onid = author_node_ids.get(ca)
                if onid:
                    g.add_edge(pid, onid, value=1, title="作者")

        return self._cache_set(
            cache_key,
            {
                "focus": "author",
                "author": author,
                "candidates": authors,
                "nodes": g.nodes,
                "edges": g.edges,
                "stats": {
                    "collaborators": len(collaborators),
                    "papers": len(papers),
                    "keywords": len(keywords),
                    "theme_peers": len(theme_peers),
                    "institutions": len(institutions),
                    "funds": len(funds),
                },
            },
        )

    def paper_network(self, doi: str) -> Dict[str, Any]:
        """Paper ego graph with 2-hop author→keyword bridges."""
        cache_key = f"paper:{(doi or '').strip()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        data = self.paper_neighborhood(doi)
        if not data:
            return {"nodes": [], "edges": [], "paper": None, "focus": "paper"}

        author_ids = [
            a.get("author_id")
            for a in (data.get("authors") or [])
            if a.get("author_id")
        ]
        paper_kws = set(k for k in (data.get("keywords") or []) if k)
        author_themes: List[Dict[str, Any]] = []
        if author_ids:
            themes_q = """
            MATCH (a:Author)<-[:AUTHORED_BY]-(p:Paper)-[:HAS_KEYWORD]->(k:Keyword)
            WHERE a.author_id IN $author_ids
            WITH a, k, count(DISTINCT p) AS paper_count
            ORDER BY paper_count DESC
            LIMIT $limit
            RETURN a.author_id AS author_id, k.keyword_id AS keyword_id,
                   k.label_zh AS label, paper_count
            """
            with self._driver.session() as session:
                author_themes = [
                    dict(r)
                    for r in session.run(
                        themes_q, author_ids=author_ids, limit=max(12, len(author_ids) * 3)
                    )
                ]

        g = self._graph_builder()
        hub_id = g.add_node(
            "paper",
            doi,
            self._short(data.get("title") or doi, 22),
            value=8,
            title=f"{data.get('title') or doi}\n{data.get('year') or ''}",
            query=doi,
        )
        author_nids: Dict[str, str] = {}
        for a in data.get("authors") or []:
            aid = a.get("author_id") or a.get("name")
            if not aid:
                continue
            aname = a.get("name") or str(aid)
            nid = g.add_node(
                "author",
                str(aid),
                aname,
                value=4,
                title=f"作者顺序 {a.get('order') or '-'}",
                query=aname,
            )
            author_nids[str(aid)] = nid
            g.add_edge(hub_id, nid, value=1, title="作者")
        kw_nids: Dict[str, str] = {}
        for kw in data.get("keywords") or []:
            nid = g.add_node("keyword", kw, kw, value=3, title="关键词", query=kw)
            kw_nids[kw] = nid
            g.add_edge(hub_id, nid, value=1, title="关键词")
        for inst in data.get("institutions") or []:
            nid = g.add_node(
                "institution",
                inst,
                self._short(inst, 18),
                value=3,
                title="机构",
                query=inst,
            )
            g.add_edge(hub_id, nid, value=1, title="机构")
        for fund in data.get("funds") or []:
            nid = g.add_node("fund", fund, self._short(fund, 18), value=2, title="基金")
            g.add_edge(hub_id, nid, value=1, title="基金")
        for code in data.get("clc") or []:
            nid = g.add_node("clc", code, code, value=2, title="中图分类")
            g.add_edge(hub_id, nid, value=1, title="分类")

        # Paper→Author→Keyword (author's themes beyond this paper's keywords)
        for row in author_themes:
            aid = str(row.get("author_id") or "")
            anid = author_nids.get(aid)
            label = row.get("label")
            if not anid or not label:
                continue
            if label in paper_kws and label in kw_nids:
                g.add_edge(anid, kw_nids[label], value=1, title="研究主题")
                continue
            kid = row.get("keyword_id") or label
            kn = kw_nids.get(label)
            if not kn:
                kn = g.add_node(
                    "keyword",
                    str(kid),
                    label,
                    value=int(row.get("paper_count") or 1),
                    title=f"作者主题 · {row.get('paper_count') or 0} 篇",
                    query=label,
                )
                kw_nids[label] = kn
            g.add_edge(
                anid,
                kn,
                value=int(row.get("paper_count") or 1),
                title="研究主题",
            )

        return self._cache_set(
            cache_key,
            {
                "focus": "paper",
                "paper": {
                    "doi": doi,
                    "title": data.get("title"),
                    "year": data.get("year"),
                    "url": data.get("url"),
                },
                "nodes": g.nodes,
                "edges": g.edges,
            },
        )

    def keyword_network(self, keyword: str, limit: int = 20) -> Dict[str, Any]:
        """Keyword ego graph with 2-hop author→keyword bridges."""
        cache_key = f"keyword:{(keyword or '').strip()}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        paper_limit = max(5, min(limit, 10))
        author_limit = max(5, min(limit, 12))
        related_kw_limit = 8
        bridge_limit = max(12, min(limit + 8, 28))
        inst_limit = 6

        find_q = """
        MATCH (k:Keyword)
        WHERE k.label_zh CONTAINS $keyword OR k.label_en CONTAINS $keyword
        OPTIONAL MATCH (k)<-[:HAS_KEYWORD]-(p:Paper)
        RETURN k.keyword_id AS keyword_id, k.label_zh AS label,
               count(DISTINCT p) AS paper_count
        ORDER BY paper_count DESC
        LIMIT 5
        """
        with self._driver.session() as session:
            found = [dict(r) for r in session.run(find_q, keyword=keyword)]
            if not found:
                return {"nodes": [], "edges": [], "keyword": None, "focus": "keyword"}
            hub = found[0]
            kid = hub["keyword_id"]

            papers_q = """
            MATCH (k:Keyword {keyword_id: $keyword_id})<-[:HAS_KEYWORD]-(p:Paper)
            RETURN p.doi AS doi, p.title_zh AS title, p.year AS year
            ORDER BY p.year DESC
            LIMIT $limit
            """
            authors_q = """
            MATCH (k:Keyword {keyword_id: $keyword_id})<-[:HAS_KEYWORD]-(p:Paper)-[:AUTHORED_BY]->(a:Author)
            RETURN a.author_id AS author_id, a.name_zh AS name_zh, a.name_en AS name_en,
                   count(DISTINCT p) AS paper_count
            ORDER BY paper_count DESC
            LIMIT $limit
            """
            related_q = """
            MATCH (k:Keyword {keyword_id: $keyword_id})<-[:HAS_KEYWORD]-(p:Paper)-[:HAS_KEYWORD]->(r:Keyword)
            WHERE r.keyword_id <> k.keyword_id
            RETURN r.keyword_id AS keyword_id, r.label_zh AS label,
                   count(DISTINCT p) AS paper_count
            ORDER BY paper_count DESC
            LIMIT $limit
            """
            # 2-hop: hub keyword → authors → their other keywords
            author_themes_q = """
            MATCH (k:Keyword {keyword_id: $keyword_id})<-[:HAS_KEYWORD]-(p:Paper)-[:AUTHORED_BY]->(a:Author)
            WITH a, count(DISTINCT p) AS hub_cnt
            ORDER BY hub_cnt DESC
            LIMIT $author_limit
            MATCH (a)<-[:AUTHORED_BY]-(p2:Paper)-[:HAS_KEYWORD]->(ok:Keyword)
            WHERE ok.keyword_id <> $keyword_id
            WITH a, ok, count(DISTINCT p2) AS shared
            ORDER BY shared DESC
            LIMIT $bridge_limit
            RETURN a.author_id AS author_id, a.name_zh AS name_zh, a.name_en AS name_en,
                   ok.keyword_id AS keyword_id, ok.label_zh AS label,
                   shared AS paper_count
            """
            inst_q = """
            MATCH (k:Keyword {keyword_id: $keyword_id})<-[:HAS_KEYWORD]-(p:Paper)-[:HAS_AFFILIATION]->(i:Institution)
            RETURN i.institution_id AS institution_id, i.name_norm AS name,
                   count(DISTINCT p) AS paper_count
            ORDER BY paper_count DESC
            LIMIT $limit
            """
            paper_author_q = """
            MATCH (k:Keyword {keyword_id: $keyword_id})<-[:HAS_KEYWORD]-(p:Paper)
            WITH p ORDER BY p.year DESC LIMIT $paper_limit
            MATCH (p)-[:AUTHORED_BY]->(a:Author)
            RETURN p.doi AS doi, collect(DISTINCT a.author_id)[0..6] AS author_ids
            """

        (
            papers,
            authors,
            related,
            author_themes,
            institutions,
            paper_authors,
        ) = self._run_parallel(
            [
                (papers_q, {"keyword_id": kid, "limit": paper_limit}),
                (authors_q, {"keyword_id": kid, "limit": author_limit}),
                (related_q, {"keyword_id": kid, "limit": related_kw_limit}),
                (
                    author_themes_q,
                    {
                        "keyword_id": kid,
                        "author_limit": author_limit,
                        "bridge_limit": bridge_limit,
                    },
                ),
                (inst_q, {"keyword_id": kid, "limit": inst_limit}),
                (paper_author_q, {"keyword_id": kid, "paper_limit": paper_limit}),
            ]
        )

        hub_label = hub.get("label") or keyword
        g = self._graph_builder()
        hub_id = g.add_node(
            "keyword",
            str(kid),
            hub_label,
            value=max(int(hub.get("paper_count") or 1), 1),
            title=f"关键词 · {hub.get('paper_count') or 0} 篇",
            query=hub_label,
        )
        author_nids: Dict[str, str] = {}
        for a in authors:
            aname = a.get("name_zh") or a.get("name_en") or a["author_id"]
            nid = g.add_node(
                "author",
                a["author_id"],
                aname,
                value=int(a.get("paper_count") or 1),
                title=f"{a.get('paper_count') or 0} 篇相关",
                query=aname,
            )
            author_nids[a["author_id"]] = nid
            g.add_edge(hub_id, nid, value=int(a.get("paper_count") or 1), title="作者")

        paper_ids = {}
        for p in papers:
            doi = p.get("doi")
            if not doi:
                continue
            pid = g.add_node(
                "paper",
                doi,
                self._short(p.get("title") or doi, 18),
                value=4,
                title=f"{p.get('title') or doi}\n{p.get('year') or ''}",
                query=doi,
            )
            paper_ids[doi] = pid
            g.add_edge(hub_id, pid, value=2, title="论文")

        kw_nids: Dict[str, str] = {str(kid): hub_id}
        for r in related:
            rid = r.get("keyword_id") or r.get("label")
            if not rid:
                continue
            label = r.get("label") or str(rid)
            nid = g.add_node(
                "keyword",
                str(rid),
                label,
                value=int(r.get("paper_count") or 1),
                title=f"共现 {r.get('paper_count') or 0} 篇",
                query=label,
            )
            kw_nids[str(rid)] = nid
            g.add_edge(hub_id, nid, value=int(r.get("paper_count") or 1), title="共现")

        for row in author_themes:
            aid = row.get("author_id")
            anid = author_nids.get(aid) if aid else None
            oid = row.get("keyword_id") or row.get("label")
            if not anid or not oid:
                continue
            label = row.get("label") or str(oid)
            kn = kw_nids.get(str(oid))
            if not kn:
                kn = g.add_node(
                    "keyword",
                    str(oid),
                    label,
                    value=int(row.get("paper_count") or 1),
                    title=f"作者主题 · {row.get('paper_count') or 0} 篇",
                    query=label,
                )
                kw_nids[str(oid)] = kn
            g.add_edge(
                anid,
                kn,
                value=int(row.get("paper_count") or 1),
                title="研究主题",
            )

        for inst in institutions:
            iid = inst.get("institution_id") or inst.get("name")
            if not iid:
                continue
            iname = inst.get("name") or str(iid)
            nid = g.add_node(
                "institution",
                str(iid),
                self._short(iname, 18),
                value=int(inst.get("paper_count") or 1),
                title=iname,
                query=iname,
            )
            g.add_edge(hub_id, nid, value=1, title="机构")

        for pa in paper_authors:
            pid = paper_ids.get(pa.get("doi"))
            if not pid:
                continue
            for aid in pa.get("author_ids") or []:
                anid = author_nids.get(aid)
                if anid:
                    g.add_edge(pid, anid, value=1, title="作者")

        return self._cache_set(
            cache_key,
            {
                "focus": "keyword",
                "keyword": hub,
                "candidates": found,
                "nodes": g.nodes,
                "edges": g.edges,
                "stats": {
                    "papers": len(papers),
                    "authors": len(authors),
                    "related_keywords": len(related),
                    "author_themes": len(author_themes),
                    "institutions": len(institutions),
                },
            },
        )

    def institution_network(self, name: str, limit: int = 20) -> Dict[str, Any]:
        """Institution ego graph with 2-hop author→keyword bridges."""
        cache_key = f"institution:{(name or '').strip()}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        author_limit = max(5, min(limit, 12))
        paper_limit = max(5, min(limit // 2 + 3, 8))
        kw_limit = 10
        bridge_limit = max(12, min(limit + 8, 28))

        find_q = """
        MATCH (i:Institution)
        WHERE i.name_norm CONTAINS $name
        OPTIONAL MATCH (p:Paper)-[:HAS_AFFILIATION]->(i)
        RETURN i.institution_id AS institution_id, i.name_norm AS name,
               count(DISTINCT p) AS paper_count
        ORDER BY paper_count DESC
        LIMIT 5
        """
        with self._driver.session() as session:
            found = [dict(r) for r in session.run(find_q, name=name)]
            if not found:
                return {
                    "nodes": [],
                    "edges": [],
                    "institution": None,
                    "focus": "institution",
                }
            hub = found[0]
            iid = hub["institution_id"]

            authors_q = """
            MATCH (p:Paper)-[:HAS_AFFILIATION]->(i:Institution {institution_id: $iid})
            MATCH (p)-[:AUTHORED_BY]->(a:Author)
            RETURN a.author_id AS author_id, a.name_zh AS name_zh, a.name_en AS name_en,
                   count(DISTINCT p) AS paper_count
            ORDER BY paper_count DESC
            LIMIT $limit
            """
            papers_q = """
            MATCH (p:Paper)-[:HAS_AFFILIATION]->(i:Institution {institution_id: $iid})
            RETURN p.doi AS doi, p.title_zh AS title, p.year AS year
            ORDER BY p.year DESC
            LIMIT $limit
            """
            kw_q = """
            MATCH (p:Paper)-[:HAS_AFFILIATION]->(i:Institution {institution_id: $iid})
            MATCH (p)-[:HAS_KEYWORD]->(k:Keyword)
            RETURN k.keyword_id AS keyword_id, k.label_zh AS label,
                   count(DISTINCT p) AS paper_count
            ORDER BY paper_count DESC
            LIMIT $limit
            """
            # 2-hop: institution authors → their keywords
            author_themes_q = """
            MATCH (p:Paper)-[:HAS_AFFILIATION]->(i:Institution {institution_id: $iid})
            MATCH (p)-[:AUTHORED_BY]->(a:Author)
            WITH a, count(DISTINCT p) AS hub_cnt
            ORDER BY hub_cnt DESC
            LIMIT $author_limit
            MATCH (a)<-[:AUTHORED_BY]-(p2:Paper)-[:HAS_KEYWORD]->(k:Keyword)
            WITH a, k, count(DISTINCT p2) AS shared
            ORDER BY shared DESC
            LIMIT $bridge_limit
            RETURN a.author_id AS author_id, a.name_zh AS name_zh, a.name_en AS name_en,
                   k.keyword_id AS keyword_id, k.label_zh AS label,
                   shared AS paper_count
            """

        authors, papers, keywords, author_themes = self._run_parallel(
            [
                (authors_q, {"iid": iid, "limit": author_limit}),
                (papers_q, {"iid": iid, "limit": paper_limit}),
                (kw_q, {"iid": iid, "limit": kw_limit}),
                (
                    author_themes_q,
                    {
                        "iid": iid,
                        "author_limit": author_limit,
                        "bridge_limit": bridge_limit,
                    },
                ),
            ]
        )

        hub_name = hub.get("name") or name
        g = self._graph_builder()
        hub_id = g.add_node(
            "institution",
            str(iid),
            self._short(hub_name, 20),
            value=max(int(hub.get("paper_count") or 1), 1),
            title=f"{hub_name} · {hub.get('paper_count') or 0} 篇",
            query=hub_name,
        )
        author_nids: Dict[str, str] = {}
        for a in authors:
            aname = a.get("name_zh") or a.get("name_en") or a["author_id"]
            nid = g.add_node(
                "author",
                a["author_id"],
                aname,
                value=int(a.get("paper_count") or 1),
                title=f"{a.get('paper_count') or 0} 篇",
                query=aname,
            )
            author_nids[a["author_id"]] = nid
            g.add_edge(hub_id, nid, value=int(a.get("paper_count") or 1), title="作者")
        for p in papers:
            doi = p.get("doi")
            if not doi:
                continue
            nid = g.add_node(
                "paper",
                doi,
                self._short(p.get("title") or doi, 18),
                value=4,
                title=f"{p.get('title') or doi}\n{p.get('year') or ''}",
                query=doi,
            )
            g.add_edge(hub_id, nid, value=2, title="论文")
        kw_nids: Dict[str, str] = {}
        for k in keywords:
            kid = k.get("keyword_id") or k.get("label")
            if not kid:
                continue
            label = k.get("label") or str(kid)
            nid = g.add_node(
                "keyword",
                str(kid),
                label,
                value=int(k.get("paper_count") or 1),
                title=f"{k.get('paper_count') or 0} 篇",
                query=label,
            )
            kw_nids[str(kid)] = nid
            g.add_edge(hub_id, nid, value=int(k.get("paper_count") or 1), title="关键词")

        for row in author_themes:
            aid = row.get("author_id")
            anid = author_nids.get(aid) if aid else None
            oid = row.get("keyword_id") or row.get("label")
            if not anid or not oid:
                continue
            label = row.get("label") or str(oid)
            kn = kw_nids.get(str(oid))
            if not kn:
                kn = g.add_node(
                    "keyword",
                    str(oid),
                    label,
                    value=int(row.get("paper_count") or 1),
                    title=f"作者主题 · {row.get('paper_count') or 0} 篇",
                    query=label,
                )
                kw_nids[str(oid)] = kn
            g.add_edge(
                anid,
                kn,
                value=int(row.get("paper_count") or 1),
                title="研究主题",
            )

        return self._cache_set(
            cache_key,
            {
                "focus": "institution",
                "institution": hub,
                "candidates": found,
                "nodes": g.nodes,
                "edges": g.edges,
                "stats": {
                    "authors": len(authors),
                    "papers": len(papers),
                    "keywords": len(keywords),
                    "author_themes": len(author_themes),
                },
            },
        )


class _GraphBuilder:
    """Collect unique nodes/edges for visualization payloads."""

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self._node_ids: set[str] = set()
        self._edge_ids: set[str] = set()

    def add_node(
        self,
        group: str,
        item_id: str,
        label: str,
        *,
        value: int = 1,
        title: str = "",
        query: str = "",
    ) -> str:
        nid = f"{group}:{item_id}"
        if nid not in self._node_ids:
            self._node_ids.add(nid)
            text = (label or str(item_id)).strip()
            q = (query or text).strip()
            self.nodes.append(
                {
                    "id": nid,
                    "label": text if len(text) <= 18 else text[:17] + "…",
                    "group": group,
                    "value": max(int(value), 1),
                    "title": title or text,
                    "query": q,
                }
            )
        return nid

    def add_edge(
        self,
        source: str,
        target: str,
        *,
        value: int = 1,
        title: str = "",
        label: str = "",
    ) -> None:
        if source not in self._node_ids or target not in self._node_ids:
            return
        eid = f"{source}->{target}"
        rid = f"{target}->{source}"
        if eid in self._edge_ids or rid in self._edge_ids:
            return
        self._edge_ids.add(eid)
        self.edges.append(
            {
                "id": eid,
                "from": source,
                "to": target,
                "value": max(int(value), 1),
                "label": label,
                "title": title or label,
            }
        )
