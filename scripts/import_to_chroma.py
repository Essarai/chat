#!/usr/bin/env python3
"""
Embed rag_cleaned/documents.jsonl via MiniMax and upsert into Chroma.

Targets:
  --target http   → chromadb.HttpClient (self-hosted)
  --target cloud  → chromadb.CloudClient (Chroma Cloud)

Does NOT touch Neo4j or MySQL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import chromadb

ROOT = Path(__file__).resolve().parents[1]
DOCS_JSONL = ROOT / "rag_cleaned" / "documents.jsonl"

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

DEFAULT_CHROMA_HOST = os.getenv("CHROMA_HOST", "182.92.0.163")
DEFAULT_CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
DEFAULT_CHROMA_TOKEN = os.getenv("CHROMA_TOKEN", "YourStrongSecretKey123!")
DEFAULT_COLLECTION = os.getenv("CHROMA_COLLECTION", "journal_papers")
DEFAULT_CLOUD_API_KEY = os.getenv("CHROMA_CLOUD_API_KEY", "")
DEFAULT_CLOUD_TENANT = os.getenv("CHROMA_CLOUD_TENANT", "")
DEFAULT_CLOUD_DATABASE = os.getenv("CHROMA_CLOUD_DATABASE", "journals")

DEFAULT_MINIMAX_KEY = os.getenv(
    "MINIMAX_API_KEY",
    "sk-cp-cLMZQs9GoZlEMSB13KHy1AgVQaWFxv3y_NrjGcAuofOccNRM7Pec1o-tWLAgE6A0ZpMNsuC1_v-jHsTDo9CF2XeOV1_Z7CEP8q-q_LfAafLOLZxpwM4jxTs",
)
DEFAULT_MINIMAX_URL = os.getenv(
    "MINIMAX_EMBED_URL", "https://api.minimaxi.com/v1/embeddings"
)
DEFAULT_MODEL = os.getenv("MINIMAX_EMBED_MODEL", "embo-01")

# embo-01 ~4096 tokens; truncate conservatively for long abstracts
MAX_CHARS = int(os.getenv("EMBED_MAX_CHARS", "6000"))
BATCH = int(os.getenv("EMBED_BATCH", "16"))


META_KEYS = [
    "doi",
    "title",
    "title_zh",
    "year",
    "volume",
    "issue",
    "pages",
    "journal",
    "journal_id",
    "issn",
    "keywords_zh",
    "authors_zh",
    "clc",
    "funding",
    "has_abstract",
    "text_length",
    "pub_date",
]


def load_docs(path: Path) -> list[dict]:
    docs = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            docs.append(json.loads(line))
    return docs


def sanitize_meta(doc: dict) -> dict:
    meta = {}
    for k in META_KEYS:
        v = doc.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            meta[k] = v
        elif isinstance(v, (int, float)):
            meta[k] = v
        else:
            s = str(v)
            # Chroma metadata values should stay reasonably small
            meta[k] = s[:1000]
    # ensure year/text_length are ints when possible
    if "year" in meta:
        try:
            meta["year"] = int(meta["year"])
        except (TypeError, ValueError):
            pass
    if "text_length" in meta:
        try:
            meta["text_length"] = int(meta["text_length"])
        except (TypeError, ValueError):
            pass
    if "has_abstract" in meta and not isinstance(meta["has_abstract"], bool):
        meta["has_abstract"] = str(meta["has_abstract"]).lower() in {
            "1",
            "true",
            "y",
            "yes",
        }
    return meta


def embed_batch(
    texts: list[str],
    api_key: str,
    url: str,
    model: str,
    embed_type: str = "db",
    retries: int = 5,
) -> list[list[float]]:
    payload = {"model": model, "type": embed_type, "texts": texts}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            status = (data.get("base_resp") or {}).get("status_code", 0)
            if status != 0:
                raise RuntimeError(f"MiniMax error: {data.get('base_resp')}")
            vectors = data.get("vectors")
            if not vectors or len(vectors) != len(texts):
                raise RuntimeError(
                    f"Unexpected vectors size: got {0 if not vectors else len(vectors)}, want {len(texts)}"
                )
            return vectors
        except Exception as e:
            last_err = e
            sleep_s = min(2**attempt, 30)
            print(f"  embed retry {attempt + 1}/{retries}: {e}; sleep {sleep_s}s")
            time.sleep(sleep_s)
    raise RuntimeError(f"embed_batch failed after retries: {last_err}")


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def get_client(args):
    if getattr(args, "target", "http") == "cloud":
        if not args.cloud_api_key or not args.cloud_tenant:
            raise SystemExit(
                "Cloud target requires --cloud-api-key / --cloud-tenant "
                "(or CHROMA_CLOUD_API_KEY / CHROMA_CLOUD_TENANT in .env)"
            )
        print(
            f"Chroma Cloud: database={args.cloud_database} "
            f"tenant={args.cloud_tenant[:8]}…"
        )
        return chromadb.CloudClient(
            api_key=args.cloud_api_key,
            tenant=args.cloud_tenant,
            database=args.cloud_database,
        )
    print(f"Chroma Http: {args.host}:{args.port}")
    return chromadb.HttpClient(
        host=args.host,
        port=args.port,
        headers={"Authorization": f"Bearer {args.token}"},
    )


def import_docs(args):
    docs_path = Path(args.jsonl) if getattr(args, "jsonl", None) else DOCS_JSONL
    if not docs_path.exists():
        print(f"Missing {docs_path}", file=sys.stderr)
        sys.exit(1)

    docs = load_docs(docs_path)
    print(f"Loaded {len(docs)} docs from {docs_path}")

    client = get_client(args)
    try:
        print("Chroma heartbeat:", client.heartbeat())
    except Exception as e:
        print(f"Chroma heartbeat skipped: {e}")

    if args.recreate:
        try:
            client.delete_collection(args.collection)
            print(f"Deleted collection `{args.collection}`")
        except Exception:
            pass

    # Cloud may reject custom HNSW metadata keys; keep http-only extras soft.
    meta = {
        "source": "rag_cleaned/documents.jsonl",
        "embedding_model": args.model,
        "embedding_provider": "minimax",
    }
    if args.target == "http":
        meta["hnsw:space"] = "cosine"
    try:
        collection = client.get_or_create_collection(
            name=args.collection,
            metadata=meta,
        )
    except Exception:
        collection = client.get_or_create_collection(name=args.collection)

    # Resume support: skip IDs already present when not recreating
    existing = set()
    if not args.recreate and collection.count() > 0:
        print(f"Existing count={collection.count()}, scanning IDs for resume ...")
        offset = 0
        # Chroma Cloud free tier often caps Get limit around 300
        page = min(int(getattr(args, "get_page", 100) or 100), 300)
        while True:
            got = collection.get(include=[], limit=page, offset=offset)
            ids = got.get("ids") or []
            if not ids:
                break
            existing.update(ids)
            offset += len(ids)
            if len(ids) < page:
                break
        print(f"Already indexed: {len(existing)}")

    pending = []
    for d in docs:
        doc_id = d.get("chunk_id") or d.get("doc_id") or d.get("doi")
        if not doc_id:
            continue
        if doc_id in existing:
            continue
        text = (d.get("text") or "").strip()
        if not text:
            continue
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
        pending.append(
            {
                "id": doc_id,
                "text": text,
                "meta": sanitize_meta(d),
            }
        )

    print(f"To upsert: {len(pending)} (skip {len(docs) - len(pending)})")
    if args.limit:
        pending = pending[: args.limit]
        print(f"Limited to {len(pending)}")

    done = 0
    t0 = time.time()
    for batch in chunks(pending, args.batch):
        texts = [x["text"] for x in batch]
        ids = [x["id"] for x in batch]
        metas = [x["meta"] for x in batch]
        vectors = embed_batch(
            texts,
            api_key=args.api_key,
            url=args.embed_url,
            model=args.model,
            embed_type="db",
        )
        # Chroma remote occasionally returns 502; retry upsert without re-embedding
        last_upsert_err = None
        for attempt in range(6):
            try:
                collection.upsert(
                    ids=ids,
                    embeddings=vectors,
                    documents=texts,
                    metadatas=metas,
                )
                last_upsert_err = None
                break
            except Exception as e:
                last_upsert_err = e
                sleep_s = min(2**attempt, 30)
                print(f"  chroma upsert retry {attempt + 1}/6: {e}; sleep {sleep_s}s")
                time.sleep(sleep_s)
                # refresh collection handle after gateway blips
                collection = client.get_or_create_collection(name=args.collection)
        if last_upsert_err is not None:
            raise RuntimeError(f"chroma upsert failed: {last_upsert_err}")
        done += len(batch)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed else 0
        print(
            f"  upserted {done}/{len(pending)}  "
            f"collection={collection.count()}  "
            f"{rate:.1f} docs/s",
            flush=True,
        )

    print(
        f"\nDone. collection=`{args.collection}` count={collection.count()} "
        f"in {time.time() - t0:.1f}s"
    )


def main():
    p = argparse.ArgumentParser(description="MiniMax embed -> Chroma import")
    p.add_argument(
        "--target",
        choices=("http", "cloud"),
        default=os.getenv("CHROMA_TARGET", "cloud"),
        help="cloud=Chroma CloudClient (default); http=self-hosted HttpClient",
    )
    p.add_argument("--host", default=DEFAULT_CHROMA_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_CHROMA_PORT)
    p.add_argument("--token", default=DEFAULT_CHROMA_TOKEN)
    p.add_argument("--collection", default=DEFAULT_COLLECTION)
    p.add_argument(
        "--jsonl",
        type=Path,
        default=DOCS_JSONL,
        help="path to documents.jsonl",
    )
    p.add_argument("--cloud-api-key", default=DEFAULT_CLOUD_API_KEY)
    p.add_argument("--cloud-tenant", default=DEFAULT_CLOUD_TENANT)
    p.add_argument("--cloud-database", default=DEFAULT_CLOUD_DATABASE)
    p.add_argument("--api-key", default=DEFAULT_MINIMAX_KEY)
    p.add_argument("--embed-url", default=DEFAULT_MINIMAX_URL)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--batch", type=int, default=BATCH)
    p.add_argument(
        "--get-page",
        type=int,
        default=100,
        help="page size when scanning existing IDs (Cloud Get limit often ≤300)",
    )
    p.add_argument("--limit", type=int, default=0, help="debug: only first N docs")
    p.add_argument(
        "--recreate",
        action="store_true",
        help="delete collection before import",
    )
    args = p.parse_args()
    import_docs(args)


if __name__ == "__main__":
    main()
