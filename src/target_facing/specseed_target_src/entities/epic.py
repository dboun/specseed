"""
epic.py - the top tier of the work breakdown.

An epic groups tickets. It has no parent and contains tickets.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from specseed_target_src.entities.entity_base import Entity


@Entity.register
class Epic(Entity):
    TIER: ClassVar[str] = "epic"
    PARENT_TIER: ClassVar[Optional[str]] = None
    CHILD_TIER: ClassVar[Optional[str]] = "ticket"
