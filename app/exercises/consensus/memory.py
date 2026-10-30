"""Long-term memory (PHASE 3 — MEMORY ACROSS SESSIONS).

Three kinds of memory exist in this app; only the third lives in this file:

  1. Working memory  — an agent's own tool-call transcript inside one loop
                       (the `messages` list in core/agent_loop.py).
  2. Session memory  — prior question/answer turns of the current research
                       session (kept in RAM in routes.py, injected into the
                       shared context each follow-up).
  3. Long-term memory — THIS: past sessions persisted to SQLite, recalled by
                       keyword relevance when a new query arrives.

Recall is deliberately simple keyword overlap — the learning point is the
*shape* (store → recall → inject into context), not retrieval sophistication.
Swap `recall()` for an embeddings search later without touching callers.
"""

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

# Repo root (app/exercises/consensus/memory.py → parents[3]).
DB_PATH = Path(__file__).parents[3] / "memory.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS research_memory (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               created_at REAL NOT NULL,
               query TEXT NOT NULL,
               merged_answer TEXT NOT NULL,
               agreement_summary TEXT,
               disagreements_json TEXT
           )"""
    )
    return conn


def remember(query: str, verdict: dict[str, Any]) -> None:
    """Persist a finished run's essentials. Called after every successful
    verdict — memory accumulates whether or not recall is enabled, so there
    is something to recall once you switch the feature on."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO research_memory "
            "(created_at, query, merged_answer, agreement_summary, disagreements_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                time.time(),
                query,
                verdict.get("merged_answer", ""),
                verdict.get("agreement_summary", ""),
                json.dumps(verdict.get("disagreements", [])),
            ),
        )


def recall(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Find past research relevant to a new query by distinct-term overlap
    against stored queries and answers. Returns compact summaries ready to
    inject into the shared context."""
    terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 3}
    if not terms:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT created_at, query, merged_answer, agreement_summary, disagreements_json "
            "FROM research_memory ORDER BY created_at DESC LIMIT 200"
        ).fetchall()

    scored = []
    for created_at, q, merged, summary, dis_json in rows:
        haystack = f"{q} {merged}".lower()
        score = sum(1 for t in terms if t in haystack)
        if score >= 2:  # require real overlap, not a single stray word
            scored.append((score, created_at, q, merged, summary, dis_json))
    scored.sort(key=lambda s: -s[0])

    out = []
    for _, created_at, q, merged, summary, dis_json in scored[:limit]:
        disagreements = json.loads(dis_json or "[]")
        out.append(
            {
                "date": time.strftime("%Y-%m-%d", time.localtime(created_at)),
                "query": q,
                "summary": summary or merged[:300],
                "disagreement_topics": [d.get("topic", "") for d in disagreements],
            }
        )
    return out


def format_recalled(items: list[dict[str, Any]]) -> str:
    """Render recalled memories as a context block. Framed as *prior* findings
    so agents treat them as leads to verify, not ground truth."""
    lines = [
        "Findings from this user's PAST research sessions (verify before relying on them):"
    ]
    for m in items:
        line = f"- [{m['date']}] Q: {m['query']} — {m['summary']}"
        if m["disagreement_topics"]:
            line += f" (models disagreed on: {', '.join(m['disagreement_topics'])})"
        lines.append(line)
    return "\n".join(lines)
