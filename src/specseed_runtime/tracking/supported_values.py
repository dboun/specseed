"""
supported_values.py - stable names used by tracking code.

Providers may return other labels and reactions. Track them as data. These
constants are the specseed names code should prefer for autocomplete and typos.
"""

from __future__ import annotations


REACTION_EYES = "eyes"
REACTION_HEART = "heart"
REACTION_THUMBS_UP = "thumbs_up"
REACTION_THUMBS_DOWN = "thumbs_down"

SUPPORTED_REACTIONS = frozenset(
    {
        REACTION_EYES,
        REACTION_HEART,
        REACTION_THUMBS_UP,
        REACTION_THUMBS_DOWN,
    }
)

SPEC_CHANGE_LABELS = frozenset(
    {
        "spec-change:adopt",
        "spec-change:adapt",
        "spec-change:tweak",
        "spec-change:inject",
        "spec-change:plan-next-sprint",
        "spec-change:status:open",
        "spec-change:status:awaiting_input",
        "spec-change:status:awaiting_approval",
        "spec-change:status:approved",
        "spec-change:status:done",
        "spec-change:status:rejected",
    }
)

MANAGEMENT_LABELS = frozenset(
    {"draft", "current_sprint", "management", "question", "platform_error"}
)
WORK_TIER_LABELS = frozenset({"epic", "ticket", "issue"})
# Work-item kind. Issues always carry one; tickets may (never ``qa``). ``qa`` marks a
# ticket's terminal QA pass. Drives the entity body template + lets the runtime filter.
WORK_TYPE_LABELS = frozenset(
    {f"type:{kind}" for kind in ("feature", "bug", "chore", "spike", "qa")}
)
# Issue difficulty (optional). Modifies the code-review gate: ``hard`` never
# auto-approves, always landing in ``awaiting_approval`` for a human.
DIFFICULTY_LABELS = frozenset({f"difficulty:{level}" for level in ("easy", "hard")})
WORK_STATUS_LABELS = frozenset(
    {
        f"{tier}:status:{status}"
        for tier in ("epic", "ticket", "issue")
        for status in (
            "todo",
            "in_progress",
            "blocked",
            "in_review",
            "awaiting_approval",
            "awaiting_merge",
            "done",
            "wont_do",
            "deprecated",
        )
    }
)

SUPPORTED_LABELS = frozenset(
    set(SPEC_CHANGE_LABELS)
    | set(MANAGEMENT_LABELS)
    | set(WORK_TIER_LABELS)
    | set(WORK_TYPE_LABELS)
    | set(DIFFICULTY_LABELS)
    | set(WORK_STATUS_LABELS)
)
