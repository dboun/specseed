"""
issue.py - the leaf tier of the work breakdown.

An issue lives under a ticket and is the execution unit the agent actually
works. It has no children.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from specseed_target_src.entities.entity_base import Entity


@Entity.register
class Issue(Entity):
    TIER: ClassVar[str] = "issue"
    PARENT_TIER: ClassVar[Optional[str]] = "ticket"
    CHILD_TIER: ClassVar[Optional[str]] = None
