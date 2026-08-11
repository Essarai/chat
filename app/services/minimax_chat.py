from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Dict, Iterator, List, Optional

from app.config import Settings, get_settings


def _extract_message_content(content) -> str:
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("text"):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content or "")


class MiniMaxChat:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.minimax_api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 8192,
        retries: int = 2,
    ) -> str:
        if not self.settings.minimax_api_key:
            raise RuntimeError("MINIMAX_API_KEY is not set")

        payload = {
            "model": self.settings.minimax_chat_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.settings.minimax_api_key}",
            "Content-Type": "application/json",
        }
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    self.settings.minimax_chat_url,
                    data=body,
                    headers=headers,
                    method="POST",
                )
                # Keep both attempts inside the stage budget: understanding
                # stays near 30s and generation near 90s including backoff.
                timeout = 14 if max_tokens <= 1200 else 44
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                # OpenAI-compatible shape
                choices = data.get("choices")
                if choices:
                    msg = choices[0].get("message") or {}
                    content = _extract_message_content(msg.get("content"))
                    if content:
                        return content.strip()

                # Native fallback
                base = data.get("base_resp") or {}
                if base.get("status_code") not in (None, 0):
                    raise RuntimeError(f"MiniMax chat error: {base}")
                reply = data.get("reply") or data.get("output_text")
                if reply:
                    return str(reply).strip()
                raise RuntimeError(f"Unexpected chat response: {list(data.keys())}")
            except Exception as e:
                last_err = e
                retryable = isinstance(e, (TimeoutError, urllib.error.URLError))
                if isinstance(e, urllib.error.HTTPError):
                    retryable = e.code == 429 or e.code >= 500
                if not retryable or attempt + 1 >= retries:
                    break
                time.sleep(1)
        raise RuntimeError(f"chat failed: {last_err}")

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> Iterator[str]:
        """Yield text deltas from OpenAI-compatible SSE stream."""
        if not self.settings.minimax_api_key:
            raise RuntimeError("MINIMAX_API_KEY is not set")

        payload = {
            "model": self.settings.minimax_chat_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.settings.minimax_chat_url,
            data=body,
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            while True:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content is None:
                    continue
                text = _extract_message_content(content)
                if text:
                    yield text
