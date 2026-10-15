"""Eval results in SQLite (`eval.db` at the repo root, gitignored), so runs
can be compared over time — e.g. before vs. after a prompt change."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

# Repo root (app/exercises/eval/store.py → parents[3]).
DB_PATH = Path(__file__).parents[3] / "eval.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS eval_runs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               created_at REAL NOT NULL,
               dataset TEXT NOT NULL,
               target TEXT NOT NULL,
               target_label TEXT NOT NULL,
               grader TEXT NOT NULL,
               summary_json TEXT NOT NULL,
               results_json TEXT NOT NULL
           )"""
    )
    return conn


def save_run(
    dataset: str, target: str, label: str, grader: str,
    summary: dict[str, Any], rows: list[dict[str, Any]],
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO eval_runs (created_at, dataset, target, target_label, grader, "
            "summary_json, results_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), dataset, target, label, grader, json.dumps(summary), json.dumps(rows)),
        )
        return int(cur.lastrowid)


def recent_runs(limit: int = 30) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, dataset, target, target_label, grader, summary_json "
            "FROM eval_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"id": r[0], "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[1])),
         "dataset": r[2], "target": r[3], "label": r[4], "grader": r[5], **json.loads(r[6])}
        for r in rows
    ]
