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

from specseed_runtime.executing import agent_report
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
    ``specseed_primary_branch`` + the git/remote permissions. Refresh-on-merge and
    the conflict-park rule are stated here because there is no runtime merge driver.
    """
    perms = _perms(ctx)
    config = getattr(ctx, "config", {}) or {}
    primary_branch = config.get("specseed_primary_branch") or "main"
    remote_on = perms.remote_enabled()
    lines = ["Git rules:"]
    lines.append(
        "- Work on a dedicated branch for this issue, forked from `{0}`. Never commit "
        "straight onto `{0}`.".format(primary_branch)
    )
    if perms.can_merge_to_primary():
        lines.append(
            "- When the work is complete and tests pass, merge your branch into `{0}`.".format(primary_branch)
        )
        lines.append(
            "- Refresh: after merging into `{0}`, and when you resume a parked branch, merge "
            "the latest `{0}` into the other live issue branches. Resolve trivial conflicts "
            "silently. Only a genuinely unsafe conflict you cannot settle should be left in "
            "place and reported for a human.".format(primary_branch)
        )
    else:
        lines.append(
            "- Do NOT merge into `{0}` yourself; leave your branch for a human to merge.".format(primary_branch)
        )
    if remote_on:
        lines.append(
            "- You may push your issue branch to the remote."
            if perms.can_push_branches()
            else "- Do NOT push your issue branch to the remote."
        )
        lines.append(
            "- You may push `{0}` to the remote.".format(primary_branch)
            if perms.can_push_primary()
            else "- Do NOT push `{0}` to the remote.".format(primary_branch)
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


# Per-route guardrail files scaffolded into the target's specseed_dir at configure
# time (configuring/configure.py). The prompts point the agent at the right one.
INSTRUCTIONS_FILES = {
    "implement": "AGENTS_INSTRUCTIONS_IMPL.md",
    "spec_change": "AGENTS_INSTRUCTIONS_SPEC.md",
    "review": "AGENTS_INSTRUCTIONS_REVIEW.md",
}


def render_identity_rule(ctx: Any, intent: str) -> str:
    """Hard rule keeping the agent inside the target and off the engine.

    The run history showed agents concluding the "app" was specseed itself
    (engine src on PYTHONPATH + ``specseed_runtime`` all over the prompts) and
    editing the engine. This block, plus the scaffolded instruction file, makes
    the boundary explicit.
    """
    specseed_dir = _specseed_dir(ctx)
    fname = INSTRUCTIONS_FILES.get(intent, INSTRUCTIONS_FILES["implement"])
    return (
        "TARGET & ENGINE (hard rule): your work target is THIS repository (your current working "
        "directory). The `specseed_runtime` package reachable on PYTHONPATH is the READ-ONLY "
        "engine that drives you - NEVER create, edit, move, or delete anything under it, the "
        "specseed engine checkout, or anywhere outside this repository. A near-empty target at "
        "the start is normal; build what the spec describes HERE. Read "
        f"{specseed_dir}/{fname} for the full rules before you start."
    )


def render_custom_instructions(ctx: Any, intent: str) -> str:
    """User-owned custom instructions appended to the agent prompt.

    Reads the global ``CUSTOM_INSTRUCTIONS.md`` plus the per-step file for
    ``intent`` from the target's specseed dir (seeded as empty stubs by
    scaffold). A pristine, untouched stub is treated as empty and skipped, so a
    user who set nothing adds nothing. Best-effort: a missing dir/file is silent.
    """
    from specseed_runtime.configuring import scaffold

    storage = getattr(ctx, "storage", None)
    if not storage:
        return ""
    base = Path(storage).parent  # <repo>/<specseed_dir>/storage -> specseed dir
    parts: list[str] = []
    for scope in ("", intent):
        fname = scaffold.CUSTOM_INSTRUCTION_FILES.get(scope)
        if not fname:
            continue
        try:
            text = (base / fname).read_text(encoding="utf-8")
        except OSError:
            continue
        pristine = scaffold._CUSTOM_STUB_HEADER.format(scope=scope or "all steps")
        if text.strip() == pristine.strip():
            continue  # untouched stub = no custom instructions
        # drop the leading specseed HTML-comment marker line if present
        body = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("<!-- specseed:")
        ).strip()
        if body:
            parts.append(body)
    if not parts:
        return ""
    return (
        "USER CUSTOM INSTRUCTIONS (project-specific; honour these alongside the rules "
        "above):\n\n" + "\n\n".join(parts)
    )


def _custom_block(ctx: Any, intent: str) -> str:
    """Custom-instruction block (trailing blank line) or '' when none, for splicing
    in just before the result-format instructions."""
    text = render_custom_instructions(ctx, intent)
    return text + "\n\n" if text else ""


def build_spec_change_prompt(route: str, request_id: Any, entity: Any, ctx: Any) -> str:
    """Prompt for the specseed spec-change worker (one route, one request)."""
    specseed_dir = _specseed_dir(ctx)
    skill_dir = _engine_skill_dir()
    return (
        render_identity_rule(ctx, agent_report.SPEC_CHANGE) + "\n\n"
        + "You are the specseed spec-change worker. Read the skill documentation at "
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
        "spec-change:status:awaiting_input label, enqueue, and stop. One request, one run.\n\n"
        "A leftover plan.json/apply.py in the request dir is a PREVIOUS run's output (e.g. a "
        "clarification round the human has now answered). Re-enqueueing it unchanged is a no-op "
        "and the human gets silence. ALWAYS rewrite plan.json + apply.py for what THIS run "
        "decided (carry forward plan.json bookkeeping like questions/apr), then enqueue.\n\n"
        + _custom_block(ctx, agent_report.SPEC_CHANGE)
        + agent_report.result_instructions(agent_report.SPEC_CHANGE)
    )


def build_implement_prompt(entity: Any, ctx: Any) -> str:
    """Prompt for implementing a ready issue."""
    specseed_dir = _specseed_dir(ctx)
    return (
        render_identity_rule(ctx, agent_report.IMPLEMENT) + "\n\n"
        + "You are implementing a specseed work issue. The issue is remote post "
        f"{getattr(entity, 'post_id', '?')} titled {_title(entity)!r}. Read its body and "
        f"comments from the local tracker for context. Read the spec under {specseed_dir}/spec/ "
        f"before coding: start with {specseed_dir}/spec/vision.md, then {specseed_dir}/spec/sad.md "
        "(the authoritative project layout - match it, do not invent a different structure), then "
        "the SDD/SRS for the area you touch. "
        "If this issue was reviewed before, the latest `Code review` comment lists the findings "
        "that bounced it back - read it and address every point. "
        "Implement the change in this repository to satisfy the issue, keeping edits scoped "
        "to what the issue asks. Do not change the issue's workflow labels or approve "
        "anything yourself; the scheduler advances state programmatically. When finished, "
        "leave the working tree in a building, test-passing state.\n\n"
        + render_git_policy(ctx)
        + "\n\n"
        + render_action_gates(ctx)
        + "\n\n"
        + _custom_block(ctx, agent_report.IMPLEMENT)
        + agent_report.result_instructions(agent_report.IMPLEMENT)
    )


_PE_REASON = {
    "new": (
        "First engagement: the early automatic retries did not clear it, so this "
        "looks like a real problem, not a blip. Investigate, then REWRITE the post "
        "body with what you found. The body currently holds only the raw error stub."
    ),
    "exhausted": (
        "Automatic retries ran out; still failing. Dig deeper than last time and "
        "give concrete fix options. Update the body; add a comment summarizing "
        "what changed since your last report."
    ),
    "reply": (
        "The human replied on the thread. Read their last comment and answer it "
        "as a comment. Update the body only if the situation materially changed."
    ),
}


def _thread_transcript(post: Any, limit: int = 4000) -> str:
    lines = []
    for comment in getattr(post, "comments", []) or []:
        author = getattr(comment, "author", None) or "?"
        body = str(getattr(comment, "body", "") or "").strip()
        lines.append(f"[{author}] {body}")
    text = "\n---\n".join(lines) or "(no comments yet)"
    if len(text) > limit:
        text = "...[truncated]" + text[-limit:]
    return text


def build_platform_error_prompt(post: Any, payload: dict, reason: str, ctx: Any) -> str:
    """Prompt for the resolve_platform_errors chain: diagnose + report + converse.

    Thread-as-memory: every engagement is a fresh run; the post body + comments
    carry the whole conversation state.
    """
    storage = str(getattr(ctx, "storage", "") or "")
    engine_src = str(default_specseed_dir() / "src")
    post_id = getattr(post, "id", None)
    origin_task = payload.get("origin_task_id")
    origin_action = payload.get("origin_action")
    origin_post = payload.get("origin_post_id")
    body = str(getattr(post, "body", "") or "")
    return f"""You are specseed's platform-error resolver. A platform task failed. Diagnose it, \
report on the error post, talk with the human there. You are read-only everywhere EXCEPT that \
one post: edit its body, add comments. Never touch code, git, other posts, labels, or the queue.

