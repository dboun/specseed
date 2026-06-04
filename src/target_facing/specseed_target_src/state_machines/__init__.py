"""State-machine APIs for specseed entities."""

from src.target_facing.specseed_target_src.state_machines.base import (
    StateMachineResult,
    StateTransition,
    approval_ids_from_body,
    approved_by,
    current_state,
    evaluate_entity_state,
    possible_next_states,
)
from src.target_facing.specseed_target_src.state_machines.epic import (
    current_epic_state,
    epic_state_machine,
    possible_next_epic_states,
)
from src.target_facing.specseed_target_src.state_machines.issue import (
    current_issue_state,
    issue_state_machine,
    possible_next_issue_states,
)
from src.target_facing.specseed_target_src.state_machines.ticket import (
    current_ticket_state,
    possible_next_ticket_states,
    ticket_state_machine,
)

__all__ = [
    "StateMachineResult",
    "StateTransition",
    "approval_ids_from_body",
    "approved_by",
    "current_epic_state",
    "current_issue_state",
    "current_state",
    "current_ticket_state",
    "epic_state_machine",
    "evaluate_entity_state",
    "issue_state_machine",
    "possible_next_epic_states",
    "possible_next_issue_states",
    "possible_next_states",
    "possible_next_ticket_states",
    "ticket_state_machine",
]
