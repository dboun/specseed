"""State machine entry points for Epic entities."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from specseed_runtime.entities.epic import Epic
from specseed_runtime.state_machines.base import (
    StateMachineResult,
    current_state,
    evaluate_entity_state,
    possible_next_states,
)


def epic_state_machine(
    epic: Epic,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> StateMachineResult:
    return evaluate_entity_state(epic, config, conversation)


def current_epic_state(epic: Epic, config: Optional[dict[str, Any]] = None) -> str:
    return current_state(epic, config)


def possible_next_epic_states(
    epic: Epic,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> list[str]:
    return possible_next_states(epic, config, conversation)
