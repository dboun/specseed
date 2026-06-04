"""Shared state-machine helpers for specseed work entities.

Callers pass an Entity instance plus configuration and receive the entity's
current state with the next states the runner may legally move to. Conversation
comments are optional but, when present, approval commands are resolved across
the whole post conversation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Iterable, Optional

from specseed_target_src.entities.entity_base import Entity
from specseed_target_src.state_machines.approvals import requested_apr_ids


DEFAULT_STATE = "todo"
TERMINAL_STATES = {"done", "wont_do", "deprecated"}
APPROVAL_COMMAND_RE = re.compile(r"^\s*approve\b(?P<ids>.*)$", re.IGNORECASE | re.DOTALL)
REJECT_COMMAND_RE = re.compile(r"^\s*reject\b(?P<ids>.*)$", re.IGNORECASE | re.DOTALL)
ID_SPLIT_RE = re.compile(r"[\s,]+")

# Reaction kinds the approval system reads off a post: 👍 approves, 👎 rejects.
APPROVE_REACTION = "thumbs_up"
REJECT_REACTION = "thumbs_down"
# Author names that are the bot itself, never a human approver. The local/remote
# sqlite stand-ins author as these; real providers add the configured bot login.
_DEFAULT_BOT_NAMES = {"local", "remote"}


@dataclass(frozen=True)
class StateTransition:
    """One possible state change for an entity."""

    to_state: str
    actor: str
    reason: str
    requires_approval: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StateMachineResult:
    """State-machine evaluation result."""

    entity_id: str
    tier: str
    current_state: str
    next_states: list[str]
    transitions: list[StateTransition] = field(default_factory=list)
    approved_by: list[str] = field(default_factory=list)
    rejected_by: list[str] = field(default_factory=list)
    approval_ids: list[str] = field(default_factory=list)
    review_required: bool = False
    manual_testing_required: bool = False
    hitl_required: bool = False

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["transitions"] = [transition.to_dict() for transition in self.transitions]
        return data


def approval_ids_from_body(body: Optional[str]) -> list[str]:
    """Parse a strict single approval post: ``approve <id> [<id> ...]``.

    The verb is case-insensitive. IDs may be separated with spaces, commas, or
    both. Text that does not begin with ``approve`` is ignored.
    """

    if not body:
        return []
    match = APPROVAL_COMMAND_RE.match(body.strip())
    if match is None:
        return []
    tail = match.group("ids").strip()
    if not tail:
        return []
    return [part for part in ID_SPLIT_RE.split(tail) if part]


def reject_ids_from_body(body: Optional[str]) -> list[str]:
    """Parse a strict single rejection post: ``reject <id> [<id> ...]``.

    Mirror of :func:`approval_ids_from_body` for the negative path.
    """

    if not body:
        return []
    match = REJECT_COMMAND_RE.match(body.strip())
    if match is None:
        return []
    tail = match.group("ids").strip()
    if not tail:
        return []
    return [part for part in ID_SPLIT_RE.split(tail) if part]


def approver_usernames(config: dict[str, Any]) -> set[str]:
    """Return configured usernames allowed to approve remote gates."""

    candidates = []
    approvals = config.get("approvals")
    if isinstance(approvals, dict):
        candidates.extend(approvals.get("approver_usernames") or [])
    candidates.extend(config.get("approver_usernames") or [])
    permissions = config.get("permissions")
    if isinstance(permissions, dict):
        nested = permissions.get("approvals")
        if isinstance(nested, dict):
            candidates.extend(nested.get("approver_usernames") or [])
    return {str(name).casefold() for name in candidates if str(name).strip()}


def bot_usernames(config: dict[str, Any]) -> set[str]:
    """Author names that are the bot, never a valid human approver."""

    names = set(_DEFAULT_BOT_NAMES)
    candidates: list[Any] = []
    for key in ("bot_usernames", "bot_username"):
        value = config.get(key)
        if isinstance(value, (list, tuple, set)):
            candidates.extend(value)
        elif value:
            candidates.append(value)
    approvals = config.get("approvals")
    if isinstance(approvals, dict):
        for key in ("bot_usernames", "bot_username"):
            value = approvals.get(key)
            if isinstance(value, (list, tuple, set)):
                candidates.extend(value)
            elif value:
                candidates.append(value)
    names.update(str(name).casefold() for name in candidates if str(name).strip())
    return names


def _allow_any_approver(config: dict[str, Any]) -> bool:
    """Whether any non-bot human may approve when no approvers are configured.

    Defaults to True: a solo/local project configures no approver list, so
    requiring one would deadlock every gate. Set ``approvals.allow_any_approver``
    false to require an explicit approver list instead.
    """
    approvals = config.get("approvals")
    if isinstance(approvals, dict) and "allow_any_approver" in approvals:
        return bool(approvals.get("allow_any_approver"))
    if "allow_any_approver" in config:
        return bool(config.get("allow_any_approver"))
    return True


def _approver_predicate(config: dict[str, Any]):
    """Return ``is_approver(author) -> bool`` for this config.

    With an explicit approver list, only those names approve. With no list and
    ``allow_any_approver`` (the default), any author that is not the bot approves.
    Otherwise nobody can (a deliberate, configured deadlock).
    """
    allowed = approver_usernames(config)
    if allowed:
        return lambda author: str(author).casefold() in allowed
    if _allow_any_approver(config):
        bots = bot_usernames(config)
        return lambda author: bool(str(author).strip()) and str(author).casefold() not in bots
    return lambda author: False


def approval_target_ids(entity: Entity) -> set[str]:
    """IDs that may be used in an ``approve`` command for this entity."""

    ids = {str(entity.post_id)}
    for attr in ("approval_id", "approval_ids", "pending_approval_ids"):
        value = getattr(entity, attr, None)
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            ids.update(str(item) for item in value)
        else:
            ids.add(str(value))
    return {item.casefold() for item in ids if item}


def _entity_reaction_users(entity: Entity, kind: str) -> list[str]:
    """Usernames who reacted ``kind`` to the entity itself (not to a comment)."""
    users: list[str] = []
    for reaction in getattr(entity, "reactions", None) or []:
        if _field(reaction, "kind") != kind:
            continue
        for user in _field(reaction, "users") or []:
            if user:
                users.append(str(user))
    return users


def approved_by(
    entity: Entity,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> list[str]:
    """Find approvers who approved this entity.

    Two equivalent signals count, both from an allowed approver (see
    :func:`_approver_predicate`):

    * a comment ``approve <id>`` whose id matches the entity (its post id, a
      pending approval attr, or a live ``APR-NNNN`` requested in the
      conversation), or
    * a 👍 (``thumbs_up``) reaction on the post itself.
    """

    is_approver = _approver_predicate(config)
    targets = approval_target_ids(entity)
    targets.update(item_id.casefold() for item_id in requested_apr_ids(conversation))
    authors = []
    for item in _conversation_items(entity, conversation):
        body = _field(item, "body")
        author = _field(item, "author")
        if not body or not author or not is_approver(author):
            continue
        ids = {item_id.casefold() for item_id in approval_ids_from_body(str(body))}
        if targets.intersection(ids):
            authors.append(str(author))
    for user in _entity_reaction_users(entity, APPROVE_REACTION):
        if is_approver(user):
            authors.append(user)
    return _dedupe(authors)


def rejected_by(
    entity: Entity,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> list[str]:
    """Find approvers who rejected this entity (``reject <id>`` or 👎)."""

    is_approver = _approver_predicate(config)
    targets = approval_target_ids(entity)
    targets.update(item_id.casefold() for item_id in requested_apr_ids(conversation))
    authors = []
    for item in _conversation_items(entity, conversation):
        body = _field(item, "body")
        author = _field(item, "author")
        if not body or not author or not is_approver(author):
            continue
        ids = {item_id.casefold() for item_id in reject_ids_from_body(str(body))}
        if targets.intersection(ids):
            authors.append(str(author))
    for user in _entity_reaction_users(entity, REJECT_REACTION):
        if is_approver(user):
            authors.append(user)
    return _dedupe(authors)


def evaluate_entity_state(
    entity: Entity,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> StateMachineResult:
    """Evaluate an entity's current state and possible next states."""

    state = entity.status or DEFAULT_STATE
    tier = entity.tier or "entity"
    approvals = approved_by(entity, config, conversation)
    rejections = rejected_by(entity, config, conversation)
    review_required = _review_required(entity, config)
    manual_required = _manual_testing_required(entity, config)
    hitl_required = _hitl_required(entity, config)

    transitions = _transitions_for(
        tier=tier,
        state=state,
        approved=bool(approvals),
        review_required=review_required,
        manual_required=manual_required,
        hitl_required=hitl_required,
        config=config,
    )
    return StateMachineResult(
        entity_id=str(entity.post_id),
        tier=tier,
        current_state=state,
        next_states=_dedupe([transition.to_state for transition in transitions]),
        transitions=transitions,
        approved_by=approvals,
        rejected_by=rejections,
        approval_ids=sorted(approval_target_ids(entity)),
        review_required=review_required,
        manual_testing_required=manual_required,
        hitl_required=hitl_required,
    )


