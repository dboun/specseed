"""handle_entry_updated.py - an existing entry's title/body/assignees changed.

Per-field changes are coalesced into one task by sync_to_db; the handler is
expected to re-read the entry from the remote (the source of truth), so the
payload only needs to point at it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from task_base import Task


@dataclass
class EntryUpdatedPayload:
    at: Optional[str] = None


class HandleEntryUpdated(Task):
    ACTION = "handle_entry_updated"

    @classmethod
    def from_change(cls, change: Any) -> "HandleEntryUpdated":
        payload = asdict(EntryUpdatedPayload(at=change.at))
        return cls(post_id=change.resource_id, payload=payload)
