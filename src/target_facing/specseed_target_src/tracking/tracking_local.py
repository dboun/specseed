"""
tracking_local.py - local sqlite implementation of the TrackingBase contract.

This adapter gives specseed's remote-facing code a provider-shaped local
backend. It stores entries, labels, comments, reactions, and pin state in a
single sqlite database named tracking_local.db by default.

Only Python stdlib is used.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from specseed_target_src.tracking.tracking_base import (
    TrackingBase,
    TrackingCommentId,
    TrackingEntryComment,
    TrackingEntryDetails,
    TrackingEntryId,
    TrackingEntryOpenState,
    TrackingEntryReactionResult,
    TrackingEntrySummary,
    TrackingLabel,
    TrackingLabelList,
    TrackingLabelSet,
    TrackingPinState,
    TrackingReaction,
    TrackingReactionResult,
    TrackingResult,
    TrackingSyncChange,
)
from specseed_target_src.tracking.pull_request import (
    TrackingPullRequestDetails,
    TrackingPullRequestId,
    TrackingPullRequestOpenState,
    TrackingPullRequestSummary,
)
from specseed_target_src.storage_paths import storage_db_path


DEFAULT_DB_PATH = storage_db_path("tracking_local.db")


_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_clock_lock = threading.Lock()
_last_stamp = ""


def _now() -> str:
    """Return a strictly-increasing microsecond UTC timestamp.

    ``sync_from_remote`` detects changes by comparing ``updated_at`` strings, so
    two writes must never produce the same value. Microsecond precision makes
    collisions astronomically unlikely; a per-process monotonic guard removes the
    last of them by bumping the stamp by 1us if the wall clock has not advanced
    past the previous one. The fixed-width ``%f`` keeps stamps lexicographically
    ordered.
    """
    global _last_stamp
    with _clock_lock:
        stamp = datetime.now(timezone.utc).strftime(_STAMP_FORMAT)
        if stamp <= _last_stamp:
            prev = datetime.strptime(_last_stamp, _STAMP_FORMAT).replace(tzinfo=timezone.utc)
            stamp = (prev + timedelta(microseconds=1)).strftime(_STAMP_FORMAT)
        _last_stamp = stamp
        return stamp


def _assignees_to_json(assignees: Optional[list[str]]) -> str:
    return json.dumps(list(assignees or []), separators=(",", ":"))


def _assignees_from_json(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]


class TrackingLocal(TrackingBase):
    """Local sqlite-backed implementation of the provider-neutral interface."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, author: str = "local") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.author = author
        self._init_db()

    def list_entries(
        self,
        is_open: Optional[bool] = None,
        labels: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> TrackingResult:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, title, is_open, author, assignees, created_at, updated_at
                    FROM entries
                    ORDER BY id
                    """
                ).fetchall()
                entries = [self._summary_from_row(conn, row) for row in rows]

            if is_open is not None:
                entries = [entry for entry in entries if entry.is_open is is_open]
            if labels:
                wanted = set(labels)
                entries = [
                    entry
                    for entry in entries
                    if wanted.issubset({label.name for label in entry.labels})
                ]
            if assignee is not None:
                entries = [entry for entry in entries if assignee in entry.assignees]
            if updated_since is not None:
                entries = [
                    entry
                    for entry in entries
                    if entry.updated_at is not None and entry.updated_at >= updated_since
                ]
            return TrackingResult(ok=True, data=entries)
        except Exception as exc:
            return self._error(exc)

    def get_entry(self, entry_id: int | str) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                summary = self._summary_from_row(conn, row)
                comments = [
                    self._comment_from_row(conn, comment_row)
                    for comment_row in conn.execute(
                        """
                        SELECT id, entry_id, body, author, created_at, updated_at
                        FROM comments
                        WHERE entry_id = ?
                        ORDER BY id
                        """,
                        (row["id"],),
                    ).fetchall()
                ]
                details = TrackingEntryDetails(
                    id=summary.id,
                    title=summary.title,
                    labels=summary.labels,
                    is_open=summary.is_open,
                    url=summary.url,
                    author=summary.author,
                    assignees=summary.assignees,
                    created_at=summary.created_at,
                    updated_at=summary.updated_at,
                    body=row["body"],
                    comments=comments,
                    reactions=self._reactions_for_entry(conn, row["id"]),
                )
            return TrackingResult(ok=True, data=details)
        except Exception as exc:
            return self._error(exc)

    def is_entry_open(self, entry_id: int | str) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                return TrackingResult(
                    ok=True,
                    data=TrackingEntryOpenState(id=row["id"], is_open=bool(row["is_open"])),
                )
        except Exception as exc:
            return self._error(exc)

    def set_entry_open(self, entry_id: int | str) -> TrackingResult:
        return self._set_entry_state(entry_id, True)

    def set_entry_closed(self, entry_id: int | str) -> TrackingResult:
        return self._set_entry_state(entry_id, False)

    def pin_entry(self, entry_id: int | str) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                conn.execute(
                    "INSERT OR IGNORE INTO pinned_entries(entry_id) VALUES (?)",
                    (row["id"],),
                )
            return TrackingResult(ok=True, data=TrackingPinState(id=row["id"], pinned=True))
        except Exception as exc:
            return self._error(exc)

    def delete_entry(self, entry_id: int | str) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                # FKs are ON (see _connect): this cascades to entry_labels,
                # comments -> comment_reactions, entry_reactions, and pinned_entries.
                conn.execute("DELETE FROM entries WHERE id = ?", (row["id"],))
            return TrackingResult(ok=True, data=TrackingEntryId(id=row["id"]))
        except Exception as exc:
            return self._error(exc)

    def edit_entry(
        self,
        entry_id: int | str,
        title: Optional[str] = None,
        body: Optional[str] = None,
    ) -> TrackingResult:
        if title is not None and not title.strip():
            return TrackingResult(ok=False, error="entry title cannot be blank")
        if title is None and body is None:
            return TrackingResult(ok=False, error="nothing to edit (title and body both None)")
        try:
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                sets: list[str] = []
                values: list[object] = []
                if title is not None:
                    sets.append("title = ?")
                    values.append(title)
                if body is not None:
                    sets.append("body = ?")
                    values.append(body)
                sets.append("updated_at = ?")
                values.append(_now())
                values.append(row["id"])
                conn.execute(f"UPDATE entries SET {', '.join(sets)} WHERE id = ?", values)
            return TrackingResult(ok=True, data=TrackingEntryId(id=row["id"]))
        except Exception as exc:
            return self._error(exc)

    def add_entry(
        self,
        title: str,
        body: Optional[str] = None,
        labels: Optional[list[str]] = None,
        assignees: Optional[list[str]] = None,
    ) -> TrackingResult:
        if not title.strip():
            return TrackingResult(ok=False, error="entry title is required")
        try:
            stamp = _now()
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO entries(
                        title, body, is_open, author, assignees, created_at, updated_at
                    )
                    VALUES (?, ?, 1, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        body,
                        self.author,
                        _assignees_to_json(assignees),
                        stamp,
                        stamp,
                    ),
                )
                entry_id = cursor.lastrowid
                for label in labels or []:
                    self._ensure_label_conn(conn, label)
                    self._attach_label_conn(conn, entry_id, label)
            return TrackingResult(ok=True, data=TrackingEntryId(id=entry_id))
        except Exception as exc:
            return self._error(exc)

    def add_entry_comment(self, entry_id: int | str, body: str) -> TrackingResult:
        if not body:
            return TrackingResult(ok=False, error="comment body is required")
        try:
            stamp = _now()
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                cursor = conn.execute(
                    """
                    INSERT INTO comments(entry_id, body, author, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (row["id"], body, self.author, stamp, stamp),
                )
                self._touch_entry(conn, row["id"], stamp)
            return TrackingResult(ok=True, data=TrackingCommentId(id=cursor.lastrowid))
        except Exception as exc:
            return self._error(exc)

    def add_entry_comment_reaction(
        self,
        entry_id: int | str,
        comment_id: int | str,
        reaction: str,
    ) -> TrackingResult:
        if not self.is_supported_reaction(reaction):
            return TrackingResult(ok=False, error=f"unsupported reaction: {reaction}")
        try:
            with self._connect() as conn:
                entry_row = self._entry_row(conn, entry_id)
                if entry_row is None:
                    return self._missing_entry(entry_id)
                comment_row = self._comment_row(conn, entry_row["id"], comment_id)
                if comment_row is None:
                    return TrackingResult(ok=False, error=f"comment not found: {comment_id}")
                conn.execute(
                    """
                    INSERT INTO comment_reactions(comment_id, kind, user)
                    VALUES (?, ?, ?)
                    """,
                    (comment_row["id"], reaction, self.author),
                )
                self._touch_entry(conn, entry_row["id"], _now())
            return TrackingResult(
                ok=True,
                data=TrackingReactionResult(
                    entry_id=entry_row["id"],
                    comment_id=comment_row["id"],
                    reaction=reaction,
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def add_entry_reaction(self, entry_id: int | str, reaction: str) -> TrackingResult:
        if not self.is_supported_reaction(reaction):
            return TrackingResult(ok=False, error=f"unsupported reaction: {reaction}")
        try:
            with self._connect() as conn:
                entry_row = self._entry_row(conn, entry_id)
                if entry_row is None:
                    return self._missing_entry(entry_id)
                conn.execute(
                    "INSERT INTO entry_reactions(entry_id, kind, user) VALUES (?, ?, ?)",
                    (entry_row["id"], reaction, self.author),
                )
                self._touch_entry(conn, entry_row["id"], _now())
            return TrackingResult(
                ok=True,
                data=TrackingEntryReactionResult(entry_id=entry_row["id"], reaction=reaction),
            )
        except Exception as exc:
            return self._error(exc)

    def get_entry_labels(self, entry_id: int | str) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                return TrackingResult(
                    ok=True,
                    data=TrackingLabelSet(
                        entry_id=row["id"],
                        labels=self._labels_for_entry(conn, row["id"]),
                    ),
                )
        except Exception as exc:
            return self._error(exc)

    def add_entry_label(self, entry_id: int | str, label: str) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                self._ensure_label_conn(conn, label)
                self._attach_label_conn(conn, row["id"], label)
                self._touch_entry(conn, row["id"], _now())
                labels = self._labels_for_entry(conn, row["id"])
            return TrackingResult(ok=True, data=TrackingLabelSet(entry_id=row["id"], labels=labels))
        except Exception as exc:
            return self._error(exc)

    def remove_entry_label(self, entry_id: int | str, label: str) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                cursor = conn.execute(
                    "DELETE FROM entry_labels WHERE entry_id = ? AND label_name = ?",
                    (row["id"], label),
                )
                if cursor.rowcount:
                    self._touch_entry(conn, row["id"], _now())
                labels = self._labels_for_entry(conn, row["id"])
            return TrackingResult(ok=True, data=TrackingLabelSet(entry_id=row["id"], labels=labels))
        except Exception as exc:
            return self._error(exc)

    def list_labels(self) -> TrackingResult:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT name, color, description FROM labels ORDER BY name"
                ).fetchall()
                labels = [TrackingLabel(row["name"], row["color"], row["description"]) for row in rows]
            return TrackingResult(ok=True, data=TrackingLabelList(labels=labels))
        except Exception as exc:
            return self._error(exc)

    def create_label(
        self,
        name: str,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> TrackingResult:
        try:
            with self._connect() as conn:
                label = self._ensure_label_conn(conn, name, color, description)
            return TrackingResult(ok=True, data=label)
        except Exception as exc:
            return self._error(exc)

    def ensure_label(
        self,
        name: str,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> TrackingResult:
        return self.create_label(name, color, description)

    def sync_from_remote(self, remote: TrackingBase | str | Path) -> TrackingResult:
        """Make this local database match another local remote database.

        The source database is treated as authoritative for provider-visible
        resources. Entry ``updated_at`` values are used to avoid reading
        comments and reactions for entries whose top-level timestamp is
        unchanged in both databases.
        """
        try:
            source = self._coerce_local_remote(remote)
            if self.db_path.resolve() == source.db_path.resolve():
                return TrackingResult(ok=True, data=[])

            changes: list[TrackingSyncChange] = []
            stamp = _now()
            with source._connect() as source_conn, self._connect() as target_conn:
                source_snapshot = self._snapshot_static_resources(source_conn)
                target_snapshot = self._snapshot_static_resources(target_conn)

                self._sync_labels(target_conn, source_snapshot, target_snapshot, changes, stamp)

                source_entries = self._rows_by_id(
                    source_conn,
                    """
                    SELECT id, title, body, is_open, author, assignees, created_at, updated_at
                    FROM entries
                    """,
                )
                target_entries = self._rows_by_id(
                    target_conn,
                    """
                    SELECT id, title, body, is_open, author, assignees, created_at, updated_at
                    FROM entries
                    """,
                )
                source_pull_requests = self._rows_by_id(
                    source_conn,
                    """
                    SELECT id, title, body, is_open, is_merged, author, assignees,
                           source_branch, target_branch, created_at, updated_at
                    FROM pull_requests
                    """,
                )
                target_pull_requests = self._rows_by_id(
                    target_conn,
                    """
                    SELECT id, title, body, is_open, is_merged, author, assignees,
                           source_branch, target_branch, created_at, updated_at
                    FROM pull_requests
                    """,
                )
                pull_request_ids = set(source_pull_requests) | set(target_pull_requests)
                source_pull_request_dynamic = self._snapshot_pull_request_dynamic_resources(
                    source_conn, pull_request_ids
                )
                target_pull_request_dynamic = self._snapshot_pull_request_dynamic_resources(
                    target_conn, pull_request_ids
                )

                self._entry_sort_labels = self._labels_by_entry(source_snapshot)
                changed_entry_ids = self._sync_entries(
                    target_conn,
                    source_entries,
                    target_entries,
                    changes,
                    stamp,
                )
                changed_entry_ids.update(self._entry_ids_for_changed_relationships(source_snapshot, target_snapshot))
                changed_entry_ids.update(set(target_entries) - set(source_entries))

                source_dynamic = self._snapshot_dynamic_resources(source_conn, changed_entry_ids)
                target_dynamic = self._snapshot_dynamic_resources(target_conn, changed_entry_ids)

                self._sync_entry_labels(
                    target_conn, source_snapshot, target_snapshot, changes, stamp
                )
                self._sync_pins(target_conn, source_snapshot, target_snapshot, changes, stamp)
                self._sync_comments(target_conn, source_dynamic, target_dynamic, changes, stamp)
                self._sync_reactions(target_conn, source_dynamic, target_dynamic, changes, stamp)
                self._sync_entry_reactions(target_conn, source_dynamic, target_dynamic, changes, stamp)
                self._sync_pull_requests(
                    target_conn,
                    source_pull_requests,
                    target_pull_requests,
                    changes,
                    stamp,
                )
                self._sync_pull_request_labels(
                    target_conn, source_snapshot, target_snapshot, changes, stamp
                )
                self._sync_pull_request_comments(
                    target_conn,
                    source_pull_request_dynamic,
                    target_pull_request_dynamic,
                    changes,
                    stamp,
                )
                self._sync_pull_request_reactions(
                    target_conn,
                    source_pull_request_dynamic,
                    target_pull_request_dynamic,
                    changes,
                    stamp,
                )
                self._delete_removed_resources(
                    target_conn,
                    source_snapshot,
                    target_snapshot,
                    source_entries,
                    target_entries,
                    source_pull_requests,
                    target_pull_requests,
                    source_dynamic,
                    target_dynamic,
                    source_pull_request_dynamic,
                    target_pull_request_dynamic,
                    changes,
                    stamp,
                )

            return TrackingResult(ok=True, data=changes)
        except Exception as exc:
            return self._error(exc)

    def list_pull_requests(
        self,
        is_open: Optional[bool] = None,
        labels: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> TrackingResult:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, title, body, is_open, is_merged, author, assignees,
                           source_branch, target_branch, created_at, updated_at
                    FROM pull_requests
                    ORDER BY id
                    """
                ).fetchall()
                prs = [self._pull_request_summary_from_row(conn, row) for row in rows]
            if is_open is not None:
                prs = [pr for pr in prs if pr.is_open is is_open]
            if labels:
                wanted = set(labels)
                prs = [pr for pr in prs if wanted.issubset({label.name for label in pr.labels})]
            if assignee is not None:
                prs = [pr for pr in prs if assignee in pr.assignees]
            if updated_since is not None:
                prs = [pr for pr in prs if pr.updated_at is not None and pr.updated_at >= updated_since]
            return TrackingResult(ok=True, data=prs)
        except Exception as exc:
            return self._error(exc)

    def get_pull_request(self, pull_request_id: int | str) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._pull_request_row(conn, pull_request_id)
                if row is None:
                    return self._missing_pull_request(pull_request_id)
                summary = self._pull_request_summary_from_row(conn, row)
                comments = [
                    self._pull_request_comment_from_row(conn, comment_row)
                    for comment_row in conn.execute(
                        """
                        SELECT id, pull_request_id, body, author, created_at, updated_at
                        FROM pull_request_comments
                        WHERE pull_request_id = ?
                        ORDER BY id
                        """,
                        (row["id"],),
                    ).fetchall()
                ]
            return TrackingResult(
                ok=True,
                data=TrackingPullRequestDetails(
                    id=summary.id,
                    title=summary.title,
                    source_branch=summary.source_branch,
                    target_branch=summary.target_branch,
                    labels=summary.labels,
                    is_open=summary.is_open,
                    is_merged=summary.is_merged,
                    url=summary.url,
                    author=summary.author,
                    assignees=summary.assignees,
                    created_at=summary.created_at,
                    updated_at=summary.updated_at,
                    body=row["body"],
                    comments=comments,
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def add_pull_request(
        self,
        title: str,
        source_branch: str,
        target_branch: str,
        body: Optional[str] = None,
        labels: Optional[list[str]] = None,
        assignees: Optional[list[str]] = None,
    ) -> TrackingResult:
        if not title.strip():
            return TrackingResult(ok=False, error="pull request title is required")
        if not source_branch.strip() or not target_branch.strip():
            return TrackingResult(ok=False, error="source_branch and target_branch are required")
        try:
            stamp = _now()
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO pull_requests(
                        title, body, is_open, is_merged, author, assignees,
                        source_branch, target_branch, created_at, updated_at
                    )
                    VALUES (?, ?, 1, 0, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        body,
                        self.author,
                        _assignees_to_json(assignees),
                        source_branch,
                        target_branch,
                        stamp,
                        stamp,
                    ),
                )
                pr_id = cursor.lastrowid
                for label in labels or []:
                    self._ensure_label_conn(conn, label)
                    conn.execute(
                        "INSERT OR IGNORE INTO pull_request_labels(pull_request_id, label_name) VALUES (?, ?)",
                        (pr_id, label),
                    )
            return TrackingResult(ok=True, data=TrackingPullRequestId(id=pr_id))
        except Exception as exc:
            return self._error(exc)

    def add_pull_request_comment(self, pull_request_id: int | str, body: str) -> TrackingResult:
        if not body:
            return TrackingResult(ok=False, error="comment body is required")
        try:
            stamp = _now()
            with self._connect() as conn:
                row = self._pull_request_row(conn, pull_request_id)
                if row is None:
                    return self._missing_pull_request(pull_request_id)
                cursor = conn.execute(
                    """
                    INSERT INTO pull_request_comments(
                        pull_request_id, body, author, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (row["id"], body, self.author, stamp, stamp),
                )
                conn.execute(
                    "UPDATE pull_requests SET updated_at = ? WHERE id = ?",
                    (stamp, row["id"]),
                )
            return TrackingResult(ok=True, data=TrackingCommentId(id=cursor.lastrowid))
        except Exception as exc:
            return self._error(exc)

    def add_pull_request_comment_reaction(
        self,
        pull_request_id: int | str,
        comment_id: int | str,
        reaction: str,
    ) -> TrackingResult:
        if not self.is_supported_reaction(reaction):
            return TrackingResult(ok=False, error=f"unsupported reaction: {reaction}")
        try:
            with self._connect() as conn:
                row = self._pull_request_row(conn, pull_request_id)
                if row is None:
                    return self._missing_pull_request(pull_request_id)
                comment = conn.execute(
                    """
                    SELECT id FROM pull_request_comments
                    WHERE pull_request_id = ? AND id = ?
                    """,
                    (row["id"], comment_id),
                ).fetchone()
                if comment is None:
                    return TrackingResult(ok=False, error=f"comment not found: {comment_id}")
                conn.execute(
                    """
                    INSERT INTO pull_request_comment_reactions(comment_id, kind, user)
                    VALUES (?, ?, ?)
                    """,
                    (comment["id"], reaction, self.author),
                )
                conn.execute(
                    "UPDATE pull_requests SET updated_at = ? WHERE id = ?",
                    (_now(), row["id"]),
                )
            return TrackingResult(
                ok=True,
                data=TrackingReactionResult(
                    entry_id=pull_request_id,
                    comment_id=comment_id,
                    reaction=reaction,
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def set_pull_request_closed(self, pull_request_id: int | str) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._pull_request_row(conn, pull_request_id)
                if row is None:
                    return self._missing_pull_request(pull_request_id)
                conn.execute(
                    "UPDATE pull_requests SET is_open = 0, updated_at = ? WHERE id = ?",
                    (_now(), row["id"]),
                )
            return TrackingResult(
                ok=True,
                data=TrackingPullRequestOpenState(id=row["id"], is_open=False, is_merged=bool(row["is_merged"])),
            )
        except Exception as exc:
            return self._error(exc)

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT,
                    is_open INTEGER NOT NULL DEFAULT 1,
                    author TEXT,
                    assignees TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS labels (
                    name TEXT PRIMARY KEY,
                    color TEXT,
                    description TEXT
                );

                CREATE TABLE IF NOT EXISTS entry_labels (
                    entry_id INTEGER NOT NULL,
                    label_name TEXT NOT NULL,
                    PRIMARY KEY (entry_id, label_name),
                    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
                    FOREIGN KEY (label_name) REFERENCES labels(name) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    author TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS comment_reactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    comment_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    user TEXT,
                    FOREIGN KEY (comment_id) REFERENCES comments(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS entry_reactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    user TEXT,
                    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pinned_entries (
                    entry_id INTEGER PRIMARY KEY,
                    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pull_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT,
                    is_open INTEGER NOT NULL DEFAULT 1,
                    is_merged INTEGER NOT NULL DEFAULT 0,
                    author TEXT,
                    assignees TEXT NOT NULL DEFAULT '[]',
                    source_branch TEXT NOT NULL,
                    target_branch TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pull_request_labels (
                    pull_request_id INTEGER NOT NULL,
                    label_name TEXT NOT NULL,
                    PRIMARY KEY (pull_request_id, label_name),
                    FOREIGN KEY (pull_request_id) REFERENCES pull_requests(id) ON DELETE CASCADE,
                    FOREIGN KEY (label_name) REFERENCES labels(name) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pull_request_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pull_request_id INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    author TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (pull_request_id) REFERENCES pull_requests(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pull_request_comment_reactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    comment_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    user TEXT,
                    FOREIGN KEY (comment_id) REFERENCES pull_request_comments(id) ON DELETE CASCADE
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _entry_row(self, conn: sqlite3.Connection, entry_id: int | str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT id, title, body, is_open, author, assignees, created_at, updated_at
            FROM entries
            WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()

    def _comment_row(
        self, conn: sqlite3.Connection, entry_id: int | str, comment_id: int | str
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT id, entry_id, body, author, created_at, updated_at
            FROM comments
            WHERE entry_id = ? AND id = ?
            """,
            (entry_id, comment_id),
        ).fetchone()

    def _pull_request_row(
        self, conn: sqlite3.Connection, pull_request_id: int | str
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT id, title, body, is_open, is_merged, author, assignees,
                   source_branch, target_branch, created_at, updated_at
            FROM pull_requests
            WHERE id = ?
            """,
            (pull_request_id,),
        ).fetchone()

    def _summary_from_row(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> TrackingEntrySummary:
        entry_id = row["id"]
        return TrackingEntrySummary(
            id=entry_id,
            title=row["title"],
            labels=self._labels_for_entry(conn, entry_id),
            is_open=bool(row["is_open"]),
            url=f"local://tracking_local/entries/{entry_id}",
            author=row["author"],
            assignees=_assignees_from_json(row["assignees"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _comment_from_row(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> TrackingEntryComment:
        return TrackingEntryComment(
            id=row["id"],
            body=row["body"],
            author=row["author"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reactions=self._reactions_for_comment(conn, row["id"]),
        )

    def _pull_request_summary_from_row(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> TrackingPullRequestSummary:
        pr_id = row["id"]
        return TrackingPullRequestSummary(
            id=pr_id,
            title=row["title"],
            source_branch=row["source_branch"],
            target_branch=row["target_branch"],
            labels=self._labels_for_pull_request(conn, pr_id),
            is_open=bool(row["is_open"]),
            is_merged=bool(row["is_merged"]),
            url=f"local://tracking_local/pull_requests/{pr_id}",
            author=row["author"],
            assignees=_assignees_from_json(row["assignees"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _pull_request_comment_from_row(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> TrackingEntryComment:
        return TrackingEntryComment(
            id=row["id"],
            body=row["body"],
            pull_request_id=row["pull_request_id"],
            author=row["author"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reactions=self._reactions_for_pull_request_comment(conn, row["id"]),
        )

    def _labels_for_entry(
        self, conn: sqlite3.Connection, entry_id: int | str
    ) -> list[TrackingLabel]:
        rows = conn.execute(
            """
            SELECT labels.name, labels.color, labels.description
            FROM labels
            JOIN entry_labels ON entry_labels.label_name = labels.name
            WHERE entry_labels.entry_id = ?
            ORDER BY labels.name
            """,
            (entry_id,),
        ).fetchall()
        return [TrackingLabel(row["name"], row["color"], row["description"]) for row in rows]

    def _labels_for_pull_request(
        self, conn: sqlite3.Connection, pull_request_id: int | str
    ) -> list[TrackingLabel]:
        rows = conn.execute(
            """
            SELECT labels.name, labels.color, labels.description
            FROM labels
            JOIN pull_request_labels ON pull_request_labels.label_name = labels.name
            WHERE pull_request_labels.pull_request_id = ?
            ORDER BY labels.name
            """,
            (pull_request_id,),
        ).fetchall()
        return [TrackingLabel(row["name"], row["color"], row["description"]) for row in rows]

    def _reactions_for_comment(
        self, conn: sqlite3.Connection, comment_id: int | str
    ) -> list[TrackingReaction]:
        rows = conn.execute(
            """
            SELECT kind, COUNT(*) AS count, GROUP_CONCAT(user, char(31)) AS users
            FROM comment_reactions
            WHERE comment_id = ?
            GROUP BY kind
            ORDER BY kind
            """,
            (comment_id,),
        ).fetchall()
        reactions = []
        for row in rows:
            users = [user for user in (row["users"] or "").split(chr(31)) if user]
            reactions.append(TrackingReaction(kind=row["kind"], count=row["count"], users=users))
        return reactions

    def _reactions_for_entry(
        self, conn: sqlite3.Connection, entry_id: int | str
    ) -> list[TrackingReaction]:
        rows = conn.execute(
            """
            SELECT kind, COUNT(*) AS count, GROUP_CONCAT(user, char(31)) AS users
            FROM entry_reactions
            WHERE entry_id = ?
            GROUP BY kind
            ORDER BY kind
            """,
            (entry_id,),
        ).fetchall()
        reactions = []
        for row in rows:
            users = [user for user in (row["users"] or "").split(chr(31)) if user]
            reactions.append(TrackingReaction(kind=row["kind"], count=row["count"], users=users))
        return reactions

    def _reactions_for_pull_request_comment(
        self, conn: sqlite3.Connection, comment_id: int | str
    ) -> list[TrackingReaction]:
        rows = conn.execute(
            """
            SELECT kind, COUNT(*) AS count, GROUP_CONCAT(user, char(31)) AS users
            FROM pull_request_comment_reactions
            WHERE comment_id = ?
            GROUP BY kind
            ORDER BY kind
            """,
            (comment_id,),
        ).fetchall()
        return [
            TrackingReaction(
                kind=row["kind"],
                count=row["count"],
                users=[user for user in (row["users"] or "").split(chr(31)) if user],
            )
            for row in rows
        ]

    def _ensure_label_conn(
        self,
        conn: sqlite3.Connection,
        name: str,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> TrackingLabel:
        if not name.strip():
            raise ValueError("label name is required")
        existing = conn.execute(
            "SELECT name, color, description FROM labels WHERE name = ?",
            (name,),
        ).fetchone()
        if existing is not None:
            return TrackingLabel(existing["name"], existing["color"], existing["description"])
        conn.execute(
            "INSERT INTO labels(name, color, description) VALUES (?, ?, ?)",
            (name, color, description),
        )
        return TrackingLabel(name=name, color=color, description=description)

    def _attach_label_conn(
        self, conn: sqlite3.Connection, entry_id: int | str, label: str
    ) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO entry_labels(entry_id, label_name) VALUES (?, ?)",
            (entry_id, label),
        )

    def _set_entry_state(self, entry_id: int | str, is_open: bool) -> TrackingResult:
        try:
            with self._connect() as conn:
                row = self._entry_row(conn, entry_id)
                if row is None:
                    return self._missing_entry(entry_id)
                conn.execute(
                    "UPDATE entries SET is_open = ?, updated_at = ? WHERE id = ?",
                    (1 if is_open else 0, _now(), row["id"]),
                )
            return TrackingResult(
                ok=True,
                data=TrackingEntryOpenState(id=row["id"], is_open=is_open),
            )
        except Exception as exc:
            return self._error(exc)

    def _touch_entry(
        self, conn: sqlite3.Connection, entry_id: int | str, stamp: str
    ) -> None:
        conn.execute("UPDATE entries SET updated_at = ? WHERE id = ?", (stamp, entry_id))

    def _missing_entry(self, entry_id: int | str) -> TrackingResult:
        return TrackingResult(ok=False, error=f"entry not found: {entry_id}")

    def _missing_pull_request(self, pull_request_id: int | str) -> TrackingResult:
        return TrackingResult(ok=False, error=f"pull request not found: {pull_request_id}")

    def _error(self, exc: Exception) -> TrackingResult:
        return TrackingResult(ok=False, error=str(exc))

    def _coerce_local_remote(self, remote: TrackingBase | str | Path) -> "TrackingLocal":
        if isinstance(remote, TrackingLocal):
            return remote
        if isinstance(remote, (str, Path)):
            return TrackingLocal(db_path=remote, author=self.author)
        raise TypeError("TrackingLocal.sync_from_remote requires TrackingLocal or sqlite db path")

    def _snapshot_static_resources(self, conn: sqlite3.Connection) -> dict[str, object]:
        return {
            "labels": self._rows_by_key(
                conn, "SELECT name, color, description FROM labels", "name"
            ),
            "entry_labels": {
                (row["entry_id"], row["label_name"])
                for row in conn.execute(
                    "SELECT entry_id, label_name FROM entry_labels"
                ).fetchall()
            },
            "pinned_entries": {
                row["entry_id"]
                for row in conn.execute("SELECT entry_id FROM pinned_entries").fetchall()
            },
            "pull_request_labels": {
                (row["pull_request_id"], row["label_name"])
                for row in conn.execute(
                    "SELECT pull_request_id, label_name FROM pull_request_labels"
                ).fetchall()
            },
        }

    def _snapshot_dynamic_resources(
        self, conn: sqlite3.Connection, entry_ids: set[int | str]
    ) -> dict[str, object]:
        if not entry_ids:
            return {"comments": {}, "reactions": {}, "entry_reactions": {}}
        placeholders = ",".join("?" for _ in entry_ids)
        ids = tuple(entry_ids)
        comments = self._rows_by_id(
            conn,
            f"""
            SELECT id, entry_id, body, author, created_at, updated_at
            FROM comments
            WHERE entry_id IN ({placeholders})
            """,
            ids,
        )
        entry_reactions = self._rows_by_id(
            conn,
            f"""
            SELECT id, entry_id, kind, user
            FROM entry_reactions
            WHERE entry_id IN ({placeholders})
            """,
            ids,
        )
        comment_ids = set(comments)
        if not comment_ids:
            return {"comments": comments, "reactions": {}, "entry_reactions": entry_reactions}
        comment_placeholders = ",".join("?" for _ in comment_ids)
        reactions = self._rows_by_id(
            conn,
            f"""
            SELECT id, comment_id, kind, user
            FROM comment_reactions
            WHERE comment_id IN ({comment_placeholders})
            """,
            tuple(comment_ids),
        )
        return {"comments": comments, "reactions": reactions, "entry_reactions": entry_reactions}

    def _snapshot_pull_request_dynamic_resources(
        self, conn: sqlite3.Connection, pull_request_ids: set[int | str]
    ) -> dict[str, object]:
        if not pull_request_ids:
            return {"pull_request_comments": {}, "pull_request_reactions": {}}
        placeholders = ",".join("?" for _ in pull_request_ids)
        ids = tuple(pull_request_ids)
        comments = self._rows_by_id(
            conn,
            f"""
            SELECT id, pull_request_id, body, author, created_at, updated_at
            FROM pull_request_comments
            WHERE pull_request_id IN ({placeholders})
            """,
            ids,
        )
        comment_ids = set(comments)
        if not comment_ids:
            return {"pull_request_comments": comments, "pull_request_reactions": {}}
        comment_placeholders = ",".join("?" for _ in comment_ids)
        reactions = self._rows_by_id(
            conn,
            f"""
            SELECT id, comment_id, kind, user
            FROM pull_request_comment_reactions
            WHERE comment_id IN ({comment_placeholders})
            """,
            tuple(comment_ids),
        )
        return {"pull_request_comments": comments, "pull_request_reactions": reactions}

    def _rows_by_id(
        self,
        conn: sqlite3.Connection,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> dict[int, dict[str, object]]:
        return {row["id"]: dict(row) for row in conn.execute(sql, params).fetchall()}

    def _rows_by_key(
        self,
        conn: sqlite3.Connection,
        sql: str,
        key: str,
        params: tuple[object, ...] = (),
    ) -> dict[object, dict[str, object]]:
        return {row[key]: dict(row) for row in conn.execute(sql, params).fetchall()}

    def _sync_labels(
        self,
        conn: sqlite3.Connection,
        source: dict[str, object],
        target: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        source_labels = source["labels"]
        target_labels = target["labels"]
        for name in sorted(source_labels):
            source_row = source_labels[name]
            target_row = target_labels.get(name)
            if target_row is None:
                conn.execute(
                    "INSERT INTO labels(name, color, description) VALUES (?, ?, ?)",
                    (name, source_row["color"], source_row["description"]),
                )
                self._record_change(changes, "create", "label", name, None, None, None, source_row, stamp)
                continue
            for field in ("color", "description"):
                if target_row[field] != source_row[field]:
                    conn.execute(
                        f"UPDATE labels SET {field} = ? WHERE name = ?",
                        (source_row[field], name),
                    )
                    self._record_change(
                        changes,
                        "update",
                        "label",
                        name,
                        None,
                        field,
                        target_row[field],
                        source_row[field],
                        stamp,
                    )

    def _sync_entries(
        self,
        conn: sqlite3.Connection,
        source_entries: dict[int, dict[str, object]],
        target_entries: dict[int, dict[str, object]],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> set[int]:
        changed_entry_ids: set[int] = set()
        for entry_id in sorted(source_entries, key=lambda eid: self._entry_sort_key(source_entries[eid])):
            source_row = source_entries[entry_id]
            target_row = target_entries.get(entry_id)
            if target_row is None:
                conn.execute(
                    """
                    INSERT INTO entries(
                        id, title, body, is_open, author, assignees, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        source_row["title"],
                        source_row["body"],
                        source_row["is_open"],
                        source_row["author"],
                        source_row["assignees"],
                        source_row["created_at"],
                        source_row["updated_at"],
                    ),
                )
                changed_entry_ids.add(entry_id)
                self._record_change(changes, "create", "entry", entry_id, None, None, None, source_row, stamp)
                continue
            if target_row["updated_at"] == source_row["updated_at"]:
                continue
            for field in ("title", "body", "is_open", "author", "assignees", "created_at", "updated_at"):
                if target_row[field] == source_row[field]:
                    continue
                conn.execute(
                    f"UPDATE entries SET {field} = ? WHERE id = ?",
                    (source_row[field], entry_id),
                )
                changed_entry_ids.add(entry_id)
                action = "state" if field == "is_open" else "update"
                self._record_change(
                    changes,
                    action,
                    "entry",
                    entry_id,
                    None,
                    field,
                    target_row[field],
                    source_row[field],
                    stamp,
                )
        return changed_entry_ids

    def _entry_ids_for_changed_relationships(
        self, source: dict[str, object], target: dict[str, object]
    ) -> set[int]:
        changed: set[int] = set()
        for entry_id, _label in source["entry_labels"] ^ target["entry_labels"]:
            changed.add(entry_id)
        changed.update(source["pinned_entries"] ^ target["pinned_entries"])
        return changed

    def _labels_by_entry(self, snapshot: dict[str, object]) -> dict[int, set[str]]:
        labels_by_entry: dict[int, set[str]] = {}
        for entry_id, label in snapshot["entry_labels"]:
            labels_by_entry.setdefault(entry_id, set()).add(label)
        return labels_by_entry

    def _sync_entry_labels(
        self,
        conn: sqlite3.Connection,
        source: dict[str, object],
        target: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        for entry_id, label in sorted(source["entry_labels"] - target["entry_labels"]):
            conn.execute(
                "INSERT OR IGNORE INTO entry_labels(entry_id, label_name) VALUES (?, ?)",
                (entry_id, label),
            )
            self._record_change(
                changes, "create", "entry_label", f"{entry_id}:{label}", entry_id, None, None, label, stamp
            )

    def _sync_pins(
        self,
        conn: sqlite3.Connection,
        source: dict[str, object],
        target: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        for entry_id in sorted(source["pinned_entries"] - target["pinned_entries"]):
            conn.execute("INSERT OR IGNORE INTO pinned_entries(entry_id) VALUES (?)", (entry_id,))
            self._record_change(changes, "create", "pin", entry_id, entry_id, None, None, True, stamp)

    def _sync_pull_requests(
        self,
        conn: sqlite3.Connection,
        source_pull_requests: dict[int, dict[str, object]],
        target_pull_requests: dict[int, dict[str, object]],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        fields = (
            "title",
            "body",
            "is_open",
            "is_merged",
            "author",
            "assignees",
            "source_branch",
            "target_branch",
            "created_at",
            "updated_at",
        )
        for pr_id in sorted(source_pull_requests):
            source_row = source_pull_requests[pr_id]
            target_row = target_pull_requests.get(pr_id)
            if target_row is None:
                conn.execute(
                    """
                    INSERT INTO pull_requests(
                        id, title, body, is_open, is_merged, author, assignees,
                        source_branch, target_branch, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pr_id,
                        source_row["title"],
                        source_row["body"],
                        source_row["is_open"],
                        source_row["is_merged"],
                        source_row["author"],
                        source_row["assignees"],
                        source_row["source_branch"],
                        source_row["target_branch"],
                        source_row["created_at"],
                        source_row["updated_at"],
                    ),
                )
                self._record_change(
                    changes, "create", "pull_request", pr_id, None, None, None, source_row, stamp
                )
                continue
            if target_row["updated_at"] == source_row["updated_at"]:
                continue
            for field in fields:
                if target_row[field] == source_row[field]:
                    continue
                conn.execute(
                    f"UPDATE pull_requests SET {field} = ? WHERE id = ?",
                    (source_row[field], pr_id),
                )
                action = "state" if field == "is_open" else "update"
                self._record_change(
                    changes,
                    action,
                    "pull_request",
                    pr_id,
                    None,
                    field,
                    target_row[field],
                    source_row[field],
                    stamp,
                )

    def _sync_pull_request_labels(
        self,
        conn: sqlite3.Connection,
        source: dict[str, object],
        target: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        for pr_id, label in sorted(source["pull_request_labels"] - target["pull_request_labels"]):
            conn.execute(
                "INSERT OR IGNORE INTO pull_request_labels(pull_request_id, label_name) VALUES (?, ?)",
                (pr_id, label),
            )
            self._record_change(
                changes,
                "create",
                "pull_request_label",
                f"{pr_id}:{label}",
                pr_id,
                None,
                None,
                label,
                stamp,
            )

    def _sync_pull_request_comments(
        self,
        conn: sqlite3.Connection,
        source: dict[str, object],
        target: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        source_comments = source["pull_request_comments"]
        target_comments = target["pull_request_comments"]
        for comment_id in sorted(source_comments):
            source_row = source_comments[comment_id]
            target_row = target_comments.get(comment_id)
            if target_row is None:
                conn.execute(
                    """
                    INSERT INTO pull_request_comments(
                        id, pull_request_id, body, author, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comment_id,
                        source_row["pull_request_id"],
                        source_row["body"],
                        source_row["author"],
                        source_row["created_at"],
                        source_row["updated_at"],
                    ),
                )
                self._record_change(
                    changes,
                    "create",
                    "pull_request_comment",
                    comment_id,
                    source_row["pull_request_id"],
                    None,
                    None,
                    source_row,
                    stamp,
                )
                continue
            if target_row["updated_at"] == source_row["updated_at"]:
                continue
            for field in ("pull_request_id", "body", "author", "created_at", "updated_at"):
                if target_row[field] == source_row[field]:
                    continue
                conn.execute(
                    f"UPDATE pull_request_comments SET {field} = ? WHERE id = ?",
                    (source_row[field], comment_id),
                )
                self._record_change(
                    changes,
                    "update",
                    "pull_request_comment",
                    comment_id,
                    source_row["pull_request_id"],
                    field,
                    target_row[field],
                    source_row[field],
                    stamp,
                )

    def _sync_pull_request_reactions(
        self,
        conn: sqlite3.Connection,
        source: dict[str, object],
        target: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        source_reactions = source["pull_request_reactions"]
        target_reactions = target["pull_request_reactions"]
        for reaction_id in sorted(source_reactions):
            source_row = source_reactions[reaction_id]
            target_row = target_reactions.get(reaction_id)
            if target_row is None:
                conn.execute(
                    """
                    INSERT INTO pull_request_comment_reactions(id, comment_id, kind, user)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        reaction_id,
                        source_row["comment_id"],
                        source_row["kind"],
                        source_row["user"],
                    ),
                )
                self._record_change(
                    changes,
                    "create",
                    "pull_request_reaction",
                    reaction_id,
                    source_row["comment_id"],
                    None,
                    None,
                    source_row,
                    stamp,
                )
                continue
            for field in ("comment_id", "kind", "user"):
                if target_row[field] == source_row[field]:
                    continue
                conn.execute(
                    f"UPDATE pull_request_comment_reactions SET {field} = ? WHERE id = ?",
                    (source_row[field], reaction_id),
                )
                self._record_change(
                    changes,
                    "update",
                    "pull_request_reaction",
                    reaction_id,
                    source_row["comment_id"],
                    field,
                    target_row[field],
                    source_row[field],
                    stamp,
                )

    def _sync_comments(
        self,
        conn: sqlite3.Connection,
        source: dict[str, object],
        target: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        source_comments = source["comments"]
        target_comments = target["comments"]
        for comment_id in sorted(source_comments):
            source_row = source_comments[comment_id]
            target_row = target_comments.get(comment_id)
            if target_row is None:
                conn.execute(
                    """
                    INSERT INTO comments(id, entry_id, body, author, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comment_id,
                        source_row["entry_id"],
                        source_row["body"],
                        source_row["author"],
                        source_row["created_at"],
                        source_row["updated_at"],
                    ),
                )
                self._record_change(
                    changes,
                    "create",
                    "comment",
                    comment_id,
                    source_row["entry_id"],
                    None,
                    None,
                    source_row,
                    stamp,
                )
                continue
            if target_row["updated_at"] == source_row["updated_at"]:
                continue
            for field in ("entry_id", "body", "author", "created_at", "updated_at"):
                if target_row[field] == source_row[field]:
                    continue
                conn.execute(
                    f"UPDATE comments SET {field} = ? WHERE id = ?",
                    (source_row[field], comment_id),
                )
                self._record_change(
                    changes,
                    "update",
                    "comment",
                    comment_id,
                    source_row["entry_id"],
                    field,
                    target_row[field],
                    source_row[field],
                    stamp,
                )

    def _sync_reactions(
        self,
        conn: sqlite3.Connection,
        source: dict[str, object],
        target: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        source_reactions = source["reactions"]
        target_reactions = target["reactions"]
        for reaction_id in sorted(source_reactions):
            source_row = source_reactions[reaction_id]
            target_row = target_reactions.get(reaction_id)
            if target_row is None:
                conn.execute(
                    """
                    INSERT INTO comment_reactions(id, comment_id, kind, user)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        reaction_id,
                        source_row["comment_id"],
                        source_row["kind"],
                        source_row["user"],
                    ),
                )
                self._record_change(
                    changes,
                    "create",
                    "reaction",
                    reaction_id,
                    source_row["comment_id"],
                    None,
                    None,
                    source_row,
                    stamp,
                )
                continue
            for field in ("comment_id", "kind", "user"):
                if target_row[field] == source_row[field]:
                    continue
                conn.execute(
                    f"UPDATE comment_reactions SET {field} = ? WHERE id = ?",
                    (source_row[field], reaction_id),
                )
                self._record_change(
                    changes,
                    "update",
                    "reaction",
                    reaction_id,
                    source_row["comment_id"],
                    field,
                    target_row[field],
                    source_row[field],
                    stamp,
                )

    def _sync_entry_reactions(
        self,
        conn: sqlite3.Connection,
        source: dict[str, object],
        target: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        source_reactions = source["entry_reactions"]
        target_reactions = target["entry_reactions"]
        for reaction_id in sorted(source_reactions):
            source_row = source_reactions[reaction_id]
            target_row = target_reactions.get(reaction_id)
            if target_row is None:
                conn.execute(
                    "INSERT INTO entry_reactions(id, entry_id, kind, user) VALUES (?, ?, ?, ?)",
                    (reaction_id, source_row["entry_id"], source_row["kind"], source_row["user"]),
                )
                self._record_change(
                    changes,
                    "create",
                    "entry_reaction",
                    reaction_id,
                    source_row["entry_id"],
                    None,
                    None,
                    source_row,
                    stamp,
                )
                continue
            for field in ("entry_id", "kind", "user"):
                if target_row[field] == source_row[field]:
                    continue
                conn.execute(
                    f"UPDATE entry_reactions SET {field} = ? WHERE id = ?",
                    (source_row[field], reaction_id),
                )
                self._record_change(
                    changes,
                    "update",
                    "entry_reaction",
                    reaction_id,
                    source_row["entry_id"],
                    field,
                    target_row[field],
                    source_row[field],
                    stamp,
                )

    def _delete_removed_resources(
        self,
        conn: sqlite3.Connection,
        source_static: dict[str, object],
        target_static: dict[str, object],
        source_entries: dict[int, dict[str, object]],
        target_entries: dict[int, dict[str, object]],
        source_pull_requests: dict[int, dict[str, object]],
        target_pull_requests: dict[int, dict[str, object]],
        source_dynamic: dict[str, object],
        target_dynamic: dict[str, object],
        source_pull_request_dynamic: dict[str, object],
        target_pull_request_dynamic: dict[str, object],
        changes: list[TrackingSyncChange],
        stamp: str,
    ) -> None:
        source_comments = source_dynamic["comments"]
        target_comments = target_dynamic["comments"]
        source_reactions = source_dynamic["reactions"]
        target_reactions = target_dynamic["reactions"]

        for reaction_id in sorted(set(target_reactions) - set(source_reactions)):
            row = target_reactions[reaction_id]
            conn.execute("DELETE FROM comment_reactions WHERE id = ?", (reaction_id,))
            self._record_change(changes, "delete", "reaction", reaction_id, row["comment_id"], None, row, None, stamp)

        source_entry_reactions = source_dynamic["entry_reactions"]
        target_entry_reactions = target_dynamic["entry_reactions"]
        for reaction_id in sorted(set(target_entry_reactions) - set(source_entry_reactions)):
            row = target_entry_reactions[reaction_id]
            conn.execute("DELETE FROM entry_reactions WHERE id = ?", (reaction_id,))
            self._record_change(
                changes, "delete", "entry_reaction", reaction_id, row["entry_id"], None, row, None, stamp
            )

        for comment_id in sorted(set(target_comments) - set(source_comments)):
            row = target_comments[comment_id]
            conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            self._record_change(changes, "delete", "comment", comment_id, row["entry_id"], None, row, None, stamp)

        for entry_id, label in sorted(target_static["entry_labels"] - source_static["entry_labels"]):
            conn.execute(
                "DELETE FROM entry_labels WHERE entry_id = ? AND label_name = ?",
                (entry_id, label),
            )
            self._record_change(
                changes, "delete", "entry_label", f"{entry_id}:{label}", entry_id, None, label, None, stamp
            )

        for entry_id in sorted(target_static["pinned_entries"] - source_static["pinned_entries"]):
            conn.execute("DELETE FROM pinned_entries WHERE entry_id = ?", (entry_id,))
            self._record_change(changes, "delete", "pin", entry_id, entry_id, None, True, None, stamp)

        for entry_id in sorted(set(target_entries) - set(source_entries)):
            row = target_entries[entry_id]
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            self._record_change(changes, "delete", "entry", entry_id, None, None, row, None, stamp)

        for pr_id, label in sorted(target_static["pull_request_labels"] - source_static["pull_request_labels"]):
            conn.execute(
                "DELETE FROM pull_request_labels WHERE pull_request_id = ? AND label_name = ?",
                (pr_id, label),
            )
            self._record_change(
                changes,
                "delete",
                "pull_request_label",
                f"{pr_id}:{label}",
                pr_id,
                None,
                label,
                None,
                stamp,
            )

        source_pr_reactions = source_pull_request_dynamic["pull_request_reactions"]
        target_pr_reactions = target_pull_request_dynamic["pull_request_reactions"]
        for reaction_id in sorted(set(target_pr_reactions) - set(source_pr_reactions)):
            row = target_pr_reactions[reaction_id]
            conn.execute("DELETE FROM pull_request_comment_reactions WHERE id = ?", (reaction_id,))
            self._record_change(
                changes,
                "delete",
                "pull_request_reaction",
                reaction_id,
                row["comment_id"],
                None,
                row,
                None,
                stamp,
            )

        source_pr_comments = source_pull_request_dynamic["pull_request_comments"]
        target_pr_comments = target_pull_request_dynamic["pull_request_comments"]
        for comment_id in sorted(set(target_pr_comments) - set(source_pr_comments)):
            row = target_pr_comments[comment_id]
            conn.execute("DELETE FROM pull_request_comments WHERE id = ?", (comment_id,))
            self._record_change(
                changes,
                "delete",
                "pull_request_comment",
                comment_id,
                row["pull_request_id"],
                None,
                row,
                None,
                stamp,
            )

        for pr_id in sorted(set(target_pull_requests) - set(source_pull_requests)):
            row = target_pull_requests[pr_id]
            conn.execute("DELETE FROM pull_requests WHERE id = ?", (pr_id,))
            self._record_change(
                changes, "delete", "pull_request", pr_id, None, None, row, None, stamp
            )

        for name in sorted(set(target_static["labels"]) - set(source_static["labels"])):
            row = target_static["labels"][name]
            conn.execute("DELETE FROM labels WHERE name = ?", (name,))
            self._record_change(changes, "delete", "label", name, None, None, row, None, stamp)

    def _record_change(
        self,
        changes: list[TrackingSyncChange],
        action: str,
        resource_type: str,
        resource_id: int | str,
        parent_id: int | str | None,
        field: str | None,
        old: object,
        new: object,
        stamp: str,
    ) -> None:
        changes.append(
            TrackingSyncChange(
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                parent_id=parent_id,
                field=field,
                old=old,
                new=new,
                at=stamp,
            )
        )

    def _entry_sort_key(self, row: dict[str, object]) -> tuple[int, int]:
        labels = set()
        try:
            entry_labels = getattr(self, "_entry_sort_labels")
        except AttributeError:
            entry_labels = {}
        labels = entry_labels.get(row["id"], labels)
        tier_order = {"tier:epic": 0, "epic": 0, "tier:ticket": 1, "ticket": 1, "tier:issue": 2, "issue": 2}
        tier = min((tier_order[label] for label in labels if label in tier_order), default=3)
        return (tier, int(row["id"]))
