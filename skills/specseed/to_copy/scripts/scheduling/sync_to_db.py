"""
sync_to_db.py - turn a remote sync into DB queue operations.

This is the bridge between the tracking layer and the work queue. Given a local
tracker and a remote (source of truth), it:

  1. calls ``local.sync_from_remote(remote)`` to learn what changed, and
  2. translates each change into a typed task on the DB queue.

It is deliberately the only "intelligent" piece:

* **Mapping** - each (resource_type, action) maps to a concrete task class in
  ``../tasks`` (or is ignored). Entry edits coalesce into one task.
* **Supersession** - before enqueueing a task, any *pending* task about the same
  remote resource is removed (``resource_key``). So a comment edit cancels the
  prior comment task, a label add/remove pair cancels out, and duplicate change
  storms collapse to a single row.
* **Teardown** - when an entry is closed or deleted, every pending task for that
  entry is dropped. If a task for it was *in progress*, we enqueue a CleanupTask
  and (TODO) interrupt the running work - we never edit the in-progress row.

Ordering is inherited from ``sync_from_remote`` (tier-sorted creates,
leaf-first deletes), so tasks land in a sane order without extra work here.

Only Python stdlib is used.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

_SCRIPTS = Path(__file__).resolve().parents[1]
for _sub in ("db", "tracking", "tasks", "entities"):
    sys.path.insert(0, str(_SCRIPTS / _sub))

from database import Database  # noqa: E402
from task_base import resource_key  # noqa: E402
from handle_entry_created import HandleEntryCreated  # noqa: E402
from handle_entry_updated import HandleEntryUpdated  # noqa: E402
from handle_entry_reopened import HandleEntryReopened  # noqa: E402
from handle_label_added import HandleLabelAdded  # noqa: E402
from handle_label_removed import HandleLabelRemoved  # noqa: E402
from handle_comment_added import HandleCommentAdded  # noqa: E402
from handle_comment_updated import HandleCommentUpdated  # noqa: E402
from handle_reaction_added import HandleReactionAdded  # noqa: E402
from handle_reaction_removed import HandleReactionRemoved  # noqa: E402
from cleanup_task import CleanupTask  # noqa: E402


def sync_to_db(local: Any, remote: Any, db: Optional[Database] = None) -> dict[str, Any]:
    """Sync ``remote`` into ``local`` and apply the resulting changes to the queue.

    Returns a summary dict; on a failed sync, ``{"ok": False, "error": ...}``.
    """
    db = db or Database.instance()
    result = local.sync_from_remote(remote)
    if not result.ok:
        return {"ok": False, "error": result.error}

    summary = {
        "ok": True,
        "changes": len(result.data),
        "enqueued": 0,
        "superseded": 0,
        "coalesced": 0,
        "cleanups": 0,
        "interrupts_todo": 0,
        "ignored": 0,
    }
    seen_keys: set[tuple] = set()  # within-run coalescing for entry edits

    for change in result.data:
        if _is_entry_teardown(change):
            _teardown_post(db, str(change.resource_id), change.action, summary)
            continue

        task = _build_task(change)
        if task is None:
            summary["ignored"] += 1
            continue

        key = task.resource_key()
        if task.ACTION == HandleEntryUpdated.ACTION:
            if key in seen_keys:
                summary["coalesced"] += 1
                continue
            seen_keys.add(key)

        summary["superseded"] += _supersede(db, task)
        task.enqueue(db)
        summary["enqueued"] += 1

    return summary


def _build_task(change: Any):
    rt, action = change.resource_type, change.action
    if rt == "entry":
        if action == "create":
            return HandleEntryCreated.from_change(change)
        if action == "update":
            return HandleEntryUpdated.from_change(change)
        if action == "state" and change.field == "is_open" and _is_open(change.new):
            return HandleEntryReopened.from_change(change)
        return None  # closes/deletes handled by teardown; other state fields ignored
    if rt == "entry_label":
        if action == "create":
            return HandleLabelAdded.from_change(change)
        if action == "delete":
            return HandleLabelRemoved.from_change(change)
        return None
    if rt == "comment":
        if action == "create":
            return HandleCommentAdded.from_change(change)
        if action == "update":
            return HandleCommentUpdated.from_change(change)
        return None
    if rt == "reaction":
        if action == "create":
            return HandleReactionAdded.from_change(change)
        if action == "delete":
            return HandleReactionRemoved.from_change(change)
        return None
    # repo-level labels and pins imply no agent work.
    return None


def _is_entry_teardown(change: Any) -> bool:
    if change.resource_type != "entry":
        return False
    if change.action == "delete":
        return True
    return change.action == "state" and change.field == "is_open" and not _is_open(change.new)


def _is_open(value: Any) -> bool:
    # is_open is stored as 0/1; tolerate strings/bools/None too.
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "")
    return bool(value)


def _supersede(db: Database, task: Any) -> int:
    """Remove pending tasks about the same resource as ``task``. Returns count."""
    if task.post_id is None:
        return 0
    key = task.resource_key()
    removed = 0
    for row in db.tasks_for(task.post_id):
        if row["status"] != "pending":
            continue
        if resource_key(row["action"], row["post_id"], row["payload"]) == key:
            db.remove(row["task_id"])
            removed += 1
    return removed


def _teardown_post(db: Database, post_id: str, action: str, summary: dict[str, Any]) -> None:
    in_progress: list[dict[str, Any]] = []
    for row in db.tasks_for(post_id):
        if row["status"] == "pending":
            db.remove(row["task_id"])
            summary["superseded"] += 1
        elif row["status"] == "in_progress":
            in_progress.append(row)

    for row in in_progress:
        _request_interrupt(row)  # TODO: real interruption once execution exists.
        summary["interrupts_todo"] += 1
        CleanupTask.for_post(
            post_id, reason=f"entry_{action}", interrupted_task_id=row["task_id"]
        ).enqueue(db)
        summary["cleanups"] += 1


def _request_interrupt(row: dict[str, Any]) -> None:
    """Placeholder for stopping a running task.

    There is no execution engine yet, so this is intentionally a no-op: we must
    never edit an in-progress row. When a runner exists, this should signal it to
    abort task ``row["task_id"]`` before the CleanupTask runs.
    """
    # TODO(scheduler): wire cooperative cancellation of the running task here.
    return None
