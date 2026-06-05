"""prompts.py - natural-language prompts handed to the coding agent.

Agents are used for exactly three jobs in the new specseed: following a
spec-change order, implementing a ready issue, and reviewing work in review.
Permissions and approvals are decided programmatically (see ``permissions.py`` and
the state machine), never inside these prompts.

Headless ``claude -p`` drops user-invoked slash commands, so prompts are plain
task descriptions that point the agent at the engine's skill docs (an absolute
path - the engine is not in the target) rather than ``/specseed`` invocations.

Only Python stdlib is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specseed_runtime.executing.permissions import (
    AGENT_CATEGORIES,
    Permissions,
)
from specseed_runtime.storage_paths import default_specseed_dir


def _engine_skill_dir() -> str:
    """Absolute path to the engine's skill docs - they live with the engine, not
    in the target. The agent runs in the target (cwd=repo_root), so it needs the
    full path to read SKILL.md / routes."""
    return str(default_specseed_dir() / "skills" / "specseed")


def _title(entity: Any) -> str:
    return getattr(entity, "title", None) or "(untitled)"


_GATE_RULE = {
    "block": "do NOT do it; stop, report it is blocked, and leave it for a human",
    "require_human_approval": "do NOT do it yet; stop and report it needs human approval first",
    "surface": "do it, but announce it clearly in your final report",
    "auto": "just do it",
}


def render_action_gates(ctx: Any) -> str:
    """Action-class gate policy the implementation agent must honour.

    Deterministic from ``permissions.agents``. Each action class carries a level the
    agent obeys before taking such an action.
    """
    perms = getattr(ctx, "permissions", None)
    if not isinstance(perms, Permissions):
        perms = Permissions(getattr(ctx, "config", {}) or {})
    policy = perms.agents_policy()
    lines = ["Action gates (honour BEFORE taking any such action):"]
    for category, desc in AGENT_CATEGORIES.items():
        level = policy.get(category, "block")
        lines.append(
            "- {0} ({1}) -> {2} [{3}]".format(category, desc, _GATE_RULE.get(level, level), level)
        )
    lines.append(
        "When unsure which class an action falls in, treat it as the stricter case. These "
        "gates fire mid-work regardless of which issue is active."
    )
    return "\n".join(lines)


def _perms(ctx: Any) -> Permissions:
    perms = getattr(ctx, "permissions", None)
    if isinstance(perms, Permissions):
        return perms
    return Permissions(getattr(ctx, "config", {}) or {})


def render_git_policy(ctx: Any) -> str:
    """Git rules the implementation agent must follow.

    The runtime never runs git itself; the agent does. This block is the agent's
    only source of truth for branch/merge/push/PR behaviour, derived from
    ``dev_branch`` + the git/remote permissions. Refresh-on-merge and the
    conflict-park rule are stated here because there is no runtime merge driver.
    """
    perms = _perms(ctx)
    config = getattr(ctx, "config", {}) or {}
    dev_branch = config.get("dev_branch") or "main"
    remote_on = perms.remote_enabled()
    lines = ["Git rules:"]
    lines.append(
        "- Work on a dedicated branch for this issue, forked from `{0}`. Never commit "
        "straight onto `{0}`.".format(dev_branch)
    )
    if perms.can_merge_to_dev_branch():
        lines.append(
            "- When the work is complete and tests pass, merge your branch into `{0}`.".format(dev_branch)
        )
        lines.append(
            "- Refresh: after merging into `{0}`, and when you resume a parked branch, merge "
            "the latest `{0}` into the other live issue branches. Resolve trivial conflicts "
            "silently. Only a genuinely unsafe conflict you cannot settle should be left in "
            "place and reported for a human.".format(dev_branch)
        )
    else:
        lines.append(
            "- Do NOT merge into `{0}` yourself; leave your branch for a human to merge.".format(dev_branch)
        )
    if remote_on:
        lines.append(
            "- You may push your issue branch to the remote."
            if perms.can_push_branches()
            else "- Do NOT push your issue branch to the remote."
        )
        lines.append(
            "- You may push `{0}` to the remote.".format(dev_branch)
            if perms.can_push_dev_branch()
            else "- Do NOT push `{0}` to the remote.".format(dev_branch)
        )
        lines.append(
            "- You may open a pull/merge request for completed work."
            if perms.can_make_prs()
            else "- Do NOT open pull/merge requests."
        )
    else:
        lines.append("- No remote is configured: keep everything local, do not push or open PRs.")
    return "\n".join(lines)


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
    skill_dir = _engine_skill_dir()
    return (
        "You are the specseed spec-change worker. Read the skill documentation at "
        f"{skill_dir}/SKILL.md and the matching route under "
        f"{skill_dir}/routes/{route}.md, then run the '{route}' route "
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
        "leave the working tree in a building, test-passing state.\n\n"
        + render_git_policy(ctx)
        + "\n\n"
        + render_action_gates(ctx)
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
