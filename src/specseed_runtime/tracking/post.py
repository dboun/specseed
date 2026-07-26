"""post.py - normalized issue-like posts for tracking providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from specseed_runtime.tracking.comment import TrackingComment, TrackingReaction


@dataclass(frozen=True)
class TrackingLabel:
    """Provider-neutral label metadata."""

    name: str
    color: Optional[str] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class TrackingPostSummary:
    """Small post shape returned by list_posts/list_entries."""

    id: int | str
    title: str
    labels: list[TrackingLabel] = field(default_factory=list)
    is_open: bool = True
    url: Optional[str] = None
    author: Optional[str] = None
    assignees: list[str] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class TrackingPostDetails(TrackingPostSummary):
    """Full post shape returned by get_post/get_entry."""

    body: Optional[str] = None
    comments: list[TrackingComment] = field(default_factory=list)
    # Reactions attached to the post ENTRY itself (not to a comment). Both GitHub
    # (issue reactions) and GitLab (issue award emoji) support these. A 👍 here from
    # an approver is one way the approval system clears an awaiting_approval gate.
    reactions: list[TrackingReaction] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingPostId:
    """Payload for operations that create a post."""

    id: int | str


@dataclass(frozen=True)
class TrackingPostOpenState:
    """Payload for post open/closed queries and mutations."""

    id: int | str
    is_open: bool


@dataclass(frozen=True)
class TrackingPinState:
    """Payload for post pinning operations."""

    id: int | str
    pinned: bool


@dataclass(frozen=True)
class TrackingLabelSet:
    """Payload for operations that return labels on a post."""

    entry_id: int | str
    labels: list[TrackingLabel] = field(default_factory=list)

    @property
    def post_id(self) -> int | str:
        return self.entry_id


@dataclass(frozen=True)
class TrackingLabelList:
    """Payload for operations that return repository/project labels."""

    labels: list[TrackingLabel] = field(default_factory=list)


TrackingEntrySummary = TrackingPostSummary
TrackingEntryDetails = TrackingPostDetails
TrackingEntryId = TrackingPostId
TrackingEntryOpenState = TrackingPostOpenState
