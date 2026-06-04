"""prompts.py - natural-language prompts handed to the coding agent.

Agents are used for exactly three jobs in the new specseed: following a
spec-change order, implementing a ready issue, and reviewing work in review.
Permissions and approvals are decided programmatically (see ``permissions.py`` and
the state machine), never inside these prompts.

Headless ``claude -p`` drops user-invoked slash commands, so prompts are plain
task descriptions that point the agent at the installed skill docs rather than
``/specseed`` invocations.

Only Python stdlib is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _title(entity: Any) -> str:
    return getattr(entity, "title", None) or "(untitled)"


def _specseed_dir(ctx: Any) -> str:
    configured = (getattr(ctx, "config", {}) or {}).get("specseed_dir")
    if configured:
        return str(configured).rstrip("/")
    storage = getattr(ctx, "storage", None)
    if storage is not None:
        return Path(storage).parent.name
    return "<specseed_dir>"


def build_spec_change_prompt(route: str, request_id: Any, entity: Any, ctx: Any) -> str:
    """Prompt for the specseed spec-change worker (one route, one request)."""
    specseed_dir = _specseed_dir(ctx)
    return (
        "You are the specseed spec-change worker. Read the skill documentation at "
        f"{specseed_dir}/skills/specseed/SKILL.md and the matching route under "
        f"{specseed_dir}/skills/specseed/routes/{route}.md, then run the '{route}' route "
        f"for spec-change request {request_id} (remote post titled {_title(entity)!r}).\n\n"
        "Read context from the LOCAL tracker only (resolve_local / tracking_local.db); "
        f"never poll the remote to plan. Edit the spec under {specseed_dir}/spec/ as the route "
        f"dictates, write the reconcile script {specseed_dir}/storage/spec-change/"
        f"{request_id}/apply.py that projects the work-breakdown changes onto the remote "
        "through the resolve_remote() tracker, then enqueue it with "
        "scheduling/spec_change.enqueue_spec_change_run(...). Do NOT run the script, touch "
        "git, or edit application code. If the request is materially ambiguous, make only "
        "the edits you are confident about, have apply.py post a clarifying comment plus the "
        "spec-change:status:awaiting_approval label, enqueue, and stop. One request, one run."
    )


def build_implement_prompt(entity: Any, ctx: Any) -> str:
    """Prompt for implementing a ready issue."""
    specseed_dir = _specseed_dir(ctx)
    return (
        "You are implementing a specseed work issue. The issue is remote post "
        f"{getattr(entity, 'post_id', '?')} titled {_title(entity)!r}. Read its body and "
        f"comments from the local tracker for context and read the spec under {specseed_dir}/spec/. "
        "Implement the change in this repository to satisfy the issue, keeping edits scoped "
        "to what the issue asks. Do not change the issue's workflow labels or approve "
        "anything yourself; the scheduler advances state programmatically. When finished, "
        "leave the working tree in a building, test-passing state."
    )


def build_review_prompt(entity: Any, ctx: Any) -> str:
    """Prompt for reviewing an issue that is in review."""
    return (
        "You are reviewing completed work for specseed work issue "
        f"{getattr(entity, 'post_id', '?')} titled {_title(entity)!r}. Read the issue body "
        "and comments from the local tracker and inspect the relevant changes in this "
        "repository. Assess correctness, scope, and whether the issue's acceptance criteria "
        "are met. Report findings as a concise review summary. Do not merge, do not change "
        "workflow labels, and do not approve; the scheduler resolves the outcome "
        "programmatically from your review and the configured gates.\n\n"
        "End your output with EXACTLY ONE final line in this format (nothing after it):\n"
        "SPECSEED_REVIEW verdict=<approve|changes> confidence=<0.0-1.0>\n"
        "Use verdict=approve only if the work is correct and complete; otherwise "
        "verdict=changes. confidence is your certainty in that verdict."
    )
