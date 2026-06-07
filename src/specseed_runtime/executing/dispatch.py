"""dispatch.py - turn a claimed DB task into the right reaction.

The scheduler claims a task off the queue and hands it here. ``dispatch`` switches
on ``task["action"]``:

* ``run_spec_change_script`` -> run the generated ``apply.py`` as a gated
  subprocess that mutates the remote (``run_spec_change_script``).
* every work handler (entry created/updated/reopened, label/comment/reaction
  added/removed) -> read the entity from the local mirror, judge its legal state
  with the state machine, and decide an :class:`AgentIntent`. A real intent builds
  the matching prompt and runs the injected agent; a non-actionable event is a
  success no-op.
* ``cleanup`` -> a best-effort success no-op recording that an interrupted task was
  torn down.
* anything else -> a hard failure.

Permissions and state are judged here in code; the agent only ever does the work.

Only Python stdlib is used.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from specseed_runtime.executing import advance
from specseed_runtime.executing import git_ops
from specseed_runtime.executing.agent_runner import stdout_tail
from specseed_runtime.executing import context as context_mod
from specseed_runtime.executing import inflight
from specseed_runtime.executing import recovery
from specseed_runtime.executing import platform_log
from specseed_runtime.executing.context import ExecutionContext
from specseed_runtime.executing import prompts
from specseed_runtime.scheduling.spec_change import (
    SPEC_CHANGE_ACTION,
    SPEC_CHANGE_PROPOSE_ACTION,
    spec_change_dir,
)
from specseed_runtime.state_machines.approvals import (
    approval_request_comment,
    requested_apr_ids,
)
from specseed_runtime.state_machines.base import evaluate_entity_state
from specseed_runtime.platform_identity import platform_comment
from specseed_runtime.storage_paths import SPECSEED_STORAGE_ENV


# How often the spec-change subprocess wait loop checks cancel/timeout.
_POLL_INTERVAL_S = 0.25
# Grace given to a terminate() before a kill().
_GRACE_S = 10.0

CLEANUP_ACTION = "cleanup"
_SPEC_CHANGE_LABEL_PREFIX = "spec-change:"
_SPEC_CHANGE_STATUS_PREFIX = "spec-change:status:"

# Spec-change request statuses that still want a worker run. ``done``/``rejected``
# are terminal: the request is settled and must never re-run the agent. The route
# label (``spec-change:adapt`` etc.) never goes away, so without this gate every
# later edit / label churn on a finished request re-ran the whole worker.
_SPEC_CHANGE_TERMINAL_STATUSES = {"done", "rejected"}
# Parked statuses: ``awaiting_input`` = the worker asked the human a question;
# ``awaiting_approval`` = an APR-NNNN plan approval is pending. Both wake only on
# a fresh comment (the human's answer / a plan objection); an ``updated_at`` bump
# or a status-label swap must not re-run the worker.
_SPEC_CHANGE_PARKED_STATUSES = {"awaiting_input", "awaiting_approval"}
_SPEC_CHANGE_WAKE_ACTIONS = {"handle_comment_added", "handle_comment_updated"}


@dataclass
class HandlerOutcome:
    """Result of handling one task. The scheduler completes/requeues from this.

    ``retryable`` marks failures a scheduled retry may fix (agent/script died);
    deterministic refusals (permission denied, bad payload) stay False.
    """

    success: bool
    requeue: bool = False
    error: Optional[str] = None
    detail: str = ""
    retryable: bool = False
    # Provider quota hit (every runner spec quota-blocked). The scheduler opens a
    # circuit breaker: requeue this task, park the loop until ``quota_until``, and
    # do NOT run failure recovery (no error post, no resolver - it shares the quota).
    quota: bool = False
    quota_until: Optional[str] = None


class AgentIntent:
    """The kind of agent work a work-event implies (plain string constants)."""

    SPEC_CHANGE = "spec_change"
    IMPLEMENT = "implement"
    REVIEW = "review"
    PLATFORM_ERROR = "platform_error"
    NONE = "none"


# intent -> runner function (the chain to run). merge_conflicts has no intent.
_INTENT_FUNCTION = {
    AgentIntent.SPEC_CHANGE: "spec",
    AgentIntent.IMPLEMENT: "implementation",
    AgentIntent.REVIEW: "review",
    AgentIntent.PLATFORM_ERROR: "resolve_platform_errors",
}


def _run_agent(
    ctx: ExecutionContext, prompt: str, intent: str, task_id: Any = None
) -> Any:
    """Run the prompt on the chain for ``intent``'s function.

    Works with both a per-function :class:`RunnerChains` (production) and a bare
    runner (a test double injected straight into the context). Spawned children
    are ledgered against ``task_id`` for startup orphan reclaim."""
    runner = ctx.runner
    on_start = None
    if task_id is not None:
        on_start = lambda pid, binary: inflight.record(ctx.storage, task_id, pid, binary)  # noqa: E731
    try:
        if hasattr(runner, "chain_for"):  # RunnerChains
            return runner.run(
                prompt,
                function=_INTENT_FUNCTION.get(intent, "implementation"),
                cwd=ctx.repo_root,
                cancel=ctx.cancel,
                timeout_s=ctx.agent_timeout_s,
                on_start=on_start,
                intent=intent,
            )
        return runner.run(
            prompt,
            cwd=ctx.repo_root,
            cancel=ctx.cancel,
            timeout_s=ctx.agent_timeout_s,
            on_start=on_start,
            intent=intent,
        )
    finally:
        if task_id is not None:
            inflight.clear(ctx.storage, task_id)


def _spec_change_route(entity: Any) -> Optional[str]:
    """Return the route suffix of a ``spec-change:<route>`` label, else None.

    ``spec-change:status:*`` labels are bookkeeping, not routes, so they are
    ignored.
    """
    for label in getattr(entity, "labels", []) or []:
        name = str(label)
        if not name.startswith(_SPEC_CHANGE_LABEL_PREFIX):
            continue
        if name.startswith(_SPEC_CHANGE_STATUS_PREFIX):
            continue
        route = name[len(_SPEC_CHANGE_LABEL_PREFIX):]
        if route:
            return route
    return None


def _spec_change_status(entity: Any) -> Optional[str]:
    """Return the ``spec-change:status:<state>`` suffix on the entity, if any."""
    for label in getattr(entity, "labels", []) or []:
        name = str(label)
        if name.startswith(_SPEC_CHANGE_STATUS_PREFIX):
            return name[len(_SPEC_CHANGE_STATUS_PREFIX):]
    return None


def _spec_change_actionable(status: Optional[str], action: Optional[str]) -> bool:
    """Whether a spec-change event should (re)run the worker.

    A request only runs while it is open/approved (or has no status yet). A
    terminal request never runs again. A parked request (``awaiting_input`` /
    ``awaiting_approval``) runs only when woken by a new comment (the human's answer).
    """
    if status in _SPEC_CHANGE_TERMINAL_STATUSES:
        return False
    if status in _SPEC_CHANGE_PARKED_STATUSES:
        return action in _SPEC_CHANGE_WAKE_ACTIONS
    return True


def _has_pending_spec_change_run(ctx: "ExecutionContext", post_id: Any) -> bool:
    """True if an apply.py for this request is already queued to run.

    Several distinct events (the body edit, the ``draft`` label removal) can each
    decide SPEC_CHANGE for one request before its apply.py executes. The first run
    already wrote+enqueued the script, so any later trigger should wait for it
    rather than re-running the (expensive) worker over the same request.
    """
    if post_id in (None, ""):
        return False
    try:
        rows = ctx.db.tasks_for(post_id)
    except Exception:
        return False
    for row in rows or []:
        if row.get("status") == "pending" and row.get("action") == SPEC_CHANGE_ACTION:
            return True
    return False


def decide_intent(entity: Any, state_result: Any, task: Any) -> str:
    """Decide which agent intent (if any) a work event implies.

    * a ``spec-change:<route>`` label -> SPEC_CHANGE (route = suffix).
    * an issue in ``todo``/no status that may move to ``in_progress`` -> IMPLEMENT.
    * an entity in ``in_review`` -> REVIEW.
    * otherwise -> NONE.
    """
    if entity is None:
        return AgentIntent.NONE

    labels = set(getattr(entity, "labels", []) or [])
    if "draft" in labels:
        return AgentIntent.NONE

    route = _spec_change_route(entity)
    if route is not None:
        action = task.get("action") if isinstance(task, dict) else getattr(task, "action", None)
        if _spec_change_actionable(_spec_change_status(entity), action):
            return AgentIntent.SPEC_CHANGE
        return AgentIntent.NONE

    tier = getattr(entity, "tier", None)
    status = getattr(entity, "status", None)
    next_states = list(getattr(state_result, "next_states", []) or [])

    if tier == "issue" and status in ("todo", None) and "in_progress" in next_states:
        return AgentIntent.IMPLEMENT
    if status == "in_review":
        return AgentIntent.REVIEW
    return AgentIntent.NONE


def run_spec_change_script(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Run a generated spec-change ``apply.py`` as a gated subprocess.

    Gated by ``ctx.permissions.can_run_spec_change()``. The script imports
    ``specseed_runtime...`` (the engine, which is NOT in the target repo), so it
    runs with ``cwd=ctx.repo_root`` and ``PYTHONPATH`` carrying the engine's
    ``src/`` plus the repo root; the script path is the absolute join of the
    payload's ``dir`` + ``script``. ``ctx.cancel`` and ``ctx.agent_timeout_s``
    are honored via a terminate->wait->kill loop.
    """
    if not ctx.permissions.can_run_spec_change():
        platform_log.log_event(
            "spec_change_script_blocked",
            task_id=task.get("task_id"),
            reason="permission_denied",
        )
        return HandlerOutcome(
            success=False,
            error="spec-change remote writes not permitted by config",
        )

    payload = task.get("payload") or {}
    script_dir = payload.get("dir")
    script_name = payload.get("script")
    if not script_dir or not script_name:
        return HandlerOutcome(
            success=False,
            error="spec-change payload missing dir/script",
        )
    script_path = os.path.join(str(script_dir), str(script_name))
    platform_log.log_event(
        "spec_change_script_start",
        task_id=task.get("task_id"),
        post_id=task.get("post_id"),
        script=script_path,
        cwd=str(ctx.repo_root),
    )

    env = dict(os.environ)
    repo_root = str(ctx.repo_root)
    # The engine is not copied into the target, so the generated apply.py finds
    # ``specseed_runtime`` via the engine's own src/ dir (this file: executing/ ->
    # specseed_runtime/ -> src/). Repo root stays on the path for target-local imports.
    engine_src = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH")
    parts = [engine_src, repo_root] + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    # Point default_storage_dir() at the TARGET's storage: a bare
    # resolve_remote()/Database in apply.py must not land in the engine repo.
    storage = getattr(ctx, "storage", None)
    if storage:
        env[SPECSEED_STORAGE_ENV] = str(Path(storage).resolve())

    argv = [sys.executable, script_path]
    start = time.monotonic()
    timeout_s = ctx.agent_timeout_s
    deadline = start + timeout_s if timeout_s and timeout_s > 0 else None

    try:
        proc = subprocess.Popen(
            argv,
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, ValueError) as exc:
        platform_log.log_event(
            "spec_change_script_launch_failed",
            task_id=task.get("task_id"),
            script=script_path,
            error=repr(exc),
        )
        return HandlerOutcome(
            success=False,
            error="failed to launch spec-change script: {0}".format(exc),
            retryable=True,
        )

    inflight.record(storage, task.get("task_id"), proc.pid, argv[0])
    try:
        cancelled = False
        timed_out = False
        while proc.poll() is None:
            if ctx.cancel is not None and ctx.cancel.is_set():
                cancelled = True
                _stop_process(proc)
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                _stop_process(proc)
                break
            time.sleep(_POLL_INTERVAL_S)

        try:
            stdout, stderr = proc.communicate(timeout=_GRACE_S)
        except subprocess.TimeoutExpired:
            _stop_process(proc)
            stdout, stderr = proc.communicate()
        returncode = proc.returncode
    finally:
        inflight.clear(storage, task.get("task_id"))

    if cancelled:
        platform_log.log_event(
            "spec_change_script_cancelled",
            task_id=task.get("task_id"),
            script=script_path,
            returncode=returncode,
            output=_truncate(_combine_output(stdout, stderr)),
        )
        return HandlerOutcome(
            success=False,
            requeue=True,
            error="spec-change script cancelled",
            detail=_combine_output(stdout, stderr),
        )
    if timed_out:
        platform_log.log_event(
            "spec_change_script_timed_out",
            task_id=task.get("task_id"),
            script=script_path,
            returncode=returncode,
            timeout_s=timeout_s,
            output=_truncate(_combine_output(stdout, stderr)),
        )
        return HandlerOutcome(
            success=False,
            requeue=True,
            error="spec-change script timed out after {0:.0f}s".format(timeout_s),
            detail=_combine_output(stdout, stderr),
        )
    if returncode == 0:
        output = _combine_output(stdout, stderr)
        platform_log.log_event(
            "spec_change_script_complete",
            task_id=task.get("task_id"),
            script=script_path,
            returncode=returncode,
            output=_truncate(output),
        )
        _close_finalized_request(ctx, task)
        return HandlerOutcome(success=True, detail=output)
    output = _combine_output(stdout, stderr)
    platform_log.log_event(
        "spec_change_script_failed",
        task_id=task.get("task_id"),
        script=script_path,
        returncode=returncode,
        output=_truncate(output),
    )
    return HandlerOutcome(
        success=False,
        error="spec-change script exited with code {0}\n{1}".format(
            returncode, output
        ),
        retryable=True,
    )


def _close_finalized_request(ctx: ExecutionContext, task: dict) -> None:
    """Close a spec-change request post after its approval-path apply succeeds.

    Only the finalizing apply (enqueued on approval with ``close_request``) ends the
    request; mechanical runs (clarification rounds, sprint shuffles) leave the flag
    unset and never close it. The request's lifecycle is the runtime's job, not the
    agent's, so we close in code here rather than trusting ``plan.json.closes`` to
    list the request id (it routinely omits it). Idempotent: an already-closed
    request is left alone. A close failure is logged but does not fail the run - the
    work is created; closing is bookkeeping.
    """
    payload = task.get("payload") or {}
    if not payload.get("close_request"):
        return
    request_id = payload.get("request_id") or task.get("post_id")
    if request_id is None:
        return
    try:
        res = ctx.remote.get_entry(request_id)
        data = getattr(res, "data", None)
        if data is not None and not getattr(data, "is_open", True):
            return  # already closed; nothing to do
        ctx.remote.set_entry_closed(request_id)
    except Exception as exc:
        platform_log.log_event(
            "spec_change_request_close_failed",
            task_id=task.get("task_id"),
            post_id=request_id,
            error=repr(exc),
        )
        return
    platform_log.log_event(
        "spec_change_request_closed",
        task_id=task.get("task_id"),
        post_id=request_id,
    )


def _stop_process(proc: "subprocess.Popen[Any]") -> None:
    try:
        proc.terminate()
    except (OSError, ValueError):
        return
    try:
        proc.wait(timeout=_GRACE_S)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except (OSError, ValueError):
            pass


def _combine_output(stdout: Optional[str], stderr: Optional[str]) -> str:
    parts = []
    if stdout:
        parts.append(stdout.strip())
    if stderr:
        parts.append(stderr.strip())
    return "\n".join(p for p in parts if p)


def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _run_platform_error(
    ctx: ExecutionContext, task: dict, reason: Optional[str] = None
) -> HandlerOutcome:
    """Engage the resolve agent on a platform_error post.

    Reads the post fresh from the remote (it is runtime-owned state, not a work
    entity); a closed or vanished post means the human cancelled - success no-op.
    """
    post_id = task.get("post_id")
    payload = task.get("payload") or {}
    reason = reason or str(payload.get("reason") or "new")
    result = ctx.remote.get_entry(post_id)
    if not getattr(result, "ok", False) or result.data is None:
        return HandlerOutcome(
            success=True, detail="error post {0!r} gone; nothing to resolve".format(post_id)
        )
    post = result.data
    if not _entry_is_open(post):
        return HandlerOutcome(
            success=True, detail="error post {0!r} closed; resolution cancelled".format(post_id)
        )

    prompt = prompts.build_platform_error_prompt(post, payload, reason, ctx)
    agent_result = _run_agent(
        ctx, prompt, AgentIntent.PLATFORM_ERROR, task_id=task.get("task_id")
    )
    platform_log.log_event(
        "platform_error_agent_result",
        task_id=task.get("task_id"),
        error_post_id=post_id,
        reason=reason,
        ok=getattr(agent_result, "ok", False),
        returncode=getattr(agent_result, "returncode", None),
        duration_s=getattr(agent_result, "duration_s", None),
    )
    if getattr(agent_result, "quota_exhausted", False):
        # The resolver shares the provider quota. Don't burn it - requeue and let
        # the scheduler park until reset (this was the "doctor has the disease" loop).
        return HandlerOutcome(
            success=False,
            requeue=True,
            quota=True,
            quota_until=getattr(agent_result, "quota_reset_hint", None),
            error=getattr(agent_result, "error", None) or "provider quota exhausted",
            detail="resolve agent parked: provider quota",
        )
    if getattr(agent_result, "ok", False):
        return HandlerOutcome(success=True, detail="resolve agent reported ({0})".format(reason))
    # NOT retryable: recovery never recovers itself (guarded there too).
    return HandlerOutcome(
        success=False,
        error=getattr(agent_result, "error", None) or "resolve agent failed",
        detail="resolve agent failed ({0})".format(reason),
    )


def _entry_is_open(post: Any) -> bool:
    value = getattr(post, "is_open", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "")
    return bool(value)


def _primary_branch(ctx: ExecutionContext) -> str:
    return (getattr(ctx, "config", {}) or {}).get("specseed_primary_branch") or "main"


def _prepare_git_branch(ctx: ExecutionContext, entity: Any, intent: str) -> Optional[str]:
    """Runtime owns git: put the work on the issue's branch before the agent runs.

    Returns the branch name (None for non-code intents). The branch is created off
    the primary branch the first time and reused after (review bounce continues the
    same branch). Best-effort + logged; a git hiccup never aborts the work.
    """
    if intent not in (AgentIntent.IMPLEMENT, AgentIntent.REVIEW):
        return None
    branch = git_ops.branch_name(entity)
    res = git_ops.ensure_on_branch(ctx.repo_root, branch, _primary_branch(ctx))
    platform_log.log_event(
        "git_branch_prepared",
        post_id=getattr(entity, "post_id", None),
        intent=intent,
        branch=branch,
        ok=res.ok,
        actions=res.actions,
        error=res.error,
    )
    return branch


def _finalize_git_branch(ctx: ExecutionContext, entity: Any, intent: str, branch: Optional[str]) -> None:
    """Commit the agent's work (implement) and always return to the primary branch.

    Commits even a partial WIP (interruption) so nothing is lost. Review runs do
    not commit (the reviewer only reads). Best-effort + logged.
    """
    if branch is None:
        return
    if intent == AgentIntent.IMPLEMENT:
        message = "specseed: {0}".format(getattr(entity, "title", None) or "work on issue {0}".format(
            getattr(entity, "post_id", "?")))
        commit = git_ops.commit_all(ctx.repo_root, message)
        platform_log.log_event(
            "git_commit",
            post_id=getattr(entity, "post_id", None),
            branch=branch,
            ok=commit.ok,
            detail=commit.detail,
            error=commit.error,
        )
    back = git_ops.checkout(ctx.repo_root, _primary_branch(ctx))
    platform_log.log_event(
        "git_return_primary",
        post_id=getattr(entity, "post_id", None),
        branch=branch,
        ok=back.ok,
        error=back.error,
    )


def _run_work(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Common path for every work handler: load entity, judge state, run agent."""
    post_id = task.get("post_id")
    entity, conversation = context_mod.load_entity(ctx, post_id)
    if entity is None:
        platform_log.log_event(
            "work_no_entity",
            task_id=task.get("task_id"),
            action=task.get("action"),
            post_id=post_id,
        )
        return HandlerOutcome(
            success=True,
            detail="no entity for post {0!r}; nothing to do".format(post_id),
        )

    # Error posts are runtime-owned: a human reply re-engages the resolve agent;
    # every other event about them is bookkeeping, never normal work.
    labels = {str(label) for label in getattr(entity, "labels", []) or []}
    if recovery.PLATFORM_ERROR_LABEL in labels:
        if task.get("action") == "handle_comment_added":
            return _run_platform_error(ctx, task, reason="reply")
        return HandlerOutcome(
            success=True, detail="platform_error post bookkeeping; no work"
        )

    state_result = evaluate_entity_state(entity, ctx.config, conversation)
    intent = decide_intent(entity, state_result, task)
    platform_log.log_event(
        "work_intent_decided",
        task_id=task.get("task_id"),
        action=task.get("action"),
        post_id=post_id,
        tier=getattr(entity, "tier", None),
        status=getattr(entity, "status", None),
        intent=intent,
        next_states=list(getattr(state_result, "next_states", []) or []),
    )

    # A spec-change REQUEST parked awaiting_approval is finalized deterministically:
    # an approval settles its spec docs (plan.json.settle_docs) and moves it to done; a
    # rejection moves it to rejected. No agent run. Only when it is NOT an approval (a
    # wake comment that is a clarification answer) do we fall through and re-run the worker.
    if _spec_change_route(entity) is not None and _spec_change_status(entity) == "awaiting_approval":
        settled = advance.resolve_spec_change_request(ctx, entity, state_result)
        if settled is not None:
            platform_log.log_event(
                "spec_change_request_resolved",
                task_id=task.get("task_id"),
                post_id=post_id,
                detail=settled,
            )
            return HandlerOutcome(success=True, detail=settled)

    # Approval gates are resolved in code, with no agent run: an approver's
    # `approve <id>` comment moves an awaiting_approval entity forward.
    # Spec-change REQUESTS are excluded: their `spec-change:status:awaiting_approval`
    # label also reads as status "awaiting_approval" (the `:status:` infix), but their
    # approvals are resolved above, and a non-approval wake comment must fall through
    # to decide_intent so the worker re-runs with the human's answer.
    action = task.get("action")
    if _spec_change_route(entity) is None and getattr(entity, "status", None) == "awaiting_approval":
        resolved = advance.resolve_approval(ctx, entity, state_result, conversation, action)
        platform_log.log_event(
            "approval_resolved",
            task_id=task.get("task_id"),
            post_id=post_id,
            detail=resolved or "awaiting_approval; no approver yet",
        )
        return HandlerOutcome(
            success=True,
            detail=resolved or "awaiting_approval; no approver yet",
        )

    # A blocked issue is not dead: a human can approve it through (force done) or
    # hand the implementer guidance to retry. Only fires when something was applied;
    # otherwise fall through to the no-action path.
    if _spec_change_route(entity) is None and getattr(entity, "status", None) == "blocked":
        resolved = advance.resolve_blocked(ctx, entity, state_result, conversation, action)
        if resolved is not None:
            platform_log.log_event(
                "blocked_resolved",
                task_id=task.get("task_id"),
                post_id=post_id,
                detail=resolved,
            )
            return HandlerOutcome(success=True, detail=resolved)

    if intent == AgentIntent.NONE:
        platform_log.log_event(
            "work_no_action",
            task_id=task.get("task_id"),
            post_id=post_id,
            tier=getattr(entity, "tier", None),
            status=getattr(entity, "status", None),
        )
        return HandlerOutcome(
            success=True,
            detail="no actionable intent (tier={0}, status={1})".format(
                getattr(entity, "tier", None), getattr(entity, "status", None)
            ),
        )

    # Several queued events can describe one entity in a single drain (a status
    # swap is a label remove + add). The local snapshot is stale, so skip the
    # expensive agent run if the remote shows the entity already moved on.
    if intent in (AgentIntent.IMPLEMENT, AgentIntent.REVIEW) and advance._is_stale(ctx, entity):
        platform_log.log_event(
            "work_stale_skipped",
            task_id=task.get("task_id"),
            post_id=post_id,
            intent=intent,
            status=getattr(entity, "status", None),
        )
        return HandlerOutcome(
            success=True,
            detail="stale {0} event; remote already advanced past {1}".format(
                intent, getattr(entity, "status", None)
            ),
        )

    # Platform gate: a ready issue parks for human sign-off unless auto_implement_issue
    # is on, or an approver has already approved it (which moved it back to todo).
    if intent == AgentIntent.IMPLEMENT and not ctx.permissions.auto_implement_issue():
        if not getattr(state_result, "approved_by", None):
            detail = advance.park_for_implement_approval(ctx, entity)
            platform_log.log_event(
                "implement_approval_required",
                task_id=task.get("task_id"),
                post_id=post_id,
                detail=detail,
            )
            return HandlerOutcome(success=True, detail=detail)

    if intent == AgentIntent.SPEC_CHANGE and _has_pending_spec_change_run(ctx, post_id):
        platform_log.log_event(
            "spec_change_run_pending_skip",
            task_id=task.get("task_id"),
            post_id=post_id,
        )
        return HandlerOutcome(
            success=True,
            detail="apply.py already queued for request {0}; skipping re-run".format(post_id),
        )

    if intent == AgentIntent.SPEC_CHANGE:
        route = _spec_change_route(entity)
        request_id = getattr(entity, "post_id", None)
        prompt = prompts.build_spec_change_prompt(route, request_id, entity, ctx)
    elif intent == AgentIntent.IMPLEMENT:
        prompt = prompts.build_implement_prompt(entity, ctx)
    else:  # REVIEW
        prompt = prompts.build_review_prompt(entity, ctx)

    # Runtime owns git: branch before the agent edits anything.
    branch = _prepare_git_branch(ctx, entity, intent)
    result = _run_agent(ctx, prompt, intent, task_id=task.get("task_id"))
    # Commit the work (implement) and always return to the primary branch -
    # regardless of how the run ended, so WIP is never stranded on a feature branch.
    _finalize_git_branch(ctx, entity, intent, branch)
    platform_log.log_event(
        "agent_result",
        task_id=task.get("task_id"),
        post_id=post_id,
        intent=intent,
        ok=getattr(result, "ok", False),
        returncode=getattr(result, "returncode", None),
        killed=getattr(result, "killed", False),
        timed_out=getattr(result, "timed_out", False),
        duration_s=getattr(result, "duration_s", None),
        error=getattr(result, "error", None),
        stdout_chars=len(getattr(result, "stdout", "") or ""),
    )

    # Provider quota: every runner spec was quota-blocked. Not a task-local
    # failure - requeue and let the scheduler park the loop. requeue (not
    # complete-as-failed) means recovery never fires: no error post, no resolver.
    if getattr(result, "quota_exhausted", False):
        platform_log.log_event(
            "work_quota_exhausted",
            task_id=task.get("task_id"),
            post_id=post_id,
            intent=intent,
            quota_until=getattr(result, "quota_reset_hint", None),
        )
        return HandlerOutcome(
            success=False,
            requeue=True,
            quota=True,
            quota_until=getattr(result, "quota_reset_hint", None),
            error=getattr(result, "error", None) or "provider quota exhausted",
            detail="intent {0} parked: provider quota".format(intent),
        )

    if getattr(result, "ok", False):
        # rc==0 is not enough: implement/review MUST have written a valid result
        # file. A missing/invalid one means the run did not really finish - retry
        # (a fresh run; agent runs are stateless, so re-asking for just the JSON
        # is impossible). This catches the "Blocked: cannot make changes", exit 0
        # case that used to advance straight to review.
        if intent in (AgentIntent.IMPLEMENT, AgentIntent.REVIEW) and getattr(result, "report", None) is None:
            return HandlerOutcome(
                success=False,
                error="agent finished but did not report a valid result file: {0}".format(
                    getattr(result, "report_error", None) or "missing"
                ),
                detail="intent {0} missing result file".format(intent),
                retryable=True,
            )
        detail = "agent ran intent {0}".format(intent)
        if intent in (AgentIntent.IMPLEMENT, AgentIntent.REVIEW):
            try:
                transition = advance.apply_post_work_transition(
                    ctx, entity, intent, result, state_result, conversation
                )
                detail = "{0}; {1}".format(detail, transition)
            except Exception as exc:  # never lose the successful run over a write hiccup
                detail = "{0}; transition failed: {1!r}".format(detail, exc)
        return HandlerOutcome(success=True, detail=detail)
    if getattr(result, "killed", False) or getattr(result, "timed_out", False):
        return HandlerOutcome(
            success=False,
            requeue=True,
            error=getattr(result, "error", None) or "agent interrupted",
            detail="intent {0} interrupted".format(intent),
        )
    # Keep the agent's last output in the recorded error: "exited with code 1"
    # alone is undiagnosable.
    error = getattr(result, "error", None) or "agent run failed"
    tail = stdout_tail(getattr(result, "stdout", "") or "")
    if tail:
        error = "{0}\n{1}".format(error, tail)
    return HandlerOutcome(
        success=False,
        error=error,
        detail="intent {0} failed".format(intent),
        retryable=True,
    )


def _read_plan(ctx: ExecutionContext, request_id: Any) -> Optional[dict]:
    path = spec_change_dir(str(request_id), ctx.storage) / "plan.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _request_labels(post: Any) -> list[str]:
    return [str(getattr(lbl, "name", lbl)) for lbl in getattr(post, "labels", []) or []]


def _park_request_awaiting_approval(ctx: ExecutionContext, request_id: Any, labels: list[str]) -> None:
    """Swap the request's ``spec-change:status:*`` to ``awaiting_approval`` (idempotent)."""
    target = _SPEC_CHANGE_STATUS_PREFIX + "awaiting_approval"
    for name in labels:
        if name.startswith(_SPEC_CHANGE_STATUS_PREFIX) and name != target:
            ctx.remote.remove_entry_label(request_id, name)
    if target not in labels:
        ctx.remote.add_entry_label(request_id, target)


def propose_spec_change(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Plan-first gate: post the plan summary + approval request, park the request.

    Deterministic, no agent, NO remote work posts. Reads the request's
    ``plan.json`` (``plan_summary`` + ``apr``), posts both onto the request post,
    and parks it ``awaiting_approval``. The deferred ``apply.py`` - which actually
    creates the epics/tickets/issues - runs only after a human approves
    (``advance.resolve_spec_change_request`` enqueues it). Idempotent: a re-trigger
    that finds the ``APR-NNNN`` request already posted is a success no-op.
    """
    if not ctx.permissions.can_run_spec_change():
        platform_log.log_event("spec_change_propose_blocked", task_id=task.get("task_id"), reason="permission_denied")
        return HandlerOutcome(success=False, error="spec-change remote writes not permitted by config")

    request_id = task.get("post_id") or (task.get("payload") or {}).get("request_id")
    plan = _read_plan(ctx, request_id)
    if plan is None:
        return HandlerOutcome(success=False, error="propose: no readable plan.json for request {0}".format(request_id))
    apr = plan.get("apr") if isinstance(plan.get("apr"), dict) else {}
    apr_id = str(apr.get("id") or "").strip()
    if not apr_id:
        return HandlerOutcome(success=False, error="propose: plan.json missing apr.id (no approval token to gate on)")
    summary = str(apr.get("summary") or "")
    plan_summary = str(plan.get("plan_summary") or "").strip()

    result = ctx.remote.get_entry(request_id)
    post = getattr(result, "data", None)
    if not getattr(result, "ok", False) or post is None:
        return HandlerOutcome(success=False, error="propose: request {0} not found on remote".format(request_id), retryable=True)
    labels = _request_labels(post)
    conversation = list(getattr(post, "comments", []) or [])

    # Idempotency: the approval-request comment carries the APR marker. If it is
    # already on the post, a previous propose run finished - do nothing.
    if apr_id.upper() in {i.upper() for i in requested_apr_ids(conversation)}:
        platform_log.log_event("spec_change_propose_noop", task_id=task.get("task_id"), post_id=request_id, apr=apr_id)
        return HandlerOutcome(success=True, detail="proposal {0} already posted; parked".format(apr_id))

    if plan_summary:
        ctx.remote.add_entry_comment(request_id, platform_comment(plan_summary))
    ctx.remote.add_entry_comment(request_id, approval_request_comment(apr_id, summary))
    _park_request_awaiting_approval(ctx, request_id, labels)
    platform_log.log_event(
        "spec_change_proposed", task_id=task.get("task_id"), post_id=request_id, apr=apr_id,
        creates=len(plan.get("creates") or []),
    )
    return HandlerOutcome(success=True, detail="proposed {0}; parked awaiting approval".format(apr_id))


def dispatch(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Route a claimed task to its handler and return the outcome."""
    action = task.get("action")
    platform_log.log_event(
        "dispatch_start",
        task_id=task.get("task_id"),
        action=action,
        post_id=task.get("post_id"),
    )

    # A queued retry whose error post a human closed must not run again.
    if recovery.retry_cancelled(ctx.remote, task):
        platform_log.log_event(
            "retry_cancelled_by_closed_error_post",
            task_id=task.get("task_id"),
            action=action,
            post_id=task.get("post_id"),
        )
        return HandlerOutcome(
            success=True,
            detail="retry cancelled: error post closed by human",
        )

    if action == SPEC_CHANGE_ACTION:
        return run_spec_change_script(ctx, task)

    if action == SPEC_CHANGE_PROPOSE_ACTION:
        return propose_spec_change(ctx, task)

    if action == recovery.PLATFORM_ERROR_ACTION:
        return _run_platform_error(ctx, task)

    if action == CLEANUP_ACTION:
        payload = task.get("payload") or {}
        reason = payload.get("reason")
        interrupted = payload.get("interrupted_task_id")
        platform_log.log_event(
            "cleanup_recorded",
            task_id=task.get("task_id"),
            post_id=task.get("post_id"),
            reason=reason,
            interrupted_task_id=interrupted,
        )
        return HandlerOutcome(
            success=True,
            detail="cleanup recorded (reason={0}, interrupted_task_id={1})".format(
                reason, interrupted
            ),
        )

    if action in _WORK_ACTIONS:
        return _run_work(ctx, task)

    platform_log.log_event("dispatch_unknown_action", task_id=task.get("task_id"), action=action)
    return HandlerOutcome(success=False, error="unknown action: {0!r}".format(action))


_WORK_ACTIONS = {
    "handle_entry_created",
    "handle_entry_updated",
    "handle_entry_reopened",
    "handle_label_added",
    "handle_label_removed",
    "handle_comment_added",
    "handle_comment_updated",
    "handle_reaction_added",
    "handle_reaction_removed",
    "handle_entry_reaction_added",
    "handle_entry_reaction_removed",
}
