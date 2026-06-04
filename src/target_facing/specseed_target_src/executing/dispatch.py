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

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from src.target_facing.specseed_target_src.executing import context as context_mod
from src.target_facing.specseed_target_src.executing.context import ExecutionContext
from src.target_facing.specseed_target_src.executing import prompts
from src.target_facing.specseed_target_src.scheduling.spec_change import SPEC_CHANGE_ACTION
from src.target_facing.specseed_target_src.state_machines.base import evaluate_entity_state


# How often the spec-change subprocess wait loop checks cancel/timeout.
_POLL_INTERVAL_S = 0.25
# Grace given to a terminate() before a kill().
_GRACE_S = 10.0

CLEANUP_ACTION = "cleanup"
_SPEC_CHANGE_LABEL_PREFIX = "spec-change:"
_SPEC_CHANGE_STATUS_PREFIX = "spec-change:status:"


@dataclass
class HandlerOutcome:
    """Result of handling one task. The scheduler completes/requeues from this."""

    success: bool
    requeue: bool = False
    error: Optional[str] = None
    detail: str = ""


class AgentIntent:
    """The kind of agent work a work-event implies (plain string constants)."""

    SPEC_CHANGE = "spec_change"
    IMPLEMENT = "implement"
    REVIEW = "review"
    NONE = "none"


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
        return AgentIntent.SPEC_CHANGE

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
    ``src.target_facing...`` so it runs with ``cwd=ctx.repo_root`` and
    ``PYTHONPATH`` pointed at the repo root; the script path is the absolute
    join of the payload's ``dir`` + ``script``. ``ctx.cancel`` and
    ``ctx.agent_timeout_s`` are honored via a terminate->wait->kill loop.
    """
    if not ctx.permissions.can_run_spec_change():
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

    env = dict(os.environ)
    repo_root = str(ctx.repo_root)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = repo_root if not existing else os.pathsep.join([repo_root, existing])

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
        return HandlerOutcome(
            success=False,
            error="failed to launch spec-change script: {0}".format(exc),
        )

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

    if cancelled:
        return HandlerOutcome(
            success=False,
            requeue=True,
            error="spec-change script cancelled",
            detail=_combine_output(stdout, stderr),
        )
    if timed_out:
        return HandlerOutcome(
            success=False,
            requeue=True,
            error="spec-change script timed out after {0:.0f}s".format(timeout_s),
            detail=_combine_output(stdout, stderr),
        )
    if returncode == 0:
        return HandlerOutcome(success=True, detail=_combine_output(stdout, stderr))
    return HandlerOutcome(
        success=False,
        error="spec-change script exited with code {0}\n{1}".format(
            returncode, _combine_output(stdout, stderr)
        ),
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


def _run_work(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Common path for every work handler: load entity, judge state, run agent."""
    post_id = task.get("post_id")
    entity, conversation = context_mod.load_entity(ctx, post_id)
    if entity is None:
        return HandlerOutcome(
            success=True,
            detail="no entity for post {0!r}; nothing to do".format(post_id),
        )

    state_result = evaluate_entity_state(entity, ctx.config, conversation)
    intent = decide_intent(entity, state_result, task)

    if intent == AgentIntent.NONE:
        return HandlerOutcome(
            success=True,
            detail="no actionable intent (tier={0}, status={1})".format(
                getattr(entity, "tier", None), getattr(entity, "status", None)
            ),
        )

    if intent == AgentIntent.SPEC_CHANGE:
        route = _spec_change_route(entity)
        request_id = getattr(entity, "post_id", None)
        prompt = prompts.build_spec_change_prompt(route, request_id, entity, ctx)
    elif intent == AgentIntent.IMPLEMENT:
        prompt = prompts.build_implement_prompt(entity, ctx)
    else:  # REVIEW
        prompt = prompts.build_review_prompt(entity, ctx)

    result = ctx.runner.run(
        prompt,
        cwd=ctx.repo_root,
        cancel=ctx.cancel,
        timeout_s=ctx.agent_timeout_s,
    )

    if getattr(result, "ok", False):
        return HandlerOutcome(success=True, detail="agent ran intent {0}".format(intent))
    if getattr(result, "killed", False) or getattr(result, "timed_out", False):
        return HandlerOutcome(
            success=False,
            requeue=True,
            error=getattr(result, "error", None) or "agent interrupted",
            detail="intent {0} interrupted".format(intent),
        )
    return HandlerOutcome(
        success=False,
        error=getattr(result, "error", None) or "agent run failed",
        detail="intent {0} failed".format(intent),
    )


def dispatch(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Route a claimed task to its handler and return the outcome."""
    action = task.get("action")

    if action == SPEC_CHANGE_ACTION:
        return run_spec_change_script(ctx, task)

    if action == CLEANUP_ACTION:
        payload = task.get("payload") or {}
        reason = payload.get("reason")
        interrupted = payload.get("interrupted_task_id")
        return HandlerOutcome(
            success=True,
            detail="cleanup recorded (reason={0}, interrupted_task_id={1})".format(
                reason, interrupted
            ),
        )

    if action in _WORK_ACTIONS:
        return _run_work(ctx, task)

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
}
