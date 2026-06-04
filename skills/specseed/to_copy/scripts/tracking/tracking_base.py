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
from dataclasses import dataclass, field
from typing import Any, Optional


REACTION_EYES = "eyes"
REACTION_HEART = "heart"
REACTION_THUMBS_UP = "thumbs_up"
REACTION_THUMBS_DOWN = "thumbs_down"

SUPPORTED_REACTIONS = frozenset(
    {
        REACTION_EYES,
        REACTION_HEART,
        REACTION_THUMBS_UP,
        REACTION_THUMBS_DOWN,
    }
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
class TrackingLabel:
    """Provider-neutral label metadata."""

    name: str
    color: Optional[str] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class TrackingReaction:
    """Reaction summary attached to an entry comment.

    kind must be one of SUPPORTED_REACTIONS. Implementations should map provider
    names onto these names, for example GitHub '+1' -> 'thumbs_up' and GitLab
    'thumbsup' -> 'thumbs_up'.
    """

    kind: str
    count: int = 0
    users: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingEntryComment:
    """Full normalized comment data for an entry."""

    id: int | str
    body: str
    author: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    reactions: list[TrackingReaction] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingEntrySummary:
    """Small entry shape returned by list_entries(...)."""

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
class TrackingEntryDetails(TrackingEntrySummary):
    """Full entry shape returned by get_entry(...)."""

    body: Optional[str] = None
    comments: list[TrackingEntryComment] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingEntryId:
    """Payload for operations that create an entry."""

    id: int | str


@dataclass(frozen=True)
class TrackingCommentId:
    """Payload for operations that create a comment."""

    id: int | str


@dataclass(frozen=True)
class TrackingEntryOpenState:
    """Payload for entry open/closed queries and mutations."""

    id: int | str
    is_open: bool


@dataclass(frozen=True)
class TrackingPinState:
    """Payload for entry pinning operations."""

    id: int | str
    pinned: bool


@dataclass(frozen=True)
class TrackingLabelSet:
    """Payload for operations that return the labels on an entry."""

    entry_id: int | str
    labels: list[TrackingLabel] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingLabelList:
    """Payload for operations that return repository/project labels."""

    labels: list[TrackingLabel] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingReactionResult:
    """Payload for adding a reaction to an entry comment."""

    entry_id: int | str
    comment_id: int | str
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
        reactions should be normalized into TrackingReaction objects using only
        SUPPORTED_REACTIONS. Provider-specific payloads must not be returned.

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
