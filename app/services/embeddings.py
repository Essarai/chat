from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import List, Literal

from app.config import Settings, get_settings


class MiniMaxEmbeddings:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def embed(
        self,
        texts: List[str],
        embed_type: Literal["db", "query"] = "query",
        retries: int = 2,
    ) -> List[List[float]]:
        if not texts:
            return []
        if not self.settings.minimax_api_key:
            raise RuntimeError("MINIMAX_API_KEY is not set")

        payload = {
            "model": self.settings.minimax_embed_model,
            "type": embed_type,
            "texts": texts,
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.settings.minimax_api_key}",
            "Content-Type": "application/json",
        }
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    self.settings.minimax_embed_url,
                    data=body,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                status = (data.get("base_resp") or {}).get("status_code", 0)
                if status != 0:
                    raise RuntimeError(f"MiniMax embedding error: {data.get('base_resp')}")
                vectors = data.get("vectors")
                if not vectors or len(vectors) != len(texts):
                    raise RuntimeError("Unexpected embedding response size")
                return vectors
            except Exception as e:
                last_err = e
                retryable = isinstance(e, (TimeoutError, urllib.error.URLError))
                if isinstance(e, urllib.error.HTTPError):
                    retryable = e.code == 429 or e.code >= 500
                if not retryable or attempt + 1 >= retries:
                    break
                time.sleep(1)
        raise RuntimeError(f"embedding failed: {last_err}")

    def embed_query(self, text: str) -> List[float]:
        return self.embed([text], embed_type="query")[0]
