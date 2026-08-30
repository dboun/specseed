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

import importlib.util
from pathlib import Path
from typing import Any

from specseed_runtime.executing import agent_report
from specseed_runtime.executing.permissions import (
    AGENT_CATEGORIES,
    Permissions,
)
from specseed_runtime.scheduling.spec_change import spec_change_dir
from specseed_runtime.storage_paths import (
    default_specseed_dir,
    instructions_dir,
    spec_dir,
    tracker_dir,
)


def _engine_skill_dir() -> str:
    """Absolute path to the engine's skill docs - they live with the engine, not
    in the target. The agent runs in the target (cwd=repo_root), so it needs the
    full path to read SKILL.md / routes."""
    return str(default_specseed_dir() / "skills" / "specseed")


_PROMPT_GEN_MOD: Any = None


def _prompt_generator() -> Any:
    """Load (once) the skill's ``generate_prompt_from_skill`` module by file path.

    It lives under the engine's skill tree, not on PYTHONPATH, so we exec it from
    its absolute path. Cached: the path never changes within a process."""
    global _PROMPT_GEN_MOD
    if _PROMPT_GEN_MOD is None:
        path = Path(_engine_skill_dir()) / "scripts" / "generate_prompt_from_skill.py"
        spec = importlib.util.spec_from_file_location("specseed_generate_prompt_from_skill", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load prompt generator at {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PROMPT_GEN_MOD = mod
    return _PROMPT_GEN_MOD


def _skill_mode(ctx: Any) -> str:
    """The skill bundle mode for this repo's tracking provider.

    ``local`` (the no-remote stand-in managed by the web UI) -> ``specseed-ui``;
    ``github``/``gitlab`` pass through. Anything unresolvable falls back to
    ``specseed-ui``. ``chat`` is skill-only (no runtime), never produced here."""
    storage = getattr(ctx, "storage", None)
    if not storage:
        return "specseed-ui"
    try:
        from specseed_runtime.tracking.resolve_remote import load_remote_state

        state = load_remote_state(storage)
    except Exception:
        return "specseed-ui"
    if not state.get("enabled"):
        return "specseed-ui"
    provider = str(state.get("provider") or "").lower()
    return provider if provider in ("github", "gitlab") else "specseed-ui"


def _skill_bundle(ctx: Any, route: str, subroute: str | None = None) -> str:
    """The assembled skill prompt for ``route`` (+ optional ``subroute``).

    Prepended to EVERY agent run. Carries SKILL.md + the route/subroute docs + their
    mandatory reads + the user-owned instruction-file reads (absolute paths under the
    home ``instructions/`` dir). The runtime prompt that follows adds only per-request
    execution facts - never re-states the skill."""
    return _prompt_generator().generate_prompt_from_skill(
        _skill_mode(ctx), route, subroute, instructions_dir=_instructions_dir(ctx)
    )


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
    approved = perms.agent_allowed_directories()
    if approved:
        lines.append(
            "Approved directories outside the repo (a human granted each one; you may read "
            "and write there despite the `outside_repo` gate): " + ", ".join(approved)
        )
    lines.append(
        "When unsure which class an action falls in, treat it as the stricter case. These "
        "gates fire mid-work regardless of which issue is active."
    )
    lines.append(
        "Hitting a gate is NOT a dead end. When the obstacle is the MACHINE rather than the "
        "code - a toolchain that is not installed, a service that is not running, or one "
        "specific directory outside the repo you need - do not write a paragraph about what "
        "you are not permitted to do. Finish and commit whatever work does not depend on it, "
        "then report `status: \"needs_user_action\"` with a `user_action` object (see the "
        "RESULT FILE schema). Name the setup command in it whenever one exists: the human "
        "gets a Run setup button that runs it as them, plus a Check button, and the issue "
        "resumes from your branch the moment the check passes. A request that could have "
        "carried a command and instead only described one is a worse request."
    )
    return "\n".join(lines)


# How each resolved skill mode renders a user-facing reply. Stated explicitly in the
# prompt so the agent never has to infer the form from the provider (a local repo has no
# provider in remote.json, which historically made the agent fall to chat/natural prose
# instead of the structured envelope - see reply-protocol-base.md Step 0).
_REPLY_FORM = {
    "specseed-ui": (
        "specseed-UI (provider is local; the specseed web UI is the only renderer). Every "
        "user-facing reply - clarification rounds included - MUST be the STRUCTURED JSON "
        "envelope from reply-protocol-base.md, NEVER natural prose. The comment body you "
        "stage IS that JSON envelope."
    ),
    "github": (
        "external (provider is github; replies are read natively on GitHub). Use NATURAL "
        "markdown prose per reply-protocol-base.md - never the JSON envelope."
    ),
    "gitlab": (
        "external (provider is gitlab; replies are read natively on GitLab). Use NATURAL "
        "markdown prose per reply-protocol-base.md - never the JSON envelope."
    ),
}


def render_reply_mode(ctx: Any) -> str:
    """The resolved render mode + reply form, stated outright in the prompt.

    ``_skill_mode`` already maps the provider to a mode for the skill bundle header, but
    that header is easy to miss and the skill's own Step 0 otherwise infers the form from
    the provider - which a local repo lacks. This line makes the choice explicit so the
    worker uses the right form (structured envelope vs natural prose) every run.
    """
    mode = _skill_mode(ctx)
    return "Reply render mode: " + _REPLY_FORM.get(mode, _REPLY_FORM["specseed-ui"])


def render_git_policy(ctx: Any) -> str:
    """Git rules: the RUNTIME owns git, the agent does not touch it.

    The runtime branches before the run, commits the result after, returns to the
    primary branch, and drives merges (gated) itself. So the agent's only git rule
    is: do not run git at all. Stated explicitly because agents historically tried
    to branch/commit/merge and left work stranded.
    """
    config = getattr(ctx, "config", {}) or {}
    primary_branch = config.get("specseed_primary_branch") or "main"
    return "\n".join([
        "Git rules (the runtime owns git - you do NOT):",
        "- Do NOT run any git command: no branch, add, commit, checkout, merge, rebase, "
        "push, or pull/merge request. The runtime handles all of it.",
        "- You are already on this issue's dedicated branch (forked from `{0}`). The "
        "runtime commits your edits after the run, returns to `{0}`, and performs any "
        "merge itself (gated by configuration).".format(primary_branch),
        "- Your job is only to edit files. Leave the working tree in a building, "
        "test-passing state; do not stage or commit it yourself.",
    ])


def _data_root(ctx: Any) -> Path:
    """The per-repo data root (``ctx.storage``). Absolute; lives in the app home."""
    return Path(getattr(ctx, "storage", "") or ".").resolve()


def _instructions_dir(ctx: Any) -> str:
    return str(instructions_dir(_data_root(ctx)))


def _grant_lines(*pairs: tuple[str, str]) -> str:
    """Render the route's data-dir grants as absolute paths. Each pair is
    (absolute path, what it is + access). The target repo (cwd) is always implied."""
    lines = [
        "Data dirs you may use this run (ABSOLUTE paths; live in the specseed home, "
        "OUTSIDE the target repo). Use ONLY these - everything else under the home "
        "(the work queue, config, token, logs) is off-limits:"
    ]
    for path, what in pairs:
        lines.append("- {0}  ({1})".format(path, what))
    lines.append(
        "Your working dir is the TARGET repo itself (build the app there). Read the "
        "post body + thread from THIS PROMPT - never query a tracker db for messages."
    )
    return "\n".join(lines)


def _thread_block(entity: Any, conversation: Any) -> str:
    """The post body + comment thread, injected so the agent needs no db read."""
    body = str(getattr(entity, "body", "") or "").strip() or "(empty body)"
    lines = []
    for comment in conversation or []:
        author = getattr(comment, "author", None) or "?"
        ctext = str(getattr(comment, "body", "") or "").strip()
        lines.append("[{0}] {1}".format(author, ctext))
    thread = "\n---\n".join(lines) or "(no comments yet)"
    return (
        "POST (id {0}, {1!r})\n---\n{2}\n---\nTHREAD (oldest first; the latest human "
        "comment is the live ask/answer)\n{3}".format(
            getattr(entity, "post_id", "?"), _title(entity), body, thread
        )
    )


def render_identity_rule(ctx: Any, intent: str) -> str:
    """Hard rule keeping the agent inside the target and off the engine.

    The run history showed agents concluding the "app" was specseed itself
    (engine src on PYTHONPATH + ``specseed_runtime`` all over the prompts) and
    editing the engine. This block makes the boundary explicit. (The skill bundle
    points the agent at the per-route guardrail file; this is the always-on rule.)
    ``intent`` is accepted for call-site symmetry; the rule is intent-independent.
    """
    return (
        "TARGET & ENGINE (hard rule): your work target is THIS repository (your current working "
        "directory). The `specseed_runtime` package reachable on PYTHONPATH is the READ-ONLY "
        "engine that drives you - NEVER create, edit, move, or delete anything under it, the "
        "specseed engine checkout, or anywhere outside this repository. A near-empty target at "
        "the start is normal; build what the spec describes HERE."
    )


def build_spec_change_prompt(
    subroute: str, request_id: Any, entity: Any, ctx: Any, conversation: Any = None
) -> str:
    """Prompt for the specseed spec-change worker (one subroute, one request).

    The skill bundle (route ``spec`` + ``subroute``) carries the full plan-first /
    JSON plan / approval contract; this adds the per-request facts + dir grants. The
    spec route is the one route that may read the local tracker (it plans over the
    whole entity tree); the post thread is still injected so it needs no db for it.
    """
    root = _data_root(ctx)
    request_dir = spec_change_dir(request_id, root)
    grants = _grant_lines(
        (str(spec_dir(root)), "the LIVE spec - read for context; NEVER edit it here"),
        (str(request_dir), "your spec-change dir - stage spec + write plan.json HERE"),
        (str(tracker_dir(root)), "local tracker db - read the entity tree to plan (resolve_local)"),
        (str(instructions_dir(root) / "spec"), "user-owned repo context + custom instructions"),
    )
    return (
        _skill_bundle(ctx, "spec", subroute) + "\n\n"
        + render_identity_rule(ctx, agent_report.SPEC_CHANGE) + "\n\n"
        + render_reply_mode(ctx) + "\n\n"
        + grants + "\n\n"
        + _thread_block(entity, conversation) + "\n\n"
        + f"Run the spec '{subroute}' subroute for spec-change request {request_id}. Read the "
        "entity tree from the LOCAL tracker (resolve_local on the tracker dir above); never poll "
        f"the remote to plan. Stage every created/edited spec doc under {request_dir}/spec/ "
        "(mirroring its path under the live spec/), and write plan.json into "
        f"{request_dir}/. Then STOP: do not enqueue, do not run generated code, do not choose whether "
        "it needs approval, do not touch git or application code. The runtime reads your output "
        "and gates it. One request, one run.\n\n"
        + render_action_gates(ctx) + "\n\n"
        + agent_report.result_instructions(agent_report.SPEC_CHANGE)
    )


def build_implement_prompt(entity: Any, ctx: Any, conversation: Any = None) -> str:
    """Prompt for implementing a ready issue (bundle + execution facts + thread)."""
    root = _data_root(ctx)
    grants = _grant_lines(
        (str(spec_dir(root)), "the LIVE spec - read for what to build"),
        (str(instructions_dir(root) / "impl"), "user-owned repo context + custom instructions"),
    )
    return (
        _skill_bundle(ctx, "impl") + "\n\n"
        + render_identity_rule(ctx, agent_report.IMPLEMENT) + "\n\n"
        + grants + "\n\n"
        + _thread_block(entity, conversation) + "\n\n"
        + "Implement this work issue in the target repo. The post body + thread above are your "
        "full context (incl. the latest `Code review` comment, if this issue bounced back). Keep "
        "edits scoped to what the issue asks; do not change workflow labels or approve anything - "
        "the scheduler advances state. When finished, leave the working tree in a building, "
        "test-passing state.\n\n"
        + render_git_policy(ctx)
        + "\n\n"
        + render_action_gates(ctx)
        + "\n\n"
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
    "fatal": (
        "This failure is not auto-retryable - a deterministic refusal, bad "
        "payload, or crash that a re-run cannot fix. No retries will happen. "
        "Find the root cause, then REWRITE the post body with what you found and "
        "concrete fix options. The body currently holds only the raw error stub."
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

    The skill bundle (route ``platform-error``) carries the resolver how-to (read-only
    rule, report rules, marker + sentinel discipline); this adds only the per-engagement
    facts + the write-back mechanism. Thread-as-memory: every engagement is a fresh run;
    the post body + comments carry the whole conversation state.
    """
    storage = str(getattr(ctx, "storage", "") or "")
    engine_src = str(default_specseed_dir() / "src")
    post_id = getattr(post, "id", None)
    origin_task = payload.get("origin_task_id")
    origin_action = payload.get("origin_action")
    origin_post = payload.get("origin_post_id")
    body = str(getattr(post, "body", "") or "")
    return _skill_bundle(ctx, "platform-error") + "\n\n" + f"""You are specseed's \
platform-error resolver. A platform task failed. Diagnose it per the platform-error route, \
report on the error post, talk with the human there.

WHY THIS RUN: {reason}. {_PE_REASON.get(reason, _PE_REASON["new"])}

FACTS
- error post: id {post_id}, title {getattr(post, "title", "")!r}
- origin: task {origin_task} ({origin_action}) about post {origin_post}
- data root (the WHOLE repo data dir is yours for diagnosis - logs, queue, config): {storage}
  - logs/platform.log = JSON-lines event log. grep the origin task id first.
  - db/specseed.db = work queue. recorded errors:
    sqlite3 "{storage}/db/specseed.db" "SELECT message FROM task_errors WHERE task_id={origin_task}"
- engine source (read-only; ONLY when the logs do not explain it): {engine_src}
- Do NOT rewrite engine state/history (don't mutate the queue/config by hand) - just
  diagnose, then fix the underlying cause or surface concrete options on the post.

THE POST NOW
---
{body}
---
THREAD (oldest first)
{_thread_transcript(post)}

HOW TO WRITE BACK (run via Bash; PYTHONPATH and storage env are already wired):
python3 - <<'PY'
from specseed_runtime.tracking.resolve_remote import load_config, resolve_remote
from specseed_runtime.platform_identity import platform_comment
r = resolve_remote(r"{storage}")
cfg = load_config(r"{storage}")                                  # drives the comment prefix
r.edit_entry({post_id!r}, body=NEW_BODY)                          # update the report
r.add_entry_comment({post_id!r}, platform_comment("...", cfg))   # or reply on the thread
PY
Check .ok on every call; if a write fails, say so in your final output."""


def build_merge_conflict_prompt(
    entity: Any, branch: str, primary: str, files: list, ctx: Any, direction: str = "merge"
) -> str:
    """Prompt for resolving git merge conflicts. The agent ONLY edits the conflicted
    files; the runtime started the merge and completes it.

    The skill bundle (route ``merge-conflicts``) carries the resolve-by-editing /
    remove-markers / no-git how-to; this adds the per-run situation + file list.
    ``direction`` says which way the merge runs, so the prompt is accurate:
    ``"merge"`` = the final issue ``branch`` -> ``primary`` merge; ``"prepare"`` =
    bringing ``primary`` INTO the issue ``branch`` to ready it before a gate.
    """
    file_list = "\n".join("  - {0}".format(f) for f in (files or [])) or "  (see `git status`)"
    if direction == "prepare":
        situation = (
            "The runtime is bringing the primary branch `{0}` INTO this issue's branch "
            "`{1}` for issue {2} ({3!r}) to ready it for a later merge, and hit conflicts. "
            "The repository is mid-merge on branch `{1}` right now.".format(
                primary, branch, getattr(entity, "post_id", "?"), _title(entity)
            )
        )
    else:
        situation = (
            "The runtime started merging the issue branch `{0}` into `{1}` for issue {2} "
            "({3!r}) and hit conflicts. The repository is mid-merge right now.".format(
                branch, primary, getattr(entity, "post_id", "?"), _title(entity)
            )
        )
    return (
        _skill_bundle(ctx, "merge-conflicts") + "\n\n"
        + render_identity_rule(ctx, agent_report.IMPLEMENT) + "\n\n"
        + "You are resolving git MERGE CONFLICTS. " + situation + "\n\n"
        "Conflicted files:\n" + file_list + "\n\n"
        "Resolve every conflict by editing these files per the merge-conflicts route (remove "
        "every conflict marker; leave the tree building and test-passing; do NOT run git). If "
        "a conflict is genuinely unsafe to resolve mechanically, leave that file's markers and "
        "say so in your report.\n\n"
        + agent_report.result_instructions(agent_report.IMPLEMENT)
    )


def build_ask_prompt(entity: Any, ctx: Any, conversation: Any = None) -> str:
    """Prompt for answering a question post (bundle + execution facts + thread). READ-ONLY."""
    root = _data_root(ctx)
    grants = _grant_lines(
        (str(spec_dir(root)), "the LIVE spec - read for context"),
        (str(instructions_dir(root) / "ask"), "user-owned repo context + custom instructions"),
    )
    return (
        _skill_bundle(ctx, "ask") + "\n\n"
        + render_identity_rule(ctx, agent_report.ASK) + "\n\n"
        + grants + "\n\n"
        + _thread_block(entity, conversation) + "\n\n"
        + "Answer the question above (the latest human comment is the live question or the "
        "answer to your last clarification). Route the question to its source per the ask route, "
        "read it, and compose the answer. You are READ-ONLY: change nothing - not the target "
        "repo, not the spec - the runtime posts your `answer` as a comment on the post.\n\n"
        + agent_report.result_instructions(agent_report.ASK)
    )


def build_review_prompt(entity: Any, ctx: Any, conversation: Any = None) -> str:
    """Prompt for reviewing an issue that is in review (bundle + thread + compare target)."""
    root = _data_root(ctx)
    primary = (getattr(ctx, "config", {}) or {}).get("specseed_primary_branch") or "main"
    grants = _grant_lines(
        (str(spec_dir(root)), "the LIVE spec - read to judge against intent"),
        (str(instructions_dir(root) / "review"), "user-owned repo context + custom instructions"),
    )
    return (
        _skill_bundle(ctx, "review") + "\n\n"
        + render_identity_rule(ctx, agent_report.REVIEW) + "\n\n"
        + grants + "\n\n"
        + _thread_block(entity, conversation) + "\n\n"
        + "Review the completed work for this issue. The runtime has ALREADY checked out the "
        f"issue's branch for you - inspect the change with `git diff {primary}...HEAD` (compare "
        f"the work against the primary branch `{primary}`). Do NOT switch branches, merge, change "
        "workflow labels, or approve - the scheduler resolves the outcome from your verdict and "
        "the configured gates. Keep it simple: read the diff, judge it, report.\n\n"
        + agent_report.result_instructions(agent_report.REVIEW)
    )
