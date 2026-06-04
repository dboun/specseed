"""
task_base.py - typed work items that get written to the DB queue.

Each concrete task (one class per file, e.g. ``HandleLabelAdded``) models its own
payload as a dataclass and knows how to build itself from a ``TrackingSyncChange``
and enqueue itself onto the DB. ``sync_to_db`` turns the change stream into these
tasks; the scheduler later drains them.

Supersession is identity-based: every task reports a ``resource_key`` derived from
the underlying remote resource (a comment, an entry, an entry+label pair, ...).
``sync_to_db`` removes any *pending* task sharing a new task's key before
enqueueing it, so a comment edit cancels the prior comment task, an add/remove
pair cancels out, and duplicate change-storms collapse to one row.

Only Python stdlib is used.
"""

from __future__ import annotations

from typing import Any, Optional

from src.target_facing.specseed_target_src.entities.entity_base import Entity, EntityRef


def resource_key(action: str, post_id: Optional[str], payload: dict[str, Any]) -> tuple:
    """Identity of the remote resource a task is about.

    Two tasks with equal ``resource_key`` are about the same thing, so a newer
    one supersedes an older pending one regardless of their actions. Works on
    both live Task instances and raw DB rows (action + post_id + payload dict).
    """
    pid = None if post_id is None else str(post_id)
    if action in ("handle_comment_added", "handle_comment_updated"):
        return ("comment", payload.get("comment_id"))
    if action in ("handle_label_added", "handle_label_removed"):
        return ("entry_label", pid, payload.get("label"))
    if action in ("handle_reaction_added", "handle_reaction_removed"):
        return ("reaction", payload.get("reaction_id"))
    if action in ("handle_entry_created", "handle_entry_updated", "handle_entry_reopened"):
        return ("entry", pid)
    return (action, pid)


def entity_ref_for_label(post_id: str, label: str) -> Optional[EntityRef]:
    """An EntityRef when a tier:/status: label gives us entity context, else None."""
    tier = Entity.tier_from_labels([label])
    status = Entity.status_from_labels([label])
    if tier is None and status is None:
        return None
    return EntityRef(post_id=str(post_id), tier=tier, status=status)


class Task:
    """Base class for everything that lands on the DB queue."""

    ACTION: str = ""

    def __init__(self, post_id: Optional[str | int], payload: dict[str, Any]) -> None:
        self.post_id: Optional[str] = None if post_id is None else str(post_id)
        self.payload: dict[str, Any] = dict(payload)

    def resource_key(self) -> tuple:
        return resource_key(self.ACTION, self.post_id, self.payload)

    def enqueue(self, db: Any) -> int:
        return db.enqueue(self.ACTION, post_id=self.post_id, payload=self.payload)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(post_id={self.post_id!r}, payload={self.payload!r})"
