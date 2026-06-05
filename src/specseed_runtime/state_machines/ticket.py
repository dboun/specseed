"""State machine entry points for Ticket entities."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from specseed_runtime.entities.ticket import Ticket
from specseed_runtime.state_machines.base import (
    StateMachineResult,
    current_state,
    evaluate_entity_state,
    possible_next_states,
)


def ticket_state_machine(
    ticket: Ticket,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> StateMachineResult:
    return evaluate_entity_state(ticket, config, conversation)


def current_ticket_state(ticket: Ticket, config: Optional[dict[str, Any]] = None) -> str:
    return current_state(ticket, config)


def possible_next_ticket_states(
    ticket: Ticket,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> list[str]:
    return possible_next_states(ticket, config, conversation)
