"""work_runner.py - the WORK lane: run the heavy muscle, hand results back.

The control lane (``dispatch._run_work``) decides intent, clears every gate, and
schedules a work job. This module executes those jobs - the parts that run an
agent or touch the git working tree, which must be serial and must never block the
control lane:

* ``run_agent_job`` (``work_run``) - build the prompt for an intent
  (implement/review/spec_change), branch, run the agent, finalize the branch. On a
  successful run it PERSISTS the outcome and enqueues a ``process_work_result``
  control item; the lifecycle transition is brain-work and belongs on control.
  Quota / interrupt / hard-failure return straight to the scheduler so retries and
  the quota circuit work exactly as before (recovery still runs in the scheduler).
* ``run_gate_job`` (``work_gate_action``) - ready a branch + open its merge gate,
  or merge it (incl. the merge-conflicts agent and the post-merge re-prepare of
  sibling gates). Self-contained: advance applies all the remote state.
* ``process_work_result`` (control lane) - reconstruct the persisted agent outcome
  and apply the post-work transition (advance) / spec-change follow-up, scheduling
  any git it implies as a fresh ``work_gate_action``.

The execution helpers (`_run_agent`, branch + merge ops) live in ``dispatch``;
this module drives them. ``dispatch`` routes the work actions here with a lazy
import, so the dispatch <- work_runner edge is the only one.

Only Python stdlib is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from specseed_runtime.executing import advance
from specseed_runtime.executing import context as context_mod
from specseed_runtime.executing import dispatch
from specseed_runtime.executing import platform_log
from specseed_runtime.executing import prompts
from specseed_runtime.executing import priorities
from specseed_runtime.executing import work_lane
from specseed_runtime.executing.agent_runner import stdout_tail
from specseed_runtime.executing.context import ExecutionContext
from specseed_runtime.executing.dispatch import AgentIntent, HandlerOutcome
from specseed_runtime.state_machines.base import evaluate_entity_state


# --------------------------------------------------------------------------- #
# persisted work outcome (work lane writes it, control lane reads it)
# --------------------------------------------------------------------------- #
def _results_dir(ctx: ExecutionContext) -> Path:
    return Path(getattr(ctx, "storage", None) or ctx.repo_root) / "work-results"


def _result_path(ctx: ExecutionContext, work_task_id: Any) -> Path:
    return _results_dir(ctx) / "{0}.json".format(work_task_id)


def write_work_outcome(ctx: ExecutionContext, work_task_id: Any, intent: str, post_id: Any, result: Any) -> Path:
    """Serialize just what the control lane needs to apply the transition.

    The agent's structured report is the contract; stdout is kept only as a
    truncated tail (advance falls back to it only when a report is absent).
    """
    data = {
        "intent": intent,
        "post_id": None if post_id is None else str(post_id),
        "ok": bool(getattr(result, "ok", False)),
        "returncode": getattr(result, "returncode", None),
        "killed": bool(getattr(result, "killed", False)),
        "timed_out": bool(getattr(result, "timed_out", False)),
        "quota_exhausted": bool(getattr(result, "quota_exhausted", False)),
        "quota_reset_hint": getattr(result, "quota_reset_hint", None),
        "error": getattr(result, "error", None),
        "report": getattr(result, "report", None),
        "report_error": getattr(result, "report_error", None),
        "stdout_tail": stdout_tail(getattr(result, "stdout", "") or ""),
    }
    path = _result_path(ctx, work_task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return path


def read_work_outcome(ctx: ExecutionContext, work_task_id: Any) -> Optional[dict]:
    try:
        return json.loads(_result_path(ctx, work_task_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def discard_work_outcome(ctx: ExecutionContext, work_task_id: Any) -> None:
    """Best-effort delete of a consumed outcome file (keeps work-results/ bounded)."""
    try:
        _result_path(ctx, work_task_id).unlink()
    except OSError:
        pass


def _reconstruct_result(data: dict) -> SimpleNamespace:
    """A lightweight stand-in for AgentResult that advance's getattr calls accept."""
    return SimpleNamespace(
        ok=bool(data.get("ok")),
        returncode=data.get("returncode"),
        killed=bool(data.get("killed")),
        timed_out=bool(data.get("timed_out")),
        quota_exhausted=bool(data.get("quota_exhausted")),
        quota_reset_hint=data.get("quota_reset_hint"),
        error=data.get("error"),
        report=data.get("report"),
        report_error=data.get("report_error"),
        stdout=data.get("stdout_tail") or "",
    )


