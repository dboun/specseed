"""handle_entry_created.py - a new entry (epic/ticket/issue) appeared on the remote."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from specseed_target_src.entities.entity_base import EntityRef
from specseed_target_src.tasks.task_base import Task


@dataclass
class EntryCreatedPayload:
    title: Optional[str] = None
    is_open: Optional[bool] = None
    author: Optional[str] = None
    at: Optional[str] = None
    entity: Optional[dict] = None


class HandleEntryCreated(Task):
    ACTION = "handle_entry_created"

    @classmethod
    def from_change(cls, change: Any) -> "HandleEntryCreated":
        row = change.new if isinstance(change.new, dict) else {}
        entity = EntityRef(post_id=str(change.resource_id), title=row.get("title"))
        payload = asdict(
            EntryCreatedPayload(
                title=row.get("title"),
                is_open=bool(row["is_open"]) if "is_open" in row else None,
                author=row.get("author"),
                at=change.at,
                entity=entity.to_dict(),
            )
        )
        return cls(post_id=change.resource_id, payload=payload)
