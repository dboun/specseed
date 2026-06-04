"""handle_entry_reaction_added.py - a reaction was added to an entry itself.

Unlike a *comment* reaction (whose change-stream parent is the comment), an entry
reaction's parent IS the entry, so this task's ``post_id`` is the entry id. That
lets the work path load the entity and resolve an approval: a 👍 on an
``awaiting_approval`` post from an approver clears the gate (see
``executing/advance.resolve_approval`` + ``state_machines/base.approved_by``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from specseed_target_src.tasks.task_base import Task


@dataclass
class EntryReactionAddedPayload:
    reaction_id: Any
    kind: Optional[str] = None
    user: Optional[str] = None
    at: Optional[str] = None


class HandleEntryReactionAdded(Task):
    ACTION = "handle_entry_reaction_added"

    @classmethod
    def from_change(cls, change: Any) -> "HandleEntryReactionAdded":
        row = change.new if isinstance(change.new, dict) else {}
        payload = asdict(
            EntryReactionAddedPayload(
                reaction_id=change.resource_id,
                kind=row.get("kind"),
                user=row.get("user"),
                at=change.at,
            )
        )
        return cls(post_id=change.parent_id, payload=payload)