# --------------------------------------------------------------------------- #
# work lane: run the agent
# --------------------------------------------------------------------------- #
def run_agent_job(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Run the agent for the intent the control lane decided, then hand back.

    Returns to the scheduler exactly like the old inline run for quota / interrupt
    / hard failure (so the quota circuit + retries + recovery are unchanged). On a
    successful run it persists the outcome and enqueues ``process_work_result``.
    """
    payload = task.get("payload") or {}
    intent = str(payload.get("intent") or "")
    post_id = payload.get("post_id") or task.get("post_id")
    entity, _conversation = context_mod.load_entity(ctx, post_id)
    if entity is None:
        return HandlerOutcome(success=True, detail="no entity for {0!r}; nothing to run".format(post_id))

    # Work-side re-validation: control decided this run earlier; the entity may have
    # moved on while the work lane was busy (and a status change leaves the pending
    # job behind - teardown only sweeps close/delete). Skip a stale implement/review
    # rather than run an agent on work that no longer applies.
    if intent in (AgentIntent.IMPLEMENT, AgentIntent.REVIEW) and advance._is_stale(ctx, entity):
        platform_log.log_event(
            "work_stale_skipped", task_id=task.get("task_id"), post_id=post_id,
            intent=intent, status=getattr(entity, "status", None),
        )
        return HandlerOutcome(
            success=True,
            detail="stale {0}; remote advanced past {1}".format(intent, getattr(entity, "status", None)),
        )

    if intent == AgentIntent.SPEC_CHANGE:
        # the `spec-change:<sub>` label suffix is the spec SUBROUTE (route is always `spec`)
        subroute = dispatch._spec_change_route(entity)
        prompt = prompts.build_spec_change_prompt(subroute, getattr(entity, "post_id", None), entity, ctx)
    elif intent == AgentIntent.IMPLEMENT:
        prompt = prompts.build_implement_prompt(entity, ctx)
    elif intent == AgentIntent.REVIEW:
        prompt = prompts.build_review_prompt(entity, ctx)
    elif intent == AgentIntent.ASK:
        prompt = prompts.build_ask_prompt(entity, ctx)
    else:
        return HandlerOutcome(success=False, error="work_run: unknown intent {0!r}".format(intent))

    # Runtime owns git: branch before the agent edits anything.
    branch, branch_error = dispatch._prepare_git_branch(ctx, entity, intent)
    if branch_error:
        return HandlerOutcome(
            success=False,
            error="git branch prep failed for {0}: {1}".format(branch, branch_error),
            detail="intent {0} stopped before agent".format(intent),
            retryable=True,
        )
    result = dispatch._run_agent(ctx, prompt, intent, task_id=task.get("task_id"))
    # Commit the work (implement) and always return to the primary branch, however
    # the run ended, so WIP is never stranded on a feature branch.
    dispatch._finalize_git_branch(ctx, entity, intent, branch)
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

    # Provider quota: every runner spec was blocked. Requeue this work job; the
    # scheduler opens the circuit and parks the WORK lane (control stays live).
    if getattr(result, "quota_exhausted", False):
        platform_log.log_event(
            "work_quota_exhausted", task_id=task.get("task_id"), post_id=post_id, intent=intent,
            quota_until=getattr(result, "quota_reset_hint", None),
        )
        return HandlerOutcome(
            success=False, requeue=True, quota=True,
            quota_until=getattr(result, "quota_reset_hint", None),
            error=getattr(result, "error", None) or "provider quota exhausted",
            detail="intent {0} parked: provider quota".format(intent),
        )
    if getattr(result, "killed", False) or getattr(result, "timed_out", False):
        return HandlerOutcome(
            success=False, requeue=True,
            error=getattr(result, "error", None) or "agent interrupted",
            detail="intent {0} interrupted".format(intent),
        )
    if not getattr(result, "ok", False):
        # Hard failure - let the scheduler's recovery retry/escalate (unchanged).
        error = getattr(result, "error", None) or "agent run failed"
        tail = stdout_tail(getattr(result, "stdout", "") or "")
        if tail:
            error = "{0}\n{1}".format(error, tail)
        return HandlerOutcome(success=False, error=error, detail="intent {0} failed".format(intent), retryable=True)

    # rc==0 is not enough: implement/review MUST have written a valid result file.
    # A missing one means the run did not really finish - retry a fresh run.
    if intent in (AgentIntent.IMPLEMENT, AgentIntent.REVIEW, AgentIntent.ASK) and getattr(result, "report", None) is None:
        return HandlerOutcome(
            success=False,
            error="agent finished but did not report a valid result file: {0}".format(
                getattr(result, "report_error", None) or "missing"
            ),
            detail="intent {0} missing result file".format(intent),
            retryable=True,
        )

    # Success: persist the outcome and hand the lifecycle transition to control.
    write_work_outcome(ctx, task.get("task_id"), intent, post_id, result)
    ctx.db.enqueue(
        work_lane.PROCESS_WORK_RESULT,
        post_id=post_id,
        payload={"work_task_id": task.get("task_id"), "intent": intent,
                 "post_id": str(post_id) if post_id is not None else None},
        priority=priorities.CONTROL_WORK_RESULT,
    )
    platform_log.log_event(
        "work_result_handed_to_control", work_task_id=task.get("task_id"), post_id=post_id, intent=intent
    )
    return HandlerOutcome(success=True, detail="ran intent {0}; result handed to control".format(intent))


# --------------------------------------------------------------------------- #
# work lane: ready+gate / merge a branch
# --------------------------------------------------------------------------- #
def run_gate_job(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Run the git side of a gate the control lane authorized: ready+gate or merge.

    advance already applied the remote ack/state; this is the muscle. It applies
    its own resulting remote state (gate opened / closed done / parked blocked /
    siblings re-prepared), so it is terminal - no result is handed back.
    """
    payload = task.get("payload") or {}
    post_id = payload.get("post_id") or task.get("post_id")
    entity, _conversation = context_mod.load_entity(ctx, post_id)
    if entity is None:
        return HandlerOutcome(success=True, detail="no entity for {0!r}; nothing to merge".format(post_id))

    transition = advance.WorkTransition(
        "gate action",
        prepare=bool(payload.get("prepare")),
        merge=bool(payload.get("merge")),
    )
    branch = dispatch._merge_branch_for(entity)
    try:
        note = dispatch._run_gate_action(ctx, entity, transition, branch, task)
    except Exception as exc:
        platform_log.log_event("gate_action_failed", task_id=task.get("task_id"), post_id=post_id, error=repr(exc))
        return HandlerOutcome(
            success=False, error="gate action failed: {0!r}".format(exc),
            detail="gate action for {0}".format(post_id), retryable=True,
        )
    platform_log.log_event("gate_action_done", task_id=task.get("task_id"), post_id=post_id, detail=note)
    return HandlerOutcome(success=True, detail=note or "gate action: nothing to do")


# --------------------------------------------------------------------------- #
# control lane: digest a finished work run
# --------------------------------------------------------------------------- #
def process_work_result(ctx: ExecutionContext, task: dict) -> HandlerOutcome:
    """Apply the lifecycle transition implied by a successful work run (brain).

    Reads the persisted agent outcome and runs the post-work transition (advance)
    / spec-change follow-up, scheduling any git it implies as a ``work_gate_action``.
    Only enqueued for successful runs, so failure recovery is not its concern.
    """
    payload = task.get("payload") or {}
    work_task_id = payload.get("work_task_id")
    intent = str(payload.get("intent") or "")
    post_id = payload.get("post_id") or task.get("post_id")

    data = read_work_outcome(ctx, work_task_id)
    if data is None:
        platform_log.log_event("work_result_missing", task_id=task.get("task_id"), work_task_id=work_task_id)
        return HandlerOutcome(success=True, detail="no persisted outcome for work job {0}".format(work_task_id))
    # Consume the outcome file now: this item digests it exactly once.
    discard_work_outcome(ctx, work_task_id)
    result = _reconstruct_result(data)

    entity, conversation = context_mod.load_entity(ctx, post_id)
    if entity is None:
        return HandlerOutcome(success=True, detail="no entity for {0!r}; result discarded".format(post_id))

    if intent == AgentIntent.SPEC_CHANGE:
        try:
            detail = dispatch._enqueue_spec_change_followup(ctx, entity, post_id)
        except Exception as exc:
            return HandlerOutcome(
                success=False,
                error="spec-change follow-up failed: {0!r}".format(exc),
                detail="spec-change follow-up failed",
                retryable=True,
            )
        platform_log.log_event(
            "work_result_processed", task_id=task.get("task_id"), post_id=post_id, intent=intent, detail=detail
        )
        return HandlerOutcome(success=True, detail=detail)

    if intent == AgentIntent.ASK:
        try:
            detail = advance.apply_ask_answer(ctx, entity, result)
        except Exception as exc:  # never lose the run over a write hiccup
            return HandlerOutcome(success=True, detail="ask answer post failed: {0!r}".format(exc))
        platform_log.log_event(
            "work_result_processed", task_id=task.get("task_id"), post_id=post_id, intent=intent, detail=detail
        )
        return HandlerOutcome(success=True, detail=detail)

    if intent in (AgentIntent.IMPLEMENT, AgentIntent.REVIEW):
        state_result = evaluate_entity_state(entity, ctx.config, conversation)
        try:
            transition = advance.apply_post_work_transition(
                ctx, entity, intent, result, state_result, conversation
            )
            detail = transition.detail
        except Exception as exc:  # never lose the run over a write hiccup
            return HandlerOutcome(success=True, detail="transition failed: {0!r}".format(exc))
        # advance decided the branch's fate; the git is muscle -> schedule it.
        if transition.prepare or transition.merge:
            detail = dispatch._schedule_gate_action(ctx, entity, transition, task)
        platform_log.log_event(
            "work_result_processed", task_id=task.get("task_id"), post_id=post_id, intent=intent, detail=detail
        )
        return HandlerOutcome(success=True, detail=detail)

    return HandlerOutcome(success=True, detail="no transition for intent {0!r}".format(intent))
