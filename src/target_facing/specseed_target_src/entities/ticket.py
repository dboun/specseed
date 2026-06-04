"""
ticket.py - the middle tier of the work breakdown.

A ticket lives under an epic and contains issues. Tickets carry roadmap
visibility, sprint membership and priority; issues under them are the execution
units.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from src.target_facing.specseed_target_src.entities.entity_base import Entity


@Entity.register
class Ticket(Entity):
    TIER: ClassVar[str] = "ticket"
    PARENT_TIER: ClassVar[Optional[str]] = "epic"
    CHILD_TIER: ClassVar[Optional[str]] = "issue"
