"""
m_0_16_0__0_18_0.py - two-lane work queue (tasks.lane + tasks.priority).

0.18.0 splits the scheduler into a fast control lane and a serial work lane.
The queue gains two columns: ``lane`` (control|work) and ``priority`` (higher
claimed first). One storage touch:

* ``specseed.db``: ``ALTER TABLE tasks ADD COLUMN lane`` / ``priority`` - fresh
  dbs get them from the schema; existing dbs gain them here. Existing rows default
  to the control lane at the default priority (they predate the split, so the
  control lane is the safe home: it judges state and reschedules as needed).

SQLite cannot add a NOT NULL column without a constant default, so the columns
are added with defaults (control / 50). Idempotent: a column is only added when
missing; a missing/corrupt db is a no-op.

Only Python stdlib is used.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

FROM = "0.16.0"
TO = "0.18.0"

_DB = "specseed.db"


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def run(storage: str | Path, specseed_dir: str | Path) -> list[Path]:
    """Apply the hop. Returns the paths it changed."""
    storage = Path(storage)
    changed: list[Path] = []

    db_path = storage / _DB
    if not db_path.exists():
        return changed
    try:
        with sqlite3.connect(db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "tasks" not in tables:
                return changed
            touched = False
            if not _has_column(conn, "tasks", "lane"):
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN lane TEXT NOT NULL DEFAULT 'control'"
                )
                touched = True
            if not _has_column(conn, "tasks", "priority"):
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 50"
                )
                touched = True
            if touched:
                conn.commit()
                changed.append(db_path)
    except sqlite3.DatabaseError:
        pass  # not a sqlite file (corrupt/placeholder); leave it for the runtime
    return changed
