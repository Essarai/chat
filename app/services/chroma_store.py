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
        self._client = chromadb.HttpClient(
            host=self.settings.chroma_host,
            port=self.settings.chroma_port,
            headers={"Authorization": f"Bearer {self.settings.chroma_token}"},
        )
        self._collection = self._client.get_collection(self.settings.chroma_collection)

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
