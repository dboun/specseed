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
import re
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
from specseed_runtime.executing import priorities
from specseed_runtime.executing import work_lane
from specseed_runtime.executing.context import ExecutionContext
from specseed_runtime.executing import prompts
from specseed_runtime.db.database import LANE_WORK
from specseed_runtime.scheduling.spec_change import (
    DEFAULT_SCRIPT_NAME,
    SPEC_CHANGE_ACTION,
    SPEC_CHANGE_PROPOSE_ACTION,
    enqueue_spec_change_propose,
    enqueue_spec_change_run,
    spec_change_dir,
    staged_spec_files,
)
from specseed_runtime.state_machines.approvals import (
    approval_request_comment,
    requested_apr_ids,
)
from specseed_runtime.state_machines.base import evaluate_entity_state
from specseed_runtime.platform_identity import platform_comment
from specseed_runtime import storage_paths
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
    # When requeued, delay re-running by this many seconds (db not_before). Used by
    # the dependency gate to re-check a held issue next poll instead of busy-looping.
    requeue_after_s: Optional[float] = None
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
    ASK = "ask"
    MERGE_CONFLICTS = "merge_conflicts"
    PLATFORM_ERROR = "platform_error"
    NONE = "none"


# intent -> runner function (the chain to run).
_INTENT_FUNCTION = {
    AgentIntent.SPEC_CHANGE: "spec",
    AgentIntent.IMPLEMENT: "implementation",
    AgentIntent.REVIEW: "review",
    AgentIntent.ASK: "ask",
    AgentIntent.MERGE_CONFLICTS: "merge_conflicts",
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
    live_log = None
    if task_id is not None:
        on_start = lambda pid, binary: inflight.record(ctx.storage, task_id, pid, binary)  # noqa: E731
        # Live stdout feed for the UI's 'Agent output' popup, keyed by work task.
        # Ephemeral + best-effort: prune the window, then point the runner at the file.
        try:
            storage_paths.prune_agent_output(ctx.storage)
            live_log = str(storage_paths.agent_output_file(task_id, ctx.storage))
        except OSError:
            live_log = None
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
                live_log=live_log,
            )
        return runner.run(
            prompt,
            cwd=ctx.repo_root,
            cancel=ctx.cancel,
            timeout_s=ctx.agent_timeout_s,
            on_start=on_start,
            intent=intent,
            live_log=live_log,
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
    """True if a follow-up for this request is already queued (apply.py OR a proposal).

    Several distinct events (the body edit, the ``draft`` label removal) can each
    decide SPEC_CHANGE for one request before the runtime's follow-up executes. The
    first run already wrote the files and the runtime enqueued the follow-up - a
    proposal (``propose_spec_change``, which parks the request) or a direct apply
    (``run_spec_change_script``). Either way any later trigger should wait for it
    rather than re-running the (expensive) worker over the same request.
    """
    if post_id in (None, ""):
        return False
    try:
        rows = ctx.db.tasks_for(post_id)
    except Exception:
        return False
    follow_up = {SPEC_CHANGE_ACTION, SPEC_CHANGE_PROPOSE_ACTION}
    for row in rows or []:
        if row.get("status") == "pending" and row.get("action") in follow_up:
            return True
    return False


def _has_pending_work_job(ctx: "ExecutionContext", post_id: Any, action: str) -> bool:
    """True if a work job of ``action`` for this post is already queued/running.

    Several control events can describe one entity in a drain (a status swap is a
    label remove + add). Each could schedule the same heavy job. Supersession +
    the work-side staleness check would collapse the extras, but skipping here
    avoids piling identical jobs on the work lane in the first place.
    """
    if post_id in (None, ""):
        return False
    try:
        rows = ctx.db.tasks_for(post_id)
    except Exception:
        return False
    for row in rows or []:
        if row.get("action") == action and row.get("status") in ("pending", "in_progress"):
            return True
    return False


def _schedule_work_run(ctx: "ExecutionContext", entity: Any, intent: str, task: dict) -> HandlerOutcome:
    """Hand a heavy agent run (implement/review/spec_change) to the WORK lane.

    Control decided the intent and passed every gate; the work lane builds the
    prompt and runs the agent. Idempotent against repeated control events for one
    entity (``_has_pending_work_job``).
    """
    post_id = getattr(entity, "post_id", None) or task.get("post_id")
    if _has_pending_work_job(ctx, post_id, work_lane.WORK_RUN):
        platform_log.log_event("work_run_already_scheduled", post_id=post_id, intent=intent)
        return HandlerOutcome(success=True, detail="work_run already scheduled for {0}".format(post_id))
    work_id = ctx.db.enqueue(
        work_lane.WORK_RUN,
        post_id=post_id,
        payload={"intent": intent, "post_id": str(post_id) if post_id is not None else None},
        lane=LANE_WORK,
        priority=priorities.WORK_DEFAULT,
    )
    platform_log.log_event("work_run_scheduled", work_task_id=work_id, post_id=post_id, intent=intent)
    return HandlerOutcome(success=True, detail="scheduled {0} on the work lane (job {1})".format(intent, work_id))


def _schedule_gate_action(ctx: "ExecutionContext", entity: Any, transition: Any, task: dict) -> str:
    """Hand a branch ready+gate / merge (git, possibly the conflict agent) to the WORK lane.

    The brain (advance.resolve_* / apply_post_work_transition) already applied the
    remote state + acks; only the git muscle is deferred. Returns a human note. A
    transition with no prepare/merge schedules nothing.
    """
    detail = getattr(transition, "detail", None)
    if not (getattr(transition, "prepare", False) or getattr(transition, "merge", False)):
        return detail
    post_id = getattr(entity, "post_id", None) or task.get("post_id")
    if _has_pending_work_job(ctx, post_id, work_lane.WORK_GATE_ACTION):
        platform_log.log_event("gate_action_already_scheduled", post_id=post_id)
        note = "merge already scheduled"
        return "{0}; {1}".format(detail, note) if detail else note
    work_id = ctx.db.enqueue(
        work_lane.WORK_GATE_ACTION,
        post_id=post_id,
        payload={
            "post_id": str(post_id) if post_id is not None else None,
            "prepare": bool(getattr(transition, "prepare", False)),
            "merge": bool(getattr(transition, "merge", False)),
        },
        lane=LANE_WORK,
        priority=priorities.WORK_MERGE,
    )
    platform_log.log_event(
        "gate_action_scheduled", work_task_id=work_id, post_id=post_id,
        prepare=bool(getattr(transition, "prepare", False)), merge=bool(getattr(transition, "merge", False)),
    )
    note = "merge readying scheduled on the work lane (job {0})".format(work_id)
    return "{0}; {1}".format(detail, note) if detail else note


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

    # An `ask` post is a read-only Q&A thread: answer on the label add, and re-answer
    # each time a human adds a comment (the platform's own answer is dropped by
    # sync_to_db, so it never re-triggers itself). It stays open; the human closes it.
    if "ask" in labels:
        action = task.get("action") if isinstance(task, dict) else getattr(task, "action", None)
        if action in ("handle_label_added", "handle_comment_added"):
            return AgentIntent.ASK
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


def _specseed_dir(ctx: ExecutionContext) -> str:
    return str((getattr(ctx, "config", {}) or {}).get("specseed_dir") or ".specseed")


def _prepare_git_branch(
    ctx: ExecutionContext, entity: Any, intent: str
) -> tuple[Optional[str], Optional[str]]:
    """Runtime owns git: put the work on the issue's branch before the agent runs.

    Returns (branch, error). A git hiccup must stop the agent: running on the wrong
    branch is worse than retrying with a visible platform error.
    """
    if intent not in (AgentIntent.IMPLEMENT, AgentIntent.REVIEW):
        return None, None
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
    if not res.ok:
        return branch, (res.error or "git branch preparation failed")
    return branch, None


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
        commit = git_ops.commit_all(ctx.repo_root, message, exclude_paths=[_specseed_dir(ctx)])
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


def _merge_comment(ctx: ExecutionContext, post_id: Any, body: str) -> None:
    try:
        ctx.remote.add_entry_comment(post_id, platform_comment(body, getattr(ctx, "config", None)))
    except Exception:
        pass  # a comment hiccup never aborts the merge bookkeeping


def _execute_merge(ctx: ExecutionContext, entity: Any, branch: Optional[str], task: dict) -> str:
    """Run an AUTHORIZED issue-branch merge into primary, then settle the issue.

    The caller already decided the merge may run (``merge_to_primary`` on, or a human
    approved the merge gate) - there is no permission gate here. Clean merge -> close
    the issue done. Conflict -> resolver agent (edits only; runtime completes); on
    success close done. Anything that needs a human (non-conflict failure, or a
    conflict the agent could not resolve) -> park ``blocked`` with the branch intact;
    the issue is NEVER left done/closed with code still off primary.
    """
    post_id = getattr(entity, "post_id", None)
    primary = _primary_branch(ctx)
    if branch is None:
        return advance.close_issue_done(ctx, entity)

    res = git_ops.merge(ctx.repo_root, branch, primary)
    platform_log.log_event(
        "git_merge", post_id=post_id, branch=branch, primary=primary,
        ok=res.ok, conflicted=res.conflicted, files=res.files, error=res.error,
    )
    if res.ok:
        _merge_comment(ctx, post_id, "Merged branch `{0}` into `{1}`.".format(branch, primary))
        closed = advance.close_issue_done(ctx, entity)
        _reprepare_after_primary_change(ctx, post_id, task)
        return "merged {0} -> {1}; {2}".format(branch, primary, closed)
    if not res.conflicted:
        advance.park_unmerged(ctx, entity)
        _merge_comment(
            ctx, post_id,
            "Could not merge branch `{0}` into `{1}`: {2}. Parked `blocked`; the branch is "
            "intact for a human.".format(branch, primary, res.error or "unknown error"),
        )
        return "merge of {0} failed: {1}; parked blocked".format(branch, res.error)

    # Conflict -> the merge-conflicts agent (edits only; runtime completes). Should be
    # rare now: the gate is only opened after a clean prepare, so this is a backstop
    # for a primary move between prepare and merge.
    prompt = prompts.build_merge_conflict_prompt(entity, branch, primary, res.files, ctx, direction="merge")
    resolver = _run_agent(ctx, prompt, AgentIntent.MERGE_CONFLICTS, task_id=task.get("task_id"))
    complete = (
        git_ops.complete_merge(ctx.repo_root, exclude_paths=[_specseed_dir(ctx)])
        if getattr(resolver, "ok", False)
        else git_ops.GitResult(ok=False, error="resolver agent did not finish")
    )
    platform_log.log_event(
        "git_merge_conflict_resolution", post_id=post_id, branch=branch,
        resolver_ok=getattr(resolver, "ok", False), completed=complete.ok, error=complete.error,
    )
    if complete.ok:
        _merge_comment(
            ctx, post_id,
            "Merged branch `{0}` into `{1}` after auto-resolving conflicts.".format(branch, primary),
        )
        closed = advance.close_issue_done(ctx, entity)
        _reprepare_after_primary_change(ctx, post_id, task)
        return "merged {0} -> {1} after conflict resolution; {2}".format(branch, primary, closed)
    git_ops.abort_merge(ctx.repo_root)
    advance.park_unmerged(ctx, entity)
    _merge_comment(
        ctx, post_id,
        "Tried to merge branch `{0}` into `{1}` but hit conflicts I could not resolve "
        "automatically. Aborted the merge (branch intact) and parked `blocked` for a "
        "human.".format(branch, primary),
    )
    return "merge conflict on {0} unresolved; aborted, parked blocked".format(branch)


def _resolve_prepare_conflict(
    ctx: ExecutionContext, entity: Any, branch: str, primary: str, files: list, task: dict
) -> bool:
    """Run the merge-conflicts agent on a branch mid-prepare (primary->branch). Returns
    True if the conflicts were resolved and the prepare merge committed (left on primary)."""
    post_id = getattr(entity, "post_id", None)
    prompt = prompts.build_merge_conflict_prompt(entity, branch, primary, files, ctx, direction="prepare")
    resolver = _run_agent(ctx, prompt, AgentIntent.MERGE_CONFLICTS, task_id=task.get("task_id"))
    complete = (
        git_ops.complete_merge(ctx.repo_root, exclude_paths=[_specseed_dir(ctx)])
        if getattr(resolver, "ok", False)
        else git_ops.GitResult(ok=False, error="resolver agent did not finish")
    )
    platform_log.log_event(
        "git_prepare_conflict_resolution", post_id=post_id, branch=branch,
        resolver_ok=getattr(resolver, "ok", False), completed=complete.ok, error=complete.error,
    )
    if complete.ok:
        git_ops.checkout(ctx.repo_root, primary)  # leave on primary, branch readied
        return True
    git_ops.abort_merge(ctx.repo_root)
    return False


def _prepare_and_gate(
    ctx: ExecutionContext, entity: Any, transition: Any, branch: Optional[str], task: dict
) -> str:
    """Ready the issue branch (bring primary in, resolve conflicts), then open a merge
    gate - or, when ``transition.merge`` is also set, merge straight through.

    A branch that cannot be readied parks ``blocked`` with no gate (the human never
    approves a merge that cannot run)."""
    post_id = getattr(entity, "post_id", None)
    primary = _primary_branch(ctx)
    if branch is None:
        branch = _merge_branch_for(entity)
    prep = git_ops.prepare_merge(ctx.repo_root, branch, primary)
    platform_log.log_event(
        "git_prepare_merge", post_id=post_id, branch=branch, primary=primary,
        ok=prep.ok, conflicted=prep.conflicted, files=prep.files, error=prep.error,
    )
    if prep.conflicted:
        if not _resolve_prepare_conflict(ctx, entity, branch, primary, prep.files, task):
            git_ops.checkout(ctx.repo_root, primary)
            advance.park_unmerged(ctx, entity)
            _merge_comment(
                ctx, post_id,
                "Could not ready branch `{0}` for merge into `{1}`: conflicts I could not "
                "resolve automatically. Parked `blocked`; the branch is intact for you.".format(
                    branch, primary
                ),
            )
            return "prepare of {0} unresolved; parked blocked".format(branch)
    elif not prep.ok:
        git_ops.checkout(ctx.repo_root, primary)
        advance.park_unmerged(ctx, entity)
        _merge_comment(
            ctx, post_id,
            "Could not ready branch `{0}` for merge into `{1}`: {2}. Parked `blocked`; the "
            "branch is intact for you.".format(branch, primary, prep.error or "unknown error"),
        )
        return "prepare of {0} failed: {1}; parked blocked".format(branch, prep.error)
    # Readied clean (repo back on primary).
    if transition.merge:
        return _execute_merge(ctx, entity, branch, task)
    return advance.open_merge_gate_ready(ctx, entity)


def _open_merge_gate_issue_ids(ctx: ExecutionContext, exclude: Any) -> list[str]:
    """Open issues parked at a merge gate (awaiting_approval + a merge-gate comment)."""
    from specseed_runtime.entities.entity_base import Entity

    res = ctx.remote.list_entries(is_open=True)
    out: list[str] = []
    for summary in getattr(res, "data", None) or []:
        sid = str(getattr(summary, "id", "") or "")
        if not sid or sid == str(exclude):
            continue
        names = [str(getattr(lbl, "name", lbl)) for lbl in getattr(summary, "labels", []) or []]
        if Entity.tier_from_labels(names) != "issue":
            continue
        if Entity.status_from_labels(names) != "awaiting_approval":
            continue
        _ent, conv = context_mod.load_entity(ctx, sid)
        if advance._latest_marker_comment(conv, advance.MERGE_GATE_MARKER) is not None:
            out.append(sid)
    return out


def _reprepare_after_primary_change(ctx: ExecutionContext, merged_id: Any, task: dict) -> None:
    """Primary just moved: invalidate every other open merge gate and re-ready it.

    A standing approval on an old gate is dead once we post a fresh gate comment; each
    sibling branch is re-merged against the new primary (merge-conflicts agent on a
    conflict) so its gate only stands if it STILL merges clean. Best-effort: one
    sibling's trouble never aborts the merge that triggered this."""
    try:
        ids = _open_merge_gate_issue_ids(ctx, merged_id)
    except Exception as exc:
        platform_log.log_event("reprepare_scan_error", merged_id=str(merged_id), error=repr(exc))
        return
    primary = _primary_branch(ctx)
    for sid in ids:
        try:
            sib, _conv = context_mod.load_entity(ctx, sid)
            if sib is None:
                continue
            _merge_comment(
                ctx, sid,
                "`{0}` moved on. Re-checking whether this branch still merges clean before "
                "its approval stands.".format(primary),
            )
            _prepare_and_gate(
                ctx, sib, advance.WorkTransition("reprepare after primary change", prepare=True),
                None, task,
            )
        except Exception as exc:
            platform_log.log_event("reprepare_error", post_id=sid, error=repr(exc))


def _merge_branch_for(entity: Any) -> str:
    """The git branch name an issue's work lives on (pure; no git calls)."""
    return git_ops.branch_name(entity)


def _run_gate_action(
    ctx: ExecutionContext, entity: Any, transition: Any, branch: Optional[str], task: dict
) -> Optional[str]:
    """Run the git side of a transition: ready+gate/merge (``prepare``) or a direct
    merge (``merge``). Returns a human note, or None if there is nothing to run."""
    if transition.prepare:
        return _prepare_and_gate(ctx, entity, transition, branch, task)
    if transition.merge:
        return _execute_merge(ctx, entity, branch, task)
    return None


def _run_gate_merge(ctx: ExecutionContext, entity: Any, transition: Any, task: dict) -> Optional[str]:
    """Fold an authorized prepare/merge into the transition detail.

    advance owns remote STATE and decides whether the branch may ready/merge now;
    dispatch owns git and runs it here. The branch name is recomputed
    (``_merge_branch_for``) since the gate-resolution path has no live ``branch`` var -
    it is the same deterministic name the implement run used. Best-effort: a git hiccup
    is a note, never reopens the resolved gate."""
    detail = transition.detail
    if not (transition.prepare or transition.merge):
        return detail
    try:
        note = _run_gate_action(ctx, entity, transition, _merge_branch_for(entity), task)
    except Exception as exc:
        return "{0}; merge failed: {1!r}".format(detail, exc) if detail else "merge failed: {0!r}".format(exc)
    if not note:
        return detail
    return "{0}; {1}".format(detail, note) if detail else note


def _dep_status(ctx: ExecutionContext, dep_id: str) -> Optional[str]:
    dep_entity, _ = context_mod.load_entity(ctx, str(dep_id))
    return getattr(dep_entity, "status", None) if dep_entity else None


# A dependency terminally cancelled - its code will NEVER reach primary. The dependent
# can't just wait (deadlock); it gets blocked + a draft adapt for the human to triage.
_CANCELLED_DEP_STATUSES = {"wont_do", "deprecated"}


def _classify_deps(
    ctx: ExecutionContext, entity: Any
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split an issue's dependencies into ``(cancelled, pending)``.

    A dep is SATISFIED only when its status is exactly ``done`` - and ``done`` means its
    code is on primary (``advance.close_issue_done`` only runs after a real merge; an
    unmerged issue reads ``awaiting_merge``/``blocked``, never ``done``). So this gate is
    the strict merged-to-primary rule, judged in code, never the agent's call.

    * ``cancelled`` - deps in a terminal non-done state (``wont_do``/``deprecated``): the
      foundation will never land, so the dependent is blocked for a human, not held forever.
    * ``pending`` - deps not done and not cancelled (still in flight): hold and re-check.

    Two tiers: the issue's own ``Depends on: #NN`` ids and its parent ticket's. Only numeric
    provider-id deps are enforced; a dep we cannot resolve counts as pending (likely just not
    synced yet) so we wait rather than race ahead. Non-numeric tokens are skipped.
    """
    cancelled: list[tuple[str, str]] = []
    pending: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _check(dep_id: str) -> None:
        dep_id = str(dep_id)
        if not dep_id.isdigit() or dep_id in seen:
            return
        seen.add(dep_id)
        status = _dep_status(ctx, dep_id)
        if status == "done":
            return
        if status in _CANCELLED_DEP_STATUSES:
            cancelled.append((dep_id, status))
        else:
            pending.append((dep_id, status or "unknown"))

    for dep in getattr(entity, "depends_on", []) or []:
        _check(dep)

    parent_id = getattr(entity, "parent_id", None)
    if parent_id and str(parent_id).isdigit():
        parent, _ = context_mod.load_entity(ctx, str(parent_id))
        for dep in getattr(parent, "depends_on", []) or []:
            _check(dep)
    return cancelled, pending


def _poll_interval_s(ctx: ExecutionContext) -> float:
    try:
        return float((getattr(ctx, "config", {}) or {}).get("poll_interval_seconds") or 45)
    except (TypeError, ValueError):
        return 45.0


# Comment-keyed events: their task post_id is the COMMENT id, so they must be
# resolved to the owning entry before any entity work (a 👍 on a merge-gate comment).
_COMMENT_REACTION_ACTIONS = {"handle_reaction_added", "handle_reaction_removed"}


def _run_work(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Common path for every work handler: load entity, judge state, run agent."""
    post_id = task.get("post_id")
    # A comment reaction (merge-gate approval) is keyed on the comment; map it to the
    # entry that owns it so the gate on the parent issue is the thing we resolve.
    if task.get("action") in _COMMENT_REACTION_ACTIONS:
        resolver = getattr(ctx.local, "entry_id_for_comment", None)
        owning = resolver(post_id) if callable(resolver) else None
        if owning is not None:
            post_id = str(owning)
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
            # The resolve agent is heavy -> hand it to the WORK lane (control stays
            # responsive). The job is the same PLATFORM_ERROR_ACTION dispatch routes
            # to _run_platform_error, tagged reason=reply.
            if _has_pending_work_job(ctx, post_id, recovery.PLATFORM_ERROR_ACTION):
                return HandlerOutcome(success=True, detail="resolve agent already scheduled for {0}".format(post_id))
            work_id = ctx.db.enqueue(
                recovery.PLATFORM_ERROR_ACTION,
                post_id=post_id,
                payload={"reason": "reply"},
                lane=LANE_WORK,
                priority=priorities.WORK_DEFAULT,
            )
            platform_log.log_event("platform_error_reply_scheduled", work_task_id=work_id, post_id=post_id)
            return HandlerOutcome(success=True, detail="resolve agent (reply) scheduled on the work lane")
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
        transition = advance.resolve_approval(ctx, entity, state_result, conversation, action)
        detail = _schedule_gate_action(ctx, entity, transition, task) or "awaiting_approval; no approver yet"
        platform_log.log_event(
            "approval_resolved",
            task_id=task.get("task_id"),
            post_id=post_id,
            detail=detail,
        )
        return HandlerOutcome(success=True, detail=detail)

    # A blocked issue is not dead: a human can approve it through (force done) or
    # hand the implementer guidance to retry. Only fires when something was applied;
    # otherwise fall through to the no-action path.
    if _spec_change_route(entity) is None and getattr(entity, "status", None) == "blocked":
        transition = advance.resolve_blocked(ctx, entity, state_result, conversation, action)
        # nothing applied (no detail/prepare/merge) = fall through to the work path.
        if transition.detail is not None or transition.merge or transition.prepare:
            detail = _schedule_gate_action(ctx, entity, transition, task)
            platform_log.log_event(
                "blocked_resolved",
                task_id=task.get("task_id"),
                post_id=post_id,
                detail=detail,
            )
            return HandlerOutcome(success=True, detail=detail)

    # An awaiting_merge issue (work accepted, branch not on primary yet) is resolved in
    # code with no agent run: 👍/❤️ on its gate comment (or `approve`/`merge` APR) re-readies
    # and merges - idempotent, so it also just confirms a hand-merge - then closes done;
    # prose/`retry` reworks. Nothing here reaches done without an actual merge.
    if _spec_change_route(entity) is None and getattr(entity, "status", None) == "awaiting_merge":
        transition = advance.resolve_awaiting_merge(ctx, entity, state_result, conversation, action)
        detail = _schedule_gate_action(ctx, entity, transition, task) or "awaiting_merge; not yet authorized"
        platform_log.log_event(
            "awaiting_merge_resolved",
            task_id=task.get("task_id"),
            post_id=post_id,
            detail=detail,
        )
        return HandlerOutcome(success=True, detail=detail)

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

    # Dependency gate: an issue may only implement once every issue it depends on is
    # DONE (== merged to primary; see _classify_deps). A dep still in flight -> hold and
    # re-check next poll (no busy loop), so we never build on a foundation a dependency
    # was meant to lay down first. A dep terminally CANCELLED -> block + draft adapt (the
    # foundation will never land, so waiting would deadlock).
    if intent == AgentIntent.IMPLEMENT:
        cancelled, pending = _classify_deps(ctx, entity)
        if cancelled:
            detail = advance.block_on_cancelled_dep(ctx, entity, cancelled)
            platform_log.log_event(
                "work_blocked_cancelled_dep",
                task_id=task.get("task_id"),
                post_id=post_id,
                cancelled=[d for d, _ in cancelled],
            )
            return HandlerOutcome(success=True, detail=detail)
        if pending:
            detail = "held: waiting on {0}".format(
                ", ".join("#{0}({1})".format(d, s) for d, s in pending)
            )
            platform_log.log_event(
                "work_held_on_deps",
                task_id=task.get("task_id"),
                post_id=post_id,
                unmet=[d for d, _ in pending],
            )
            return HandlerOutcome(
                success=True, requeue=True, requeue_after_s=_poll_interval_s(ctx), detail=detail
            )

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

    # Control decided the intent and cleared every gate. The heavy agent run (and
    # any git it implies) is muscle - hand it to the WORK lane. The work job builds
    # the prompt, runs the agent, and on finish enqueues a process_work_result
    # control item that applies the lifecycle transition / spec-change follow-up.
    return _schedule_work_run(ctx, entity, intent, task)


def _read_plan(ctx: ExecutionContext, request_id: Any) -> Optional[dict]:
    path = spec_change_dir(str(request_id), ctx.storage) / "plan.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # A weaker model can emit a bare scalar/list as plan.json; anything that is not
    # a JSON object is not a plan we can read. Guard here so callers never .get() a str.
    return data if isinstance(data, dict) else None


# plan.json keys that, populated, mean the run creates/retires work and MUST be
# approved. ``edits``/``labels``/``comments`` are judged per-target below (a
# clarification round touches only the request itself, which never gates).
_PROPOSE_PLAN_KEYS = ("creates", "settle_docs", "closes", "deletes")
_PLAN_DEPENDS_RE = re.compile(r"depends\s+on\s*:?\s*(.+)", re.IGNORECASE)
_PLAN_VALID_DEP_RE = re.compile(r"^#\s*(?:[0-9]+|[A-Za-z]+-[0-9]+|\{id:[^}]+\})$")


def _validate_depends_on_links(plan: dict) -> None:
    """Reject dependency refs the runtime would not enforce.

    Dependency lines must hold parseable links (`#9`, `#FEAT-0001`) or a staged
    create placeholder with the `#` already present (`#{id:Title}`). Bare ids are
    too easy for agents to emit and the dependency gate ignores them.
    """
    bad: list[str] = []
    entries = list(plan.get("creates") or []) + list(plan.get("edits") or [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue  # a malformed (non-object) entry has no links to validate
        title = str(entry.get("title") or entry.get("post_id") or entry.get("post") or "?")
        body = str(entry.get("body") or "")
        for m in _PLAN_DEPENDS_RE.finditer(body):
            segment = m.group(1)
            for stop in ("-->", "\n"):
                idx = segment.find(stop)
                if idx != -1:
                    segment = segment[:idx]
            refs = [part.strip() for part in segment.split(",") if part.strip()]
            for ref in refs:
                if not _PLAN_VALID_DEP_RE.match(ref):
                    bad.append("{0}: {1}".format(title, ref))
    if bad:
        raise ValueError(
            "dependency links must use # refs, e.g. `Depends on: #{id:Title}`; bad: "
            + "; ".join(bad[:5])
        )


def _touches_other_posts(plan: dict, request_id: Any) -> bool:
    """True if any edit/label/comment in the plan targets a post OTHER than the request.

    A clarification round only comments on the request post and flips its own
    ``spec-change:status`` label; that is the human interaction, not an apply, so it
    is the one run that does not gate. Anything aimed at another post is real work.
    """
    req = str(request_id)
    for entry in (plan.get("edits") or []) + (plan.get("comments") or []):
        if not isinstance(entry, dict):
            return True  # can't prove it stays on the request -> gate it (safe default)
        if str(entry.get("post_id") if "post_id" in entry else entry.get("post")) != req:
            return True
    for change in plan.get("labels") or []:
        if not isinstance(change, dict):
            return True
        if str(change.get("post_id") if "post_id" in change else change.get("post")) != req:
            return True
    return False


def _classify_spec_change(ctx: ExecutionContext, request_id: Any) -> str:
    """Decide the runtime's follow-up for a finished spec-change run, from its OUTPUT.

    The agent no longer chooses the gate (it cannot be trusted to). The runtime reads
    what the run actually produced - ``plan.json`` + the staged spec - and decides:

    * ``"propose"`` - the run creates work OR touches the spec (staged docs,
      settle_docs, or a mutation aimed at another post). Gate it: post the plan
      summary + APR, park ``awaiting_approval``, create/promote NOTHING until a human
      approves.
    * ``"direct"`` - a pure clarification round: it only comments on the request and
      flips the request's own status label, creating no work and staging no spec. Run
      apply.py straight away so the question reaches the human.
    * ``"none"`` - the run produced no actionable plan.
    """
    plan = _read_plan(ctx, request_id)
    if plan is None:
        return "none"
    _validate_depends_on_links(plan)
    needs_approval = bool(staged_spec_files(request_id, ctx.storage)) or any(
        plan.get(key) for key in _PROPOSE_PLAN_KEYS
    ) or _touches_other_posts(plan, request_id)
    if needs_approval:
        return "propose"
    if plan.get("comments") or plan.get("labels"):
        return "direct"
    return "none"


def _enqueue_spec_change_followup(ctx: ExecutionContext, entity: Any, request_id: Any) -> str:
    """Queue the runtime-owned follow-up to a finished spec-change run.

    Plan-first, code-enforced: the agent wrote ``plan.json`` + ``apply.py`` (+ staged
    spec) and stopped. The runtime - not the agent - now decides whether the run gates
    (``_classify_spec_change``) and enqueues the matching task. A proposal parks the
    request awaiting approval; a clarification applies directly.
    """
    route = _spec_change_route(entity)
    kind = _classify_spec_change(ctx, request_id)
    if kind == "propose":
        enqueue_spec_change_propose(request_id=str(request_id), route=route, db=ctx.db)
        platform_log.log_event("spec_change_propose_enqueued", post_id=request_id, route=route)
        return "spec-change planned; proposal enqueued (awaiting approval)"
    if kind == "direct":
        script = spec_change_dir(str(request_id), ctx.storage) / DEFAULT_SCRIPT_NAME
        enqueue_spec_change_run(
            script, request_id=request_id, route=route, db=ctx.db, close_request=False
        )
        platform_log.log_event("spec_change_direct_enqueued", post_id=request_id, route=route)
        return "spec-change clarification; direct apply enqueued"
    platform_log.log_event("spec_change_no_followup", post_id=request_id, route=route)
    return "spec-change run produced no actionable plan; nothing enqueued"


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
        ctx.remote.add_entry_comment(request_id, platform_comment(plan_summary, getattr(ctx, "config", None)))
    ctx.remote.add_entry_comment(request_id, approval_request_comment(apr_id, summary, getattr(ctx, "config", None)))
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

    # Work-lane jobs + their control-lane result handler. work_runner drives the
    # execution helpers in this module; imported lazily to avoid an import cycle
    # (work_runner imports dispatch).
    if action in work_lane.WORK_LANE_ACTIONS or action == work_lane.PROCESS_WORK_RESULT:
        from specseed_runtime.executing import work_runner

        if action == work_lane.WORK_RUN:
            return work_runner.run_agent_job(ctx, task)
        if action == work_lane.WORK_GATE_ACTION:
            return work_runner.run_gate_job(ctx, task)
        return work_runner.process_work_result(ctx, task)

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