WHY THIS RUN: {reason}. {_PE_REASON.get(reason, _PE_REASON["new"])}

FACTS
- error post: id {post_id}, title {getattr(post, "title", "")!r}
- origin: task {origin_task} ({origin_action}) about post {origin_post}
- storage dir (logs live here): {storage}
  - platform.log = JSON-lines event log. grep the origin task id first.
  - specseed.db = work queue. recorded errors:
    sqlite3 "{storage}/specseed.db" "SELECT message FROM task_errors WHERE task_id={origin_task}"
- engine source (read-only; ONLY when the logs do not explain it): {engine_src}
- target repo (your cwd) is the repo the platform works on, not the platform itself.

THE POST NOW
---
{body}
---
THREAD (oldest first)
{_thread_transcript(post)}

HOW TO WRITE BACK (run via Bash; PYTHONPATH and storage env are already wired):
python3 - <<'PY'
from specseed_runtime.tracking.resolve_remote import resolve_remote
from specseed_runtime.platform_identity import platform_comment
r = resolve_remote(r"{storage}")
r.edit_entry({post_id!r}, body=NEW_BODY)                          # update the report
r.add_entry_comment({post_id!r}, platform_comment("..."))        # or reply on the thread
PY
Check .ok on every call; if a write fails, say so in your final output.

