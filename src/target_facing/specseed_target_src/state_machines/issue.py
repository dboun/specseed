"""State machine entry points for Issue entities."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from src.target_facing.specseed_target_src.entities.issue import Issue
from src.target_facing.specseed_target_src.state_machines.base import (
    StateMachineResult,
    current_state,
    evaluate_entity_state,
    possible_next_states,
)


def issue_state_machine(
    issue: Issue,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> StateMachineResult:
    return evaluate_entity_state(issue, config, conversation)


def current_issue_state(issue: Issue, config: Optional[dict[str, Any]] = None) -> str:
    return current_state(issue, config)


def possible_next_issue_states(
    issue: Issue,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> list[str]:
    return possible_next_states(issue, config, conversation)
