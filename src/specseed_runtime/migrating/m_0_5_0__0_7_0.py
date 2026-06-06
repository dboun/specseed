"""
m_0_5_0__0_7_0.py - scheduled retries (tasks.not_before) + platform_error label.

0.7.0 adds failure recovery: failed tasks requeue with a backoff timestamp the
claim honors, and hard failures surface as ``platform_error`` posts. Two storage
touches:

1. ``specseed.db``: ``ALTER TABLE tasks ADD COLUMN not_before TEXT`` - fresh dbs
   get it from the schema; existing dbs gain it here.
2. delete ``seed_state.json`` so the next startup re-seeds labels and the new
   ``platform_error`` label lands on already-seeded targets (prune=False; user
   labels untouched).

Idempotent: the column is only added when missing; a missing marker/db is a no-op.
Only Python stdlib is used.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

FROM = "0.5.0"
TO = "0.7.0"

_DB = "specseed.db"
_SEED_MARKER = "seed_state.json"


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def run(storage: str | Path, specseed_dir: str | Path) -> list[Path]:
    """Apply the hop. Returns the paths it changed."""
    storage = Path(storage)
    changed: list[Path] = []

    db_path = storage / _DB
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "tasks" in tables and not _has_column(conn, "tasks", "not_before"):
                    conn.execute("ALTER TABLE tasks ADD COLUMN not_before TEXT")
                    conn.commit()
                    changed.append(db_path)
        except sqlite3.DatabaseError:
            pass  # not a sqlite file (corrupt/placeholder); leave it for the runtime

    marker = storage / _SEED_MARKER
    if marker.exists():
        marker.unlink()
        changed.append(marker)
    return changed
