"""handle_entry_reopened.py - a closed entry was reopened on the remote."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from src.target_facing.specseed_target_src.tasks.task_base import Task


@dataclass
class EntryReopenedPayload:
    at: Optional[str] = None


class HandleEntryReopened(Task):
    ACTION = "handle_entry_reopened"

    @classmethod
    def from_change(cls, change: Any) -> "HandleEntryReopened":
        payload = asdict(EntryReopenedPayload(at=change.at))
        return cls(post_id=change.resource_id, payload=payload)
