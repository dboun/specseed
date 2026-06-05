"""handle_reaction_added.py - a reaction was added to a comment (e.g. an approval).

Note: a reaction's parent in the change stream is the *comment*, not the entry,
so this task's ``post_id`` is the comment id. Sweeping reactions on entry
teardown is therefore best-effort and left as a follow-up.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from specseed_target_src.tasks.task_base import Task


@dataclass
class ReactionAddedPayload:
    reaction_id: Any
    kind: Optional[str] = None
    user: Optional[str] = None
    at: Optional[str] = None


class HandleReactionAdded(Task):
    ACTION = "handle_reaction_added"

    @classmethod
    def from_change(cls, change: Any) -> "HandleReactionAdded":
        row = change.new if isinstance(change.new, dict) else {}
        payload = asdict(
            ReactionAddedPayload(
                reaction_id=change.resource_id,
                kind=row.get("kind"),
                user=row.get("user"),
                at=change.at,
            )
        )
        return cls(post_id=change.parent_id, payload=payload)
