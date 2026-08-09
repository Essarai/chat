#!/usr/bin/env python3
"""Probe whether /ask and /ask/stream bind the RWB corpus."""

from __future__ import annotations

import json
import sqlite3
import urllib.request

BASE = "http://127.0.0.1:8080"


def db_yearly(path: str):
    c = sqlite3.connect(path)
    return c.execute(
        "select year, count(*) from papers where year>=2022 group by year order by year"
    ).fetchall()


def ask(journal_id: str, question: str) -> dict:
    body = json.dumps(
        {"question": question, "reset": True, "journal_id": journal_id},
        ensure_ascii=False,
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/ask",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode())


def ask_stream(journal_id: str, question: str) -> dict:
    body = json.dumps(
        {"question": question, "reset": True, "journal_id": journal_id},
        ensure_ascii=False,
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/ask/stream",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    answer = ""
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    for block in raw.split("\n\n"):
        if not block.startswith("data: "):
            continue
        try:
            ev = json.loads(block[6:])
        except Exception:
            continue
        if ev.get("type") == "done":
            return ev
        if ev.get("type") == "error":
            return ev
    return {"answer": answer, "raw_tail": raw[-500:]}


def main():
    print("NXB", db_yearly("data/journal.db"))
    print("RWB", db_yearly("data/journal_rwb.db"))
    q = "统计近5年每年发文数量"
    for jid in ("ZDXBNXB", "ZDXBRWB"):
        print("\n=== /ask", jid, "===")
        d = ask(jid, q)
        yearly = ((d.get("evidence") or {}).get("sql") or {}).get("yearly") or []
        print("yearly", yearly[:5])
        print("ans", (d.get("answer") or "")[:220].replace("\n", " "))
    print("\n=== /ask/stream ZDXBRWB ===")
    ev = ask_stream("ZDXBRWB", q)
    print("type", ev.get("type"))
    print("ans", (ev.get("answer") or ev.get("message") or "")[:300].replace("\n", " "))


if __name__ == "__main__":
    main()
