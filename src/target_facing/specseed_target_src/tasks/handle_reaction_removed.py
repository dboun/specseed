"""handle_reaction_removed.py - a reaction was removed from a comment."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from specseed_target_src.tasks.task_base import Task


@dataclass
class ReactionRemovedPayload:
    reaction_id: Any
    at: Optional[str] = None


class HandleReactionRemoved(Task):
    ACTION = "handle_reaction_removed"

    @classmethod
    def from_change(cls, change: Any) -> "HandleReactionRemoved":
        payload = asdict(ReactionRemovedPayload(reaction_id=change.resource_id, at=change.at))
        return cls(post_id=change.parent_id, payload=payload)
