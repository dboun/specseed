"""handle_entry_reaction_removed.py - a reaction was removed from an entry itself.

``post_id`` is the entry id (the entry reaction's change-stream parent), so the
work path can re-evaluate the entity's gate after the reaction goes away.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from specseed_target_src.tasks.task_base import Task


@dataclass
class EntryReactionRemovedPayload:
    reaction_id: Any
    at: Optional[str] = None


class HandleEntryReactionRemoved(Task):
    ACTION = "handle_entry_reaction_removed"

    @classmethod
    def from_change(cls, change: Any) -> "HandleEntryReactionRemoved":
        payload = asdict(EntryReactionRemovedPayload(reaction_id=change.resource_id, at=change.at))
        return cls(post_id=change.parent_id, payload=payload)