REPORT RULES (the reader may not be an engineer)
- Plain words, short. What happened, what it likely means, what you suggest.
- 2-3 suggestions max, ranked by effort. Say which you would pick and why, in one line.
- Retries are automatic: the runtime schedules them and comments every attempt. Do not \
promise manual retries. Do not retry, re-run, or fix anything yourself.
- Quote at most ONE short error snippet. No log dumps. Not too technical. No em-dashes.
- KEEP the final `<!-- specseed:platform-error task=... -->` line in the body VERBATIM. It \
links post to task; losing it breaks recovery.

DONE = the post tells a human something they can act on. End your output with exactly:
PLATFORM_ERROR_REPORTED"""


def build_review_prompt(entity: Any, ctx: Any) -> str:
    """Prompt for reviewing an issue that is in review."""
    specseed_dir = _specseed_dir(ctx)
    return (
        render_identity_rule(ctx, agent_report.REVIEW) + "\n\n"
        + "You are reviewing completed work for specseed work issue "
        f"{getattr(entity, 'post_id', '?')} titled {_title(entity)!r}. Read the issue body "
        f"and comments from the local tracker; consult {specseed_dir}/spec/vision.md and "
        f"{specseed_dir}/spec/sad.md for intent and the expected layout. Inspect the relevant "
        "changes in this repository (use the git diff against the primary branch to see what "
        "this work touched). "
        "Assess correctness, scope, and whether the issue's acceptance criteria "
        "are met. Do not merge, do not change "
        "workflow labels, and do not approve; the scheduler resolves the outcome "
        "programmatically from your verdict and the configured gates.\n\n"
        + _custom_block(ctx, agent_report.REVIEW)
        + agent_report.result_instructions(agent_report.REVIEW)
    )
