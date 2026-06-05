"""
database.py - local work queue for the specseed scheduler.

The tracking remote (TrackingRemoteGitHub/TrackingRemoteGitLab/TrackingRemoteLocal)
is the source of truth for the actual work order: tickets, comments, labels,
open/closed state. This module is NOT a mirror of that. It is a small, durable
local store of *work to be done*, derived from the differences that
``TrackingBase.sync_from_remote`` reports on each poll.

Two tables:

* ``tasks``        - the queue. Append-only event log; drained FIFO by a
                     scheduler. One row per change-reaction or proactive step.
* ``task_errors``  - failures, kept separately so they survive task removal and
                     are never lost. Each row records when the attempt executed.

The store implements no policy. It only exposes accessors (enqueue, claim,
complete, remove, query). Deciding *what* to enqueue and *how* to react belongs
to the poller and scheduler.

Only Python stdlib is used. The ``Database`` is a thread-safe singleton: every
operation opens its own short-lived connection (so connections are never shared
across threads), WAL keeps concurrent readers/writers happy, and the row-claim
path uses ``BEGIN IMMEDIATE`` plus a process-local lock so two workers can never
claim the same task.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from specseed_target_src.storage_paths import storage_db_path


DEFAULT_DB_PATH = storage_db_path("specseed.db")

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# Tasks that still represent outstanding work (the queue is "not empty").
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS)


def _now() -> str:
    """Return an ISO-8601 UTC timestamp, e.g. '2026-06-04T12:00:00Z'."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Database:
    """Thread-safe singleton wrapper over the local task queue.

    Use :meth:`instance` for the shared process-wide singleton, or construct
    directly with an explicit ``db_path`` for isolated stores (e.g. tests).
    """

    _instance: Optional["Database"] = None
    _instance_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # construction / singleton
    # ------------------------------------------------------------------ #
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Serializes multi-statement operations (e.g. claim) within this process.
        self._lock = threading.RLock()
        self._init_db()

    @classmethod
    def instance(cls, db_path: str | Path = DEFAULT_DB_PATH) -> "Database":
        """Return the shared singleton, creating it on first call.

        ``db_path`` is only honored on the first call; later calls return the
        existing instance regardless of the argument.
        """
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(db_path)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Drop the shared singleton. Intended for tests."""
        with cls._instance_lock:
            cls._instance = None

    # ------------------------------------------------------------------ #
    # queue: writes
    # ------------------------------------------------------------------ #
    def enqueue(
        self,
        action: str,
        post_id: Optional[int | str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> int:
        """Append a task to the queue and return its ``task_id``.

        ``action`` is a free-form verb the scheduler understands, such as
        ``handle_label_added`` or ``continue_impl``. ``post_id`` is the remote
        entry the task concerns (or ``None`` for global work). ``payload`` holds
        action-specific data and is stored as JSON.
        """
        if not action or not action.strip():
            raise ValueError("action is required")
        now = _now()
        with self._tx() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks(action, post_id, payload, status, attempts, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (action, _post_id_to_text(post_id), _payload_to_text(payload), STATUS_PENDING, now),
            )
            return int(cursor.lastrowid)

    def claim_next(self) -> Optional[dict[str, Any]]:
        """Atomically claim the oldest pending task, or return ``None``.

        The claimed task is moved to ``in_progress``, its ``attempts`` is
        incremented, and ``last_attempted_at`` is stamped. ``BEGIN IMMEDIATE``
        plus the process lock guarantee that no two callers claim the same row.
        """
        with self._tx() as conn:
            row = conn.execute(
                f"""
                SELECT task_id FROM tasks
                WHERE status = '{STATUS_PENDING}'
                ORDER BY task_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            task_id = row["task_id"]
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, attempts = attempts + 1, last_attempted_at = ?
                WHERE task_id = ?
                """,
                (STATUS_IN_PROGRESS, _now(), task_id),
            )
            claimed = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(claimed)

    def complete(
        self,
        task_id: int,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Mark a task ``success`` or ``failed``.

        On failure, when ``error`` is provided, a row is recorded in
        ``task_errors`` stamped with the execution time so it is never lost.
        """
        now = _now()
        status = STATUS_SUCCESS if success else STATUS_FAILED
        with self._tx() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, last_attempted_at = ? WHERE task_id = ?",
                (status, now, task_id),
            )
            if not success and error is not None:
                conn.execute(
                    "INSERT INTO task_errors(task_id, message, executed_at) VALUES (?, ?, ?)",
                    (task_id, str(error), now),
                )

    def requeue(self, task_id: int) -> None:
        """Return a task to ``pending`` so the scheduler may retry it.

        ``attempts`` and any recorded errors are preserved.
        """
        with self._tx() as conn:
            conn.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ?",
                (STATUS_PENDING, task_id),
            )

    def remove(self, task_id: int) -> None:
        """Delete a task. Its ``task_errors`` rows are intentionally kept."""
        with self._tx() as conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))

    # ------------------------------------------------------------------ #
    # queue: reads
    # ------------------------------------------------------------------ #
    def get_task(self, task_id: int) -> Optional[dict[str, Any]]:
        """Return one task by id, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row)

    def tasks_for(self, post_id: int | str) -> list[dict[str, Any]]:
        """Return all tasks affecting a given remote post, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE post_id = ? ORDER BY task_id",
                (_post_id_to_text(post_id),),
            ).fetchall()
        return [_row_to_task(row) for row in rows]

    def pending_count(self) -> int:
        """Number of tasks still waiting to be claimed."""
        return self._count_status((STATUS_PENDING,))

    def active_count(self) -> int:
        """Number of tasks that are pending or in progress."""
        return self._count_status(ACTIVE_STATUSES)

    def is_empty(self) -> bool:
        """Whether the queue has no outstanding work.

        True when nothing is pending and nothing is in progress. This is the
        signal a scheduler uses to decide it may look for proactive work.
        """
        return self.active_count() == 0

    # ------------------------------------------------------------------ #
    # errors
    # ------------------------------------------------------------------ #
    def errors_for(self, task_id: int) -> list[dict[str, Any]]:
        """Return recorded errors for a task, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM task_errors WHERE task_id = ? ORDER BY error_id",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _count_status(self, statuses: tuple[str, ...]) -> int:
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM tasks WHERE status IN ({placeholders})",
                statuses,
            ).fetchone()
        return int(row["n"])

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None -> autocommit; transactions are managed explicitly
        # in _tx(). A generous busy timeout absorbs cross-process write contention.
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Run a write transaction guarded by the process lock and BEGIN IMMEDIATE."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    action            TEXT    NOT NULL,
                    post_id           TEXT,
                    payload           TEXT    NOT NULL DEFAULT '{{}}',
                    status            TEXT    NOT NULL DEFAULT '{STATUS_PENDING}'
                                      CHECK (status IN (
                                          '{STATUS_PENDING}', '{STATUS_IN_PROGRESS}',
                                          '{STATUS_SUCCESS}', '{STATUS_FAILED}'
                                      )),
                    attempts          INTEGER NOT NULL DEFAULT 0,
                    created_at        TEXT    NOT NULL,
                    last_attempted_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status_id
                    ON tasks(status, task_id);
                CREATE INDEX IF NOT EXISTS idx_tasks_post_id
                    ON tasks(post_id);

                CREATE TABLE IF NOT EXISTS task_errors (
                    error_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id     INTEGER NOT NULL,
                    message     TEXT    NOT NULL,
                    executed_at TEXT    NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_task_errors_task_id
                    ON task_errors(task_id);
                """
            )


# ---------------------------------------------------------------------- #
# module-level helpers
# ---------------------------------------------------------------------- #
def _post_id_to_text(post_id: Optional[int | str]) -> Optional[str]:
    # Remote ids may be int (GitHub) or str (GitLab iid); store uniformly as text.
    return None if post_id is None else str(post_id)


def _payload_to_text(payload: Optional[dict[str, Any]]) -> str:
    return json.dumps(payload or {}, separators=(",", ":"))


def _row_to_task(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    task = dict(row)
    raw = task.get("payload")
    try:
        task["payload"] = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        task["payload"] = {}
    return task
