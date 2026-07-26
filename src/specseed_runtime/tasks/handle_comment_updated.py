"""handle_comment_updated.py - an existing comment was edited.

Shares its ``resource_key`` (``("comment", comment_id)``) with
``HandleCommentAdded``, so editing a comment supersedes the not-yet-processed
"added" task for that same comment - the edited version is what gets handled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from specseed_runtime.tasks.task_base import Task


@dataclass
class CommentUpdatedPayload:
    comment_id: Any
    field: Optional[str] = None
    at: Optional[str] = None


class HandleCommentUpdated(Task):
    ACTION = "handle_comment_updated"

    @classmethod
    def from_change(cls, change: Any) -> "HandleCommentUpdated":
        payload = asdict(
            CommentUpdatedPayload(comment_id=change.resource_id, field=change.field, at=change.at)
        )
        return cls(post_id=change.parent_id, payload=payload)
