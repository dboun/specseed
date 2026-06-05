"""
tracking_base.py - provider-neutral contract for remote entry trackers.

This module defines the shared issue-like interface that GitHub and GitLab
implementations should inherit from. It deliberately avoids provider vocabulary
such as "issue", "note", or "post"; the normalized resource name is "entry".

Only Python stdlib is used. Return values are dataclasses with simple primitive
fields so callers can convert them with dataclasses.asdict(...) when JSON output
is needed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from specseed_target_src.tracking.comment import TrackingComment, TrackingEntryComment, TrackingReaction
from specseed_target_src.tracking.post import (
    TrackingEntryDetails,
    TrackingEntryId,
    TrackingEntryOpenState,
    TrackingEntrySummary,
    TrackingLabel,
    TrackingLabelList,
    TrackingLabelSet,
    TrackingPinState,
    TrackingPostDetails,
    TrackingPostId,
    TrackingPostOpenState,
    TrackingPostSummary,
)
from specseed_target_src.tracking.pull_request import (
    TrackingPullRequestDetails,
    TrackingPullRequestId,
    TrackingPullRequestOpenState,
    TrackingPullRequestSummary,
)
from specseed_target_src.tracking.supported_values import (
    REACTION_EYES,
    REACTION_HEART,
    REACTION_THUMBS_DOWN,
    REACTION_THUMBS_UP,
    SUPPORTED_REACTIONS,
)


@dataclass(frozen=True)
class TrackingResult:
    """Common return envelope for every remote operation.

    ok:
        True when the provider operation completed successfully.
    error:
        None on success, otherwise a concise human-readable error string.
    data:
        Operation-specific payload. Keep payloads dataclass/list/dict/scalar
        shaped so dataclasses.asdict(result) produces JSON-compatible data.
    """

    ok: bool
    error: Optional[str] = None
    data: Optional[Any] = None


@dataclass(frozen=True)
class TrackingCommentId:
    """Payload for operations that create a comment."""

    id: int | str


@dataclass(frozen=True)
class TrackingReactionResult:
    """Payload for adding a reaction to an entry comment."""

    entry_id: int | str
    comment_id: int | str
    reaction: str


@dataclass(frozen=True)
class TrackingEntryReactionResult:
    """Payload for adding a reaction to an entry itself (not one of its comments)."""

    entry_id: int | str
    reaction: str


@dataclass(frozen=True)
class TrackingSyncChange:
    """One mutation applied while syncing from another remote.

    Changes are returned in the same order they were applied. The resource
    hierarchy is provider-neutral: label -> entry -> entry_label/pin -> comment
    -> reaction, with deletions applied from leaf resources upward.
    """

    action: str
    resource_type: str
    resource_id: int | str
    parent_id: Optional[int | str] = None
    field: Optional[str] = None
    old: Optional[Any] = None
    new: Optional[Any] = None
    at: Optional[str] = None


class TrackingBase(ABC):
    """Abstract interface for GitHub/GitLab entry implementations.

    Implementations should catch provider-specific exceptions and return
    TrackingResult(ok=False, error=..., data=None) instead of leaking raw API
    exceptions through this interface.
    """

    @staticmethod
    def is_supported_reaction(reaction: str) -> bool:
        """Return whether reaction is one of the four normalized reactions."""
        return reaction in SUPPORTED_REACTIONS

    def list_posts(
        self,
        is_open: Optional[bool] = None,
        labels: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> TrackingResult:
        return self.list_entries(is_open, labels, assignee, updated_since)

    def get_post(self, post_id: int | str) -> TrackingResult:
        return self.get_entry(post_id)

    def add_post(
        self,
        title: str,
        body: Optional[str] = None,
        labels: Optional[list[str]] = None,
        assignees: Optional[list[str]] = None,
    ) -> TrackingResult:
        return self.add_entry(title, body, labels, assignees)

    def add_post_comment(self, post_id: int | str, body: str) -> TrackingResult:
        return self.add_entry_comment(post_id, body)

    def add_post_comment_reaction(
        self,
        post_id: int | str,
        comment_id: int | str,
        reaction: str,
    ) -> TrackingResult:
        return self.add_entry_comment_reaction(post_id, comment_id, reaction)

    def add_post_reaction(self, post_id: int | str, reaction: str) -> TrackingResult:
        return self.add_entry_reaction(post_id, reaction)

    def get_post_labels(self, post_id: int | str) -> TrackingResult:
        return self.get_entry_labels(post_id)

    def add_post_label(self, post_id: int | str, label: str) -> TrackingResult:
        return self.add_entry_label(post_id, label)

    @abstractmethod
    def list_entries(
        self,
        is_open: Optional[bool] = None,
        labels: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> TrackingResult:
        """Return entry summaries for the configured remote repository/project.

        Implementations should:
        - map provider issue IDs to TrackingEntrySummary.id;
        - normalize labels into TrackingLabel objects;
        - set TrackingEntrySummary.is_open from the provider open/closed state;
        - include author, assignees, url, created_at, and updated_at when the
          provider makes them available;
        - apply filters when the provider supports them, or fetch then filter
          locally when that is reasonable.

        Expected success payload:
            list[TrackingEntrySummary]
        """

    @abstractmethod
    def get_entry(self, entry_id: int | str) -> TrackingResult:
        """Return full normalized data for one entry.

        Implementations should fetch the entry body plus all comments. Comment
        reactions AND entry-level reactions should be normalized into
        TrackingReaction objects using only SUPPORTED_REACTIONS; entry reactions
        go on ``TrackingEntryDetails.reactions``. Provider-specific payloads must
        not be returned.

        Expected success payload:
            TrackingEntryDetails
        """

    @abstractmethod
    def is_entry_open(self, entry_id: int | str) -> TrackingResult:
        """Return whether an entry is currently open.

        This intentionally avoids a generic status field because callers may use
        "status" for specseed workflow state.

        Expected success payload:
            TrackingEntryOpenState
        """

    @abstractmethod
    def set_entry_open(self, entry_id: int | str) -> TrackingResult:
        """Open or reopen an entry.

        Implementations should be idempotent when the provider allows it: an
        already-open entry should still return ok=True.

        Expected success payload:
            TrackingEntryOpenState with is_open=True
        """

    @abstractmethod
    def set_entry_closed(self, entry_id: int | str) -> TrackingResult:
        """Close an entry.

        Implementations should be idempotent when the provider allows it: an
        already-closed entry should still return ok=True.

        Expected success payload:
            TrackingEntryOpenState with is_open=False
        """

    @abstractmethod
    def pin_entry(self, entry_id: int | str) -> TrackingResult:
        """Pin an entry in the provider UI.

        Implementations should treat "already pinned" as success when the
        provider exposes that case.

        Expected success payload:
            TrackingPinState with pinned=True
        """

    @abstractmethod
    def delete_entry(self, entry_id: int | str) -> TrackingResult:
        """Permanently delete an entry and its child resources.

        Deletion is NOT uniformly supported across providers. Local stores and
        GitLab can hard-delete; plain GitHub REST cannot delete an issue, so the
        GitHub implementation returns ok=False pointing at set_entry_closed(...).
        Callers that must work everywhere should prefer closing over deleting.

        Expected success payload:
            TrackingEntryId
        """

    @abstractmethod
    def edit_entry(
        self,
        entry_id: int | str,
        title: Optional[str] = None,
        body: Optional[str] = None,
    ) -> TrackingResult:
        """Edit an entry's title and/or body.

        Only the provided fields change; passing None leaves a field untouched.
        This is the seam dashboards use to rewrite their bodies (ROADMAP,
        SCHEDULE, the sprint board).

        Expected success payload:
            TrackingEntryId
        """

    @abstractmethod
    def add_entry(
        self,
        title: str,
        body: Optional[str] = None,
        labels: Optional[list[str]] = None,
        assignees: Optional[list[str]] = None,
    ) -> TrackingResult:
        """Create a new entry and return its provider ID.

        Implementations should create any requested labels first if the provider
        requires labels to exist before attaching them.

        Expected success payload:
            TrackingEntryId
        """

    @abstractmethod
    def add_entry_comment(self, entry_id: int | str, body: str) -> TrackingResult:
        """Add a text comment to an entry and return the comment ID.

        File uploads/attachments are intentionally outside this interface.

        Expected success payload:
            TrackingCommentId
        """

    @abstractmethod
    def add_entry_comment_reaction(
        self,
        entry_id: int | str,
        comment_id: int | str,
        reaction: str,
    ) -> TrackingResult:
        """Add a normalized reaction to an entry comment.

        reaction must be one of:
            eyes, heart, thumbs_up, thumbs_down

        Implementations should map the normalized reaction to the provider's
        spelling before calling the API. If reaction is unsupported, return
        ok=False with an error string.

        Expected success payload:
            TrackingReactionResult
        """

    @abstractmethod
    def add_entry_reaction(self, entry_id: int | str, reaction: str) -> TrackingResult:
        """Add a normalized reaction to an entry itself (not to a comment).

        reaction must be one of:
            eyes, heart, thumbs_up, thumbs_down

        Both GitHub (``POST /issues/:n/reactions``) and GitLab (issue award
        emoji) support entry reactions. Implementations should map the normalized
        reaction to the provider spelling, and return ok=False on an unsupported
        reaction. This is the seam the approval system reads a 👍 from.

        Expected success payload:
            TrackingEntryReactionResult
        """

    @abstractmethod
    def get_entry_labels(self, entry_id: int | str) -> TrackingResult:
        """Return the current labels attached to an entry.

        Expected success payload:
            TrackingLabelSet
        """

    @abstractmethod
    def add_entry_label(self, entry_id: int | str, label: str) -> TrackingResult:
        """Attach label to an entry, creating the label first if missing.

        Implementations should not duplicate labels. If the entry already has
        the label, return ok=True and the current label set.

        Expected success payload:
            TrackingLabelSet
        """

    @abstractmethod
    def remove_entry_label(self, entry_id: int | str, label: str) -> TrackingResult:
        """Detach label from an entry.

        Implementations should be idempotent: removing a label the entry does
        not carry returns ok=True with the unchanged label set. This is how a
        status swap works (remove status:todo, add status:in_progress).

        Expected success payload:
            TrackingLabelSet
        """

    @abstractmethod
    def list_labels(self) -> TrackingResult:
        """Return all labels available in the remote repository/project.

        Expected success payload:
            TrackingLabelList
        """

    @abstractmethod
    def create_label(
        self,
        name: str,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> TrackingResult:
        """Create a repository/project label.

        Implementations should normalize provider color requirements. For
        example, GitHub accepts six hex digits while GitLab usually expects a
        leading '#'. If the label already exists, prefer ok=True and return the
        existing label unless the provider makes that impractical.

        Expected success payload:
            TrackingLabel
        """

    @abstractmethod
    def ensure_label(
        self,
        name: str,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> TrackingResult:
        """Return an existing label or create it if it is missing.

        This is the explicit repository/project label helper used by
        add_entry_label(...) before attaching a label to an entry.

        Expected success payload:
            TrackingLabel
        """

    @abstractmethod
    def sync_from_remote(self, remote: Any) -> TrackingResult:
        """Sync changes from another remote into this remote.

        Implementations should use timestamps and provider filters when
        available so unchanged child resources do not need to be fetched.
        Expected success payload:
            list[TrackingSyncChange]
        """

    @abstractmethod
    def list_pull_requests(
        self,
        is_open: Optional[bool] = None,
        labels: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> TrackingResult:
        """Return pull request summaries for the configured provider."""

    @abstractmethod
    def get_pull_request(self, pull_request_id: int | str) -> TrackingResult:
        """Return one pull request with body and comments."""

    @abstractmethod
    def add_pull_request(
        self,
        title: str,
        source_branch: str,
        target_branch: str,
        body: Optional[str] = None,
        labels: Optional[list[str]] = None,
        assignees: Optional[list[str]] = None,
    ) -> TrackingResult:
        """Create a pull request and return its provider ID."""

    @abstractmethod
    def set_pull_request_closed(self, pull_request_id: int | str) -> TrackingResult:
        """Close a pull request without merging it."""
