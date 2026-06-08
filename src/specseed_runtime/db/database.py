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

from specseed_runtime.storage_paths import storage_db_path


DEFAULT_DB_PATH = storage_db_path("specseed.db")

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# Tasks that still represent outstanding work (the queue is "not empty").
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS)

# Two lanes share the table. ``control`` = fast deterministic work the scheduler's
# control thread drains to empty every tick (decide state, fast remote writes,
# schedule work). ``work`` = the heavy serial muscle (agents + git) drained by the
# work thread. See ``executing/priorities.py`` for the priority scale.
LANE_CONTROL = "control"
LANE_WORK = "work"
LANES = (LANE_CONTROL, LANE_WORK)

DEFAULT_PRIORITY = 50


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
        lane: str = LANE_CONTROL,
        priority: int = DEFAULT_PRIORITY,
    ) -> int:
        """Append a task to the queue and return its ``task_id``.

        ``action`` is a free-form verb the scheduler understands, such as
        ``handle_label_added`` or ``continue_impl``. ``post_id`` is the remote
        entry the task concerns (or ``None`` for global work). ``payload`` holds
        action-specific data and is stored as JSON. ``lane`` picks the control or
        work lane; ``priority`` orders within a lane (higher claimed first).
        """
        if not action or not action.strip():
            raise ValueError("action is required")
        if lane not in LANES:
            raise ValueError(f"unknown lane: {lane!r}")
        now = _now()
        with self._tx() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks(action, post_id, payload, status, attempts, created_at, lane, priority)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    action,
                    _post_id_to_text(post_id),
                    _payload_to_text(payload),
                    STATUS_PENDING,
                    now,
                    lane,
                    int(priority),
                ),
            )
            return int(cursor.lastrowid)

    def claim_next(self, lane: str = LANE_CONTROL) -> Optional[dict[str, Any]]:
        """Atomically claim the highest-priority *due* pending task in ``lane``.

        Ordering is ``priority`` descending then ``task_id`` ascending (FIFO within
        a priority). A task with a future ``not_before`` (a scheduled retry) is
        skipped until its time comes. The claimed task is moved to ``in_progress``,
        its ``attempts`` is incremented, and ``last_attempted_at`` is stamped.
        ``BEGIN IMMEDIATE`` plus the process lock guarantee that no two callers
        claim the same row.
        """
        with self._tx() as conn:
            row = conn.execute(
                f"""
                SELECT task_id FROM tasks
                WHERE status = '{STATUS_PENDING}'
                  AND lane = ?
                  AND (not_before IS NULL OR not_before <= ?)
                ORDER BY priority DESC, task_id ASC
                LIMIT 1
                """,
                (lane, _now()),
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

    def requeue(self, task_id: int, not_before: Optional[str] = None) -> None:
        """Return a task to ``pending`` so the scheduler may retry it.

        ``not_before`` (ISO-Z) delays the next claim - a scheduled retry with
        backoff. ``attempts`` and any recorded errors are preserved.
        """
        with self._tx() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, not_before = ? WHERE task_id = ?",
                (STATUS_PENDING, not_before, task_id),
            )

    def remove(self, task_id: int) -> None:
        """Delete a task. Its ``task_errors`` rows are intentionally kept."""
        with self._tx() as conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))

    def reset_in_progress(self) -> list[int]:
        """Return every ``in_progress`` task to ``pending``; list the task ids.

        Startup-only reclaim: with no scheduler draining, an ``in_progress`` row
        can only be the orphan of a dead runner.
        """
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT task_id FROM tasks WHERE status = ?", (STATUS_IN_PROGRESS,)
            ).fetchall()
            ids = [int(row["task_id"]) for row in rows]
            if ids:
                conn.execute(
                    "UPDATE tasks SET status = ? WHERE status = ?",
                    (STATUS_PENDING, STATUS_IN_PROGRESS),
                )
        return ids

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

    def pending_count(self, lane: Optional[str] = None, ready_now: bool = False) -> int:
        """Number of pending tasks, optionally scoped to ``lane``.

        ``ready_now`` excludes scheduled retries whose ``not_before`` is still in
        the future - the count of what could actually be claimed right now. The
        work thread uses ``pending_count(LANE_CONTROL, ready_now=True) == 0`` to
        decide the control lane is drained before it picks up a heavy job.
        """
        return self._count_status((STATUS_PENDING,), lane=lane, ready_now=ready_now)

    def active_count(self, lane: Optional[str] = None) -> int:
        """Number of tasks that are pending or in progress (optionally per lane)."""
        return self._count_status(ACTIVE_STATUSES, lane=lane)

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
    def _count_status(
        self,
        statuses: tuple[str, ...],
        lane: Optional[str] = None,
        ready_now: bool = False,
    ) -> int:
        placeholders = ",".join("?" for _ in statuses)
        clauses = [f"status IN ({placeholders})"]
        params: list[Any] = list(statuses)
        if lane is not None:
            clauses.append("lane = ?")
            params.append(lane)
        if ready_now:
            clauses.append("(not_before IS NULL OR not_before <= ?)")
            params.append(_now())
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM tasks WHERE {' AND '.join(clauses)}",
                params,
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
                    last_attempted_at TEXT,
                    not_before        TEXT,
                    lane              TEXT    NOT NULL DEFAULT '{LANE_CONTROL}'
                                      CHECK (lane IN ('{LANE_CONTROL}', '{LANE_WORK}')),
                    priority          INTEGER NOT NULL DEFAULT {DEFAULT_PRIORITY}
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
            # Self-heal a pre-0.18.0 table opened without the migration (the
            # Database is also constructed standalone, e.g. by the UI/tests). The
            # lane index below references these columns, so add them first.
            self._ensure_lane_columns(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_lane_claim "
                "ON tasks(lane, status, priority, task_id)"
            )

    @staticmethod
    def _ensure_lane_columns(conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "lane" not in cols:
            conn.execute(
                f"ALTER TABLE tasks ADD COLUMN lane TEXT NOT NULL DEFAULT '{LANE_CONTROL}'"
            )
        if "priority" not in cols:
            conn.execute(
                f"ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT {DEFAULT_PRIORITY}"
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