def possible_next_states(
    entity: Entity,
    config: dict[str, Any],
    conversation: Optional[Iterable[Any]] = None,
) -> list[str]:
    return evaluate_entity_state(entity, config, conversation).next_states


def current_state(entity: Entity, config: Optional[dict[str, Any]] = None) -> str:
    return entity.status or DEFAULT_STATE


def _transitions_for(
    *,
    tier: str,
    state: str,
    approved: bool,
    review_required: bool,
    manual_required: bool,
    hitl_required: bool,
    config: dict[str, Any],
) -> list[StateTransition]:
    if state in TERMINAL_STATES:
        return []
    if state == "blocked":
        return [
            StateTransition("todo", "human", "blocker cleared or approval released"),
            StateTransition("wont_do", "human", "work intentionally cancelled"),
        ]
    if state == "awaiting_approval":
        if approved:
            return [
                StateTransition("todo", "human", "approved action gate; runner may resume"),
                StateTransition("done", "human", "approved completion gate"),
            ]
        return [
            StateTransition("todo", "human", "approve an action gate", requires_approval=True),
            StateTransition("done", "human", "approve a completion gate", requires_approval=True),
            StateTransition("blocked", "human", "hold or reject the gate", requires_approval=True),
            StateTransition("in_progress", "human", "request changes on completed work"),
        ]
    if state == "awaiting_manual_test":
        return _after_work_transitions(
            review_required=review_required,
            hitl_required=hitl_required,
            manual_required=False,
            reason_prefix="manual testing passed",
        ) + [
            StateTransition("in_progress", "human", "manual testing found required changes"),
            StateTransition("blocked", "human", "manual testing is blocked"),
        ]
    if state == "in_review":
        return [
            StateTransition("in_progress", "reviewer", "code review requested changes"),
            StateTransition("awaiting_approval", "reviewer", "review passed but needs human sign-off",
                            requires_approval=True),
            StateTransition("done", "reviewer", "review passed and no human gate remains"),
        ]
    if state == "in_progress":
        return _after_work_transitions(
            review_required=review_required,
            hitl_required=hitl_required,
            manual_required=manual_required,
            reason_prefix="implementation finished",
        ) + [
            StateTransition("blocked", "agent", "implementation cannot proceed"),
        ]
    if state == "todo":
        transitions = [StateTransition("in_progress", "agent", f"claim {tier} for work")]
        if hitl_required:
            transitions.append(
                StateTransition("awaiting_approval", "agent", "HITL gate must be approved before work",
                                requires_approval=True)
            )
        transitions.append(StateTransition("wont_do", "human", "cancel before implementation"))
        return transitions
    return [
        StateTransition("todo", "agent", f"normalize unknown state {state!r}"),
        StateTransition("blocked", "agent", f"unknown state {state!r} needs triage"),
    ]


