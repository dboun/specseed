"""pull_request.py - normalized pull request model for tracking providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.target_facing.specseed_target_src.tracking.comment import TrackingComment
from src.target_facing.specseed_target_src.tracking.post import TrackingLabel


@dataclass(frozen=True)
class TrackingPullRequestSummary:
    """Small pull request shape returned by list_pull_requests."""

    id: int | str
    title: str
    source_branch: str
    target_branch: str
    labels: list[TrackingLabel] = field(default_factory=list)
    is_open: bool = True
    is_merged: bool = False
    url: Optional[str] = None
    author: Optional[str] = None
    assignees: list[str] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class TrackingPullRequestDetails(TrackingPullRequestSummary):
    """Full pull request shape returned by get_pull_request."""

    body: Optional[str] = None
    comments: list[TrackingComment] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingPullRequestId:
    """Payload for operations that create a pull request."""

    id: int | str


@dataclass(frozen=True)
class TrackingPullRequestOpenState:
    """Payload for pull request open/closed queries and mutations."""

    id: int | str
    is_open: bool
    is_merged: bool = False
