from __future__ import annotations

from typing import Any, Dict, List, Optional

import chromadb

from app.config import Settings, get_settings
from app.services.embeddings import MiniMaxEmbeddings
from app.utils import doi_url


class ChromaStore:
    def __init__(
        self,
        settings: Settings | None = None,
        embeddings: MiniMaxEmbeddings | None = None,
    ):
        self.settings = settings or get_settings()
        self.embeddings = embeddings or MiniMaxEmbeddings(self.settings)
        try:
            self._client = self._build_client()
            self._collection = self._client.get_collection(self.settings.chroma_collection)
        except Exception as e:
            raise RuntimeError(self._connect_error(e)) from e

    def _build_client(self):
        s = self.settings
        if s.chroma_uses_cloud:
            if not s.chroma_cloud_api_key or not s.chroma_cloud_tenant:
                raise RuntimeError(
                    "CHROMA_TARGET=cloud 需要设置 CHROMA_CLOUD_API_KEY 与 CHROMA_CLOUD_TENANT"
                )
            return chromadb.CloudClient(
                api_key=s.chroma_cloud_api_key,
                tenant=s.chroma_cloud_tenant,
                database=s.chroma_cloud_database,
            )
        return chromadb.HttpClient(
            host=s.chroma_host,
            port=s.chroma_port,
            headers={"Authorization": f"Bearer {s.chroma_token}"},
        )

    def _connect_error(self, e: Exception) -> str:
        s = self.settings
        if s.chroma_uses_cloud:
            return (
                f"无法连接 Chroma Cloud "
                f"(database={s.chroma_cloud_database}, collection={s.chroma_collection}): {e}. "
                "请确认 CHROMA_CLOUD_API_KEY / TENANT / DATABASE 与 collection 名称。"
            )
        return (
            f"无法连接 Chroma ({s.chroma_host}:{s.chroma_port}): {e}. "
            "若服务部署在 Railway，请确认云主机安全组对公网开放 Chroma 端口，"
            "或改用 CHROMA_TARGET=cloud。"
        )

    def count(self) -> int:
        return self._collection.count()

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        k = top_k or self.settings.rag_top_k
        vector = self.embeddings.embed_query(query)
        kwargs: Dict[str, Any] = {
            "query_embeddings": [vector],
            "n_results": k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        res = self._collection.query(**kwargs)

        hits: List[Dict[str, Any]] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc_id in enumerate(ids):
            meta = metas[i] or {}
            doi = meta.get("doi")
            hits.append(
                {
                    "id": doc_id,
                    "distance": dists[i] if i < len(dists) else None,
                    "text": docs[i] if i < len(docs) else "",
                    "doi": doi,
                    "url": doi_url(doi),
                    "title": meta.get("title_zh") or meta.get("title") or "",
                    "authors": meta.get("authors_zh") or "",
                    "keywords": meta.get("keywords_zh") or "",
                    "year": meta.get("year"),
                    "journal": meta.get("journal") or "",
                    "metadata": meta,
                }
            )
        return hits