def _after_work_transitions(
    *,
    review_required: bool,
    hitl_required: bool,
    manual_required: bool,
    reason_prefix: str,
) -> list[StateTransition]:
    transitions = []
    if manual_required:
        transitions.append(StateTransition("awaiting_manual_test", "agent", f"{reason_prefix}; QA is required"))
    if review_required:
        transitions.append(StateTransition("in_review", "agent", f"{reason_prefix}; code review is required"))
    if hitl_required:
        transitions.append(
            StateTransition("awaiting_approval", "agent", f"{reason_prefix}; human sign-off is required",
                            requires_approval=True)
        )
    if not transitions:
        transitions.append(StateTransition("done", "agent", f"{reason_prefix}; no gates remain"))
    return transitions


def _review_required(entity: Entity, config: dict[str, Any]) -> bool:
    if _truthy_attr(entity, "review_required") or _has_label(entity, "review_required", "review-required"):
        return True
    review = config.get("review")
    return isinstance(review, dict) and bool(review.get("enabled"))


def _manual_testing_required(entity: Entity, config: dict[str, Any]) -> bool:
    if _truthy_attr(entity, "manual_testing_required", "qa_required") or _has_label(
        entity, "manual-testing-required", "manual_test_required", "qa-required"
    ):
        return True
    qa = config.get("qa") or config.get("manual_testing")
    return isinstance(qa, dict) and bool(qa.get("enabled"))


