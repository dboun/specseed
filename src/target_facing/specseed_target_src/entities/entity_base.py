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

from dataclasses import asdict, dataclass, field
from typing import ClassVar, Optional


TIER_LABEL_PREFIX = "tier:"
STATUS_LABEL_PREFIX = "status:"

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
        for label in labels:
            if label.startswith(TIER_LABEL_PREFIX):
                return label[len(TIER_LABEL_PREFIX):]
        return None

    @staticmethod
    def status_from_labels(labels: list[str]) -> Optional[str]:
        for label in labels:
            if label.startswith(STATUS_LABEL_PREFIX):
                return label[len(STATUS_LABEL_PREFIX):]
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
