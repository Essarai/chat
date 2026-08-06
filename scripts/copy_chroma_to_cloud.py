#!/usr/bin/env python3
"""
Copy an existing Chroma collection (HttpClient) into Chroma Cloud.

Reuses stored embeddings — does NOT re-call MiniMax.
Credentials via env / .env (never hardcode secrets in git).

  CHROMA_HOST / CHROMA_PORT / CHROMA_TOKEN / CHROMA_COLLECTION  → source
  CHROMA_CLOUD_API_KEY / CHROMA_CLOUD_TENANT / CHROMA_CLOUD_DATABASE → dest
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import chromadb
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def source_client(host: str, port: int, token: str):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return chromadb.HttpClient(host=host, port=port, headers=headers or None)


def cloud_client(api_key: str, tenant: str, database: str):
    return chromadb.CloudClient(api_key=api_key, tenant=tenant, database=database)


def iter_collection(col, page: int = 200):
    """Yield (ids, embeddings, documents, metadatas) pages."""
    offset = 0
    total = col.count()
    while offset < total:
        batch = col.get(
            include=["embeddings", "documents", "metadatas"],
            limit=page,
            offset=offset,
        )
        ids = batch.get("ids") or []
        if not ids:
            break
        yield (
            ids,
            batch.get("embeddings"),
            batch.get("documents"),
            batch.get("metadatas"),
        )
        offset += len(ids)
        print(f"  fetched {min(offset, total)}/{total}", flush=True)


def copy_collection(
    *,
    src_host: str,
    src_port: int,
    src_token: str,
    collection: str,
    cloud_api_key: str,
    cloud_tenant: str,
    cloud_database: str,
    page: int,
    upsert_batch: int,
    recreate: bool,
) -> None:
    print(f"Source: {src_host}:{src_port} / {collection}")
    src = source_client(src_host, src_port, src_token)
    src_col = src.get_collection(collection)
    src_n = src_col.count()
    print(f"Source count: {src_n}")

    print(f"Dest: Cloud database={cloud_database} tenant={cloud_tenant[:8]}…")
    dst = cloud_client(cloud_api_key, cloud_tenant, cloud_database)
    if recreate:
        try:
            dst.delete_collection(collection)
            print(f"Deleted cloud collection `{collection}`")
        except Exception as e:
            print(f"delete_collection skipped: {e}")
    dst_col = dst.get_or_create_collection(name=collection)
    print(f"Cloud collection ready; current count={dst_col.count()}")

    done = 0
    t0 = time.time()
    buf_ids: list = []
    buf_emb: list = []
    buf_docs: list = []
    buf_meta: list = []

    def flush() -> None:
        nonlocal done, buf_ids, buf_emb, buf_docs, buf_meta
        if not buf_ids:
            return
        last_err = None
        for attempt in range(6):
            try:
                dst_col.upsert(
                    ids=buf_ids,
                    embeddings=buf_emb,
                    documents=buf_docs,
                    metadatas=buf_meta,
                )
                last_err = None
                break
            except Exception as e:
                last_err = e
                sleep_s = min(2**attempt, 30)
                print(f"  upsert retry {attempt + 1}/6: {e}; sleep {sleep_s}s", flush=True)
                time.sleep(sleep_s)
        if last_err is not None:
            raise RuntimeError(f"cloud upsert failed: {last_err}")
        done += len(buf_ids)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed else 0
        print(
            f"  upserted {done}/{src_n}  cloud={dst_col.count()}  {rate:.1f} docs/s",
            flush=True,
        )
        buf_ids, buf_emb, buf_docs, buf_meta = [], [], [], []

    for ids, embeddings, documents, metadatas in iter_collection(src_col, page=page):
        if embeddings is None:
            raise RuntimeError("source get() returned no embeddings; cannot copy vectors")
        for i, doc_id in enumerate(ids):
            emb = embeddings[i]
            if emb is None:
                print(f"  skip {doc_id}: missing embedding")
                continue
            buf_ids.append(doc_id)
            buf_emb.append(emb)
            buf_docs.append((documents or [None] * len(ids))[i])
            meta = (metadatas or [{}] * len(ids))[i] or {}
            buf_meta.append(meta)
            if len(buf_ids) >= upsert_batch:
                flush()
    flush()

    final = dst_col.count()
    print(
        f"\nDone. source={src_n} cloud=`{collection}` count={final} "
        f"in {time.time() - t0:.1f}s"
    )
    if final < src_n:
        print(
            f"WARNING: cloud count ({final}) < source ({src_n}). "
            "Re-run or check skipped ids.",
            file=sys.stderr,
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Copy Chroma Http → Chroma Cloud")
    p.add_argument("--host", default=_env("CHROMA_HOST", "182.92.0.163"))
    p.add_argument("--port", type=int, default=int(_env("CHROMA_PORT", "8000") or "8000"))
    p.add_argument("--token", default=_env("CHROMA_TOKEN"))
    p.add_argument("--collection", default=_env("CHROMA_COLLECTION", "journal_papers"))
    p.add_argument("--cloud-api-key", default=_env("CHROMA_CLOUD_API_KEY"))
    p.add_argument("--cloud-tenant", default=_env("CHROMA_CLOUD_TENANT"))
    p.add_argument("--cloud-database", default=_env("CHROMA_CLOUD_DATABASE", "journals"))
    p.add_argument("--page", type=int, default=200, help="source get page size")
    p.add_argument("--batch", type=int, default=100, help="cloud upsert batch size")
    p.add_argument(
        "--recreate",
        action="store_true",
        help="delete cloud collection before copy",
    )
    args = p.parse_args()

    if not args.cloud_api_key or not args.cloud_tenant:
        print(
            "Missing CHROMA_CLOUD_API_KEY / CHROMA_CLOUD_TENANT "
            "(set in .env or pass flags).",
            file=sys.stderr,
        )
        sys.exit(1)

    copy_collection(
        src_host=args.host,
        src_port=args.port,
        src_token=args.token,
        collection=args.collection,
        cloud_api_key=args.cloud_api_key,
        cloud_tenant=args.cloud_tenant,
        cloud_database=args.cloud_database,
        page=args.page,
        upsert_batch=args.batch,
        recreate=args.recreate,
    )


if __name__ == "__main__":
    main()