def _hitl_required(entity: Entity, config: dict[str, Any]) -> bool:
    if _truthy_attr(entity, "approval_required", "hitl_required") or _has_label(
        entity, "approval-required", "hitl-required"
    ):
        return True
    if _has_label_prefix(entity, "hitl:", "gate:") or _permission_gate_required(entity, config):
        return True
    hitl = config.get("hitl")
    if isinstance(hitl, dict) and bool(hitl.get("required")):
        return True
    return False


def _conversation_items(entity: Entity, conversation: Optional[Iterable[Any]]) -> list[Any]:
    if conversation is not None:
        return list(conversation)
    for attr in ("comments", "conversation"):
        value = getattr(entity, attr, None)
        if value is not None:
            return list(value)
    post = getattr(entity, "post", None)
    comments = getattr(post, "comments", None) if post is not None else None
    return list(comments or [])


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _truthy_attr(entity: Entity, *names: str) -> bool:
    for name in names:
        if bool(getattr(entity, name, False)):
            return True
    return False


def _has_label(entity: Entity, *labels: str) -> bool:
    wanted = {label.casefold() for label in labels}
    return any(str(label).casefold() in wanted for label in entity.labels)


def _has_label_prefix(entity: Entity, *prefixes: str) -> bool:
    wanted = tuple(prefix.casefold() for prefix in prefixes)
    return any(str(label).casefold().startswith(wanted) for label in entity.labels)


def _permission_gate_required(entity: Entity, config: dict[str, Any]) -> bool:
    for key in _required_permission_keys(entity):
        if not _permission_allowed(config, key):
            return True
    return False


def _required_permission_keys(entity: Entity) -> list[str]:
    keys = []
    value = getattr(entity, "required_permissions", None)
    if isinstance(value, (list, tuple, set)):
        keys.extend(str(item) for item in value)
    elif value:
        keys.append(str(value))
    for label in entity.labels:
        raw = str(label)
        folded = raw.casefold()
        for prefix in ("permission:", "requires-permission:", "requires_permission:"):
            if folded.startswith(prefix):
                keys.append(raw[len(prefix):])
    return keys


def _permission_allowed(config: dict[str, Any], key: str) -> bool:
    parts = [part for part in key.replace("/", ".").split(".") if part]
    if not parts:
        return False
    roots = [config]
    permissions = config.get("permissions")
    if isinstance(permissions, dict):
        roots.append(permissions)
    for root in roots:
        value = root
        for part in parts:
            if not isinstance(value, dict):
                break
            value = value.get(part)
        else:
            return bool(value)
    return False


def _dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
