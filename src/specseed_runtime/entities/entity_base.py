"""
entity_base.py - the work-item model shared by epics, tickets and issues.

The tracking layer speaks in provider-neutral "entries". This layer gives those
entries *meaning* in the specseed work breakdown: an entry is an Epic, a Ticket
or an Issue (its ``tier``), it has a workflow ``status``, and it links to other
entries (a ticket's parent epic, an issue's parent ticket, dependencies).

Subclasses inherit from :class:`Entity` and only declare their tier and the
tiers they may link to, so the linking rules live in one place. A small registry
lets callers build the right subclass from an entry's labels.

``EntityRef`` is the lightweight, serializable slice of an entity that travels
inside task payloads (see ``../tasks``): just enough for a scheduler to reason
about tier/status without re-reading the remote.

Only Python stdlib is used.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import ClassVar, Optional


TIER_LABEL_PREFIX = "tier:"
STATUS_LABEL_PREFIX = "status:"

# Dependencies are written in the post body (remote-posts.md), e.g.
#   <!-- Ticket: #41   Depends on: #61, #62 -->
# or a plain "Depends on: #61" line. We extract the `#NN` ids after the phrase.
_DEPENDS_ON_RE = re.compile(r"depends\s+on\s*:?\s*(.+)", re.IGNORECASE)
_ID_TOKEN_RE = re.compile(r"#\s*([0-9]+|[A-Za-z]+-[0-9]+)")
# Parent link in a post body: an issue links its `Ticket: #NN`, a ticket its
# `Epic: #NN` (remote-posts.md). The immediate parent is the first of these.
_PARENT_RE = re.compile(r"\b(?:Ticket|Epic)\s*:\s*#\s*([0-9]+|[A-Za-z]+-[0-9]+)", re.IGNORECASE)


def parse_depends_on(body: Optional[str]) -> list[str]:
    """Extract dependency post ids from a post body.

    Reads every ``Depends on: ...`` segment (HTML comment or plain line) and pulls
    the ``#NN`` / ``#FEAT-0001`` ids after it, up to the end of that segment
    (newline or the closing ``-->``). Returns ids without the ``#``, de-duped,
    order preserved. Empty when there are none.
    """
    if not body:
        return []
    ids: list[str] = []
    for m in _DEPENDS_ON_RE.finditer(body):
        segment = m.group(1)
        # stop at the comment close or end of line - don't bleed into later text
        for stop in ("-->", "\n"):
            idx = segment.find(stop)
            if idx != -1:
                segment = segment[:idx]
        for tok in _ID_TOKEN_RE.findall(segment):
            tok = tok.strip()
            if tok and tok not in ids:
                ids.append(tok)
    return ids


def parse_parent(body: Optional[str]) -> Optional[str]:
    """Immediate parent id from a post body's link (``Ticket: #NN`` for an issue,
    ``Epic: #NN`` for a ticket). Returns the id without ``#``, or None."""
    if not body:
        return None
    m = _PARENT_RE.search(body)
    return m.group(1).strip() if m else None

# The canonical work vocabulary actually seeded on the tracker (see
# tracking/supported_values.py, tracking/populate_defaults.py and
# references/remote-posts.md): a bare tier label (`epic`/`ticket`/`issue`) plus a
# `<tier>:status:<status>` status label. The `tier:`/`status:` prefixed forms are
# also accepted for callers/tests that use them.
_WORK_TIERS = ("epic", "ticket", "issue")

# tier -> Entity subclass, populated by Entity.register (called in epic/ticket/issue).
_TIER_REGISTRY: dict[str, type["Entity"]] = {}


@dataclass(frozen=True)
class EntityRef:
    """Serializable entity context carried inside task payloads."""

    post_id: str
    tier: Optional[str] = None
    status: Optional[str] = None
    title: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Entity:
    """Base work item. Subclasses set ``TIER``/``PARENT_TIER``/``CHILD_TIER``."""

    # Class-level taxonomy (overridden by subclasses; not __init__ fields).
    TIER: ClassVar[Optional[str]] = None
    PARENT_TIER: ClassVar[Optional[str]] = None
    CHILD_TIER: ClassVar[Optional[str]] = None

    post_id: str = ""
    title: Optional[str] = None
    labels: list[str] = field(default_factory=list)
    parent_id: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)

    # -- taxonomy helpers ------------------------------------------------ #
    @property
    def tier(self) -> Optional[str]:
        return type(self).TIER or self.tier_from_labels(self.labels)

    @property
    def status(self) -> Optional[str]:
        return self.status_from_labels(self.labels)

    @staticmethod
    def tier_from_labels(labels: list[str]) -> Optional[str]:
        # Prefer the explicit `tier:<tier>` form, then fall back to a bare
        # `epic`/`ticket`/`issue` label (the form actually seeded on the tracker).
        for label in labels:
            if label.startswith(TIER_LABEL_PREFIX):
                return label[len(TIER_LABEL_PREFIX):]
        for label in labels:
            if label in _WORK_TIERS:
                return label
        return None

    @staticmethod
    def status_from_labels(labels: list[str]) -> Optional[str]:
        # Accept both the bare `status:<status>` form and the canonical
        # `<tier>:status:<status>` form seeded on the tracker.
        marker = ":" + STATUS_LABEL_PREFIX  # ":status:"
        for label in labels:
            if label.startswith(STATUS_LABEL_PREFIX):
                return label[len(STATUS_LABEL_PREFIX):]
            idx = label.find(marker)
            if idx != -1:
                return label[idx + len(marker):]
        return None

    # -- linking --------------------------------------------------------- #
    def can_contain(self, other: "Entity") -> bool:
        """Whether ``other`` may be a direct child of this entity."""
        return type(self).CHILD_TIER is not None and type(other).TIER == type(self).CHILD_TIER

    def is_child_of(self, other: "Entity") -> bool:
        return other.can_contain(self)

    def ref(self) -> EntityRef:
        return EntityRef(post_id=str(self.post_id), tier=self.tier, status=self.status, title=self.title)

    # -- registry / factory --------------------------------------------- #
    @classmethod
    def register(cls, subclass: type["Entity"]) -> type["Entity"]:
        if subclass.TIER is not None:
            _TIER_REGISTRY[subclass.TIER] = subclass
        return subclass

    @classmethod
    def for_labels(
        cls,
        post_id: str | int,
        labels: Optional[list[str]] = None,
        title: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> "Entity":
        """Build the most specific Entity subclass implied by ``labels``."""
        labels = list(labels or [])
        tier = cls.tier_from_labels(labels)
        target = _TIER_REGISTRY.get(tier, cls)
        return target(post_id=str(post_id), title=title, labels=labels, parent_id=parent_id)
