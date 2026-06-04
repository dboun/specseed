"""handle_comment_added.py - a new comment was posted on an entry."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


from specseed_target_src.tasks.task_base import Task


@dataclass
class CommentAddedPayload:
    comment_id: Any
    body: Optional[str] = None
    author: Optional[str] = None
    at: Optional[str] = None


class HandleCommentAdded(Task):
    ACTION = "handle_comment_added"

    @classmethod
    def from_change(cls, change: Any) -> "HandleCommentAdded":
        row = change.new if isinstance(change.new, dict) else {}
        payload = asdict(
            CommentAddedPayload(
                comment_id=change.resource_id,
                body=row.get("body"),
                author=row.get("author"),
                at=change.at,
            )
        )
        # post_id is the parent entry, so the task is swept on entry teardown.
        return cls(post_id=change.parent_id, payload=payload)
