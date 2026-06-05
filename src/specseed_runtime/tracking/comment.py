"""comment.py - normalized comments and reactions for tracking providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TrackingReaction:
    """Reaction summary attached to a comment."""

    kind: str
    count: int = 0
    users: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingComment:
    """Full normalized comment data for a post or pull request."""

    id: int | str
    body: str
    post_id: Optional[int | str] = None
    pull_request_id: Optional[int | str] = None
    author: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    reactions: list[TrackingReaction] = field(default_factory=list)


TrackingEntryComment = TrackingComment
