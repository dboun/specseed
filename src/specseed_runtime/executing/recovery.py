"""recovery.py - what happens after a task fails: retries, the error post, the agent.

Failure used to be invisible: a ``task_errors`` row and a log line, the post in
limbo, no way back but recreating it. Now a retryable failure becomes remote
state and a scheduled retry:

1. **Retry with backoff.** The failed task requeues with ``not_before`` =
   now + 1' / 5' / 15' / 15'... (``claim_next`` honors it). Capped by
   ``config.recovery.max_retries``.
2. **One ``platform_error`` post per task.** The runtime creates it on the
   first failure (deterministic - exists even when no agent is configured),
   body = what failed + the error + what happens next. Every retry outcome
   lands as an auto-comment. Recovery comments + closes it. A human closing
   the post stops the retries.
3. **The resolve agent.** On post creation and on retry exhaustion a
   ``handle_platform_error`` task is queued; dispatch runs the
   ``resolve_platform_errors`` chain to investigate and report on the post
   (and to converse when the human replies there).

Recursion guard: failures of ``handle_platform_error`` tasks themselves are
logged and dropped - the recovery machinery never recovers itself.

Only Python stdlib is used.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from specseed_runtime.executing import platform_log
from specseed_runtime.executing.agent_runner import stdout_tail
from specseed_runtime.platform_identity import platform_comment

# Queue action for a resolve-agent engagement. Dispatch routes it to the
# ``resolve_platform_errors`` runner chain.
PLATFORM_ERROR_ACTION = "handle_platform_error"

# The label every error post carries; seeded by populate_defaults.
PLATFORM_ERROR_LABEL = "platform_error"

# Hidden marker tying an error post to its origin task (recoverable from the
# conversation, no side channel).
_MARKER = "specseed:platform-error"

# Backoff between retries; the last value repeats.
RETRY_BACKOFF_S = (60, 300, 900)
DEFAULT_MAX_RETRIES = 5

# Engage the resolve agent on the Nth consecutive failure: the cheap early
# retries (1', 5') get a chance to clear a transient first; tokens only burn
# when the failure looks real. Exhaustion always engages.
ENGAGE_AFTER_FAILURES = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def recovery_config(config: Optional[dict[str, Any]]) -> dict[str, Any]:
    cfg = (config or {}).get("recovery")
    cfg = cfg if isinstance(cfg, dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "max_retries": int(cfg.get("max_retries", DEFAULT_MAX_RETRIES)),
    }


def retry_delay_s(attempts: int) -> int:
    """Backoff for the NEXT retry after ``attempts`` runs so far."""
    idx = max(0, int(attempts) - 1)
    return RETRY_BACKOFF_S[min(idx, len(RETRY_BACKOFF_S) - 1)]


def _delay_text(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    return f"about {minutes} minute" + ("s" if minutes != 1 else "")


def error_post_title(task: dict[str, Any]) -> str:
    action = task.get("action") or "task"
    origin = task.get("post_id")
    suffix = f" (post {origin})" if origin else ""
    return f"Platform error: {action}{suffix}"


def _marker_line(task_id: Any) -> str:
    return f"<!-- {_MARKER} task={task_id} -->"


def _agent_eta_s(max_retries: int) -> int:
    """Seconds from first failure until the resolve agent engages."""
    engage_attempt = min(ENGAGE_AFTER_FAILURES, max_retries + 1)
    return sum(retry_delay_s(i) for i in range(1, engage_attempt))


def error_post_body(
    task: dict[str, Any],
    outcome: Any,
    next_delay_s: Optional[int],
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> str:
    """The post the human reads first. Plain words, the raw error quoted once."""
    origin = task.get("post_id")
    about = f" while working on post {origin}" if origin else ""
    error = stdout_tail(str(getattr(outcome, "error", None) or "unknown error"))
    lines = [
        f"Something went wrong{about}.",
        "",
        f"**What failed:** `{task.get('action')}` (task {task.get('task_id')}, "
        f"attempt {task.get('attempts')}).",
        "",
        "**The error:**",
        "```",
        error,
        "```",
        "",
    ]
    if next_delay_s is not None:
        lines += [
            f"The task retries automatically: next try in {_delay_text(next_delay_s)}, "
            "then with growing pauses. **Closing this post stops the retries.**",
            "",
            "If the first retries do not fix it, an agent takes a look (in "
            f"{_delay_text(_agent_eta_s(max_retries))}), updates this post with what "
            "it found and what you can do about it, and answers replies here - this "
            "thread is a conversation.",
            "",
        ]
    else:
        lines += [
            "Automatic retries are off for this failure; an agent will take a look "
            "and report here. Replies on this thread reach it.",
            "",
        ]
    lines += [_marker_line(task.get("task_id"))]
    return "\n".join(lines)


def find_error_post(remote: Any, task_id: Any) -> Optional[Any]:
    """The error post for ``task_id`` (open or closed), or None.

    Callers check ``is_open``: a closed post is the human's cancel switch."""
    listed = remote.list_entries(is_open=None, labels=[PLATFORM_ERROR_LABEL])
    if not getattr(listed, "ok", False) or not listed.data:
        return None
    marker = _marker_line(task_id)
    for summary in listed.data:
        detail = remote.get_entry(getattr(summary, "id", None))
        if not getattr(detail, "ok", False) or detail.data is None:
            continue
        if marker in str(getattr(detail.data, "body", "") or ""):
            return detail.data
    return None


def _comment(remote: Any, post_id: Any, body: str) -> None:
    try:
        remote.add_entry_comment(post_id, platform_comment(body))
    except Exception:
        pass  # the report is best-effort; the retry decision already stands


def _enqueue_resolve(db: Any, error_post_id: Any, task: dict[str, Any], reason: str) -> None:
    db.enqueue(
        PLATFORM_ERROR_ACTION,
        post_id=error_post_id,
        payload={
            "origin_task_id": task.get("task_id"),
            "origin_post_id": task.get("post_id"),
            "origin_action": task.get("action"),
            "reason": reason,  # "new" | "exhausted" | (dispatch adds "reply")
        },
    )
    platform_log.log_event(
        "platform_error_agent_enqueued",
        error_post_id=error_post_id,
        origin_task_id=task.get("task_id"),
        reason=reason,
    )


def on_failure(
    *,
    db: Any,
    config: Optional[dict[str, Any]],
    remote: Any,
    task: dict[str, Any],
    outcome: Any,
) -> dict[str, Any]:
    """React to a completed-as-failed task. Returns a small summary dict.

    Call AFTER ``db.complete(..., success=False)`` - the error row is recorded
    either way; this decides what happens next.
    """
    summary: dict[str, Any] = {"retried": False, "post_id": None, "agent": False}
    task_id = task.get("task_id")
    action = str(task.get("action") or "")

    if action == PLATFORM_ERROR_ACTION:
        # The recovery machinery never recovers itself.
        platform_log.log_event("platform_error_agent_failed", task_id=task_id)
        return summary

    cfg = recovery_config(config)
    if not cfg["enabled"] or not getattr(outcome, "retryable", False):
        return summary

    attempts = int(task.get("attempts") or 1)
    exhausted = attempts > cfg["max_retries"]
    delay_s: Optional[int] = None if exhausted else retry_delay_s(attempts)

    # The post first: failure becomes remote state even if the requeue below
    # were to fail. Remote hiccups must never block the retry decision.
    post = None
    created = False
    if remote is not None:
        try:
            post = find_error_post(remote, task_id)
            if post is None:  # also late-creates when the first write failed
                try:  # the label may predate seeding on old targets
                    remote.ensure_label(
                        PLATFORM_ERROR_LABEL,
                        color="b60205",
                        description="A platform task failed; the runtime reports and retries here.",
                    )
                except Exception:
                    pass
                result = remote.add_entry(
                    error_post_title(task),
                    body=error_post_body(task, outcome, delay_s, cfg["max_retries"]),
                    labels=[PLATFORM_ERROR_LABEL],
                )
                if getattr(result, "ok", False) and result.data is not None:
                    post = result.data
                    created = True
        except Exception as exc:
            platform_log.log_event(
                "platform_error_post_failed", task_id=task_id, error=repr(exc)
            )
    post_id = getattr(post, "id", None)
    summary["post_id"] = post_id

    # A human closing the error post is the cancel switch.
    if post is not None and not created and not _is_open(post):
        platform_log.log_event(
            "platform_error_retries_cancelled", task_id=task_id, error_post_id=post_id
        )
        return summary

    if exhausted:
        if post_id is not None:
            _comment(
                remote,
                post_id,
                f"Tried {attempts} times; still failing. No more automatic retries. "
                "An agent will take a deeper look and report here - replies with "
                "instructions are read.",
            )
            _enqueue_resolve(db, post_id, task, reason="exhausted")
            summary["agent"] = True
        platform_log.log_event(
            "task_retries_exhausted", task_id=task_id, attempts=attempts
        )
        return summary

    next_at = _now() + timedelta(seconds=delay_s)
    db.requeue(int(task_id), not_before=_iso(next_at))
    summary["retried"] = True
    platform_log.log_event(
        "task_retry_scheduled",
        task_id=task_id,
        attempts=attempts,
        delay_s=delay_s,
        not_before=_iso(next_at),
    )
    # The cheap early retries run first; the agent engages on the Nth failure
    # (or right away when the post arrives late, past that point).
    engage = attempts == ENGAGE_AFTER_FAILURES or (
        created and attempts > ENGAGE_AFTER_FAILURES
    )
    if post_id is not None:
        if not created:
            error = stdout_tail(str(getattr(outcome, "error", None) or ""), 400)
            note = "\nAn agent is taking a look now and will report here." if engage else ""
            _comment(
                remote,
                post_id,
                f"Retry #{attempts - 1} did not fix it. Next try in "
                f"{_delay_text(delay_s)} ({_iso(next_at)}).{note}\n```\n{error}\n```",
            )
        if engage:
            _enqueue_resolve(db, post_id, task, reason="new")
            summary["agent"] = True
    return summary


def on_recovered(
    *,
    db: Any,
    config: Optional[dict[str, Any]],
    remote: Any,
    task: dict[str, Any],
) -> Optional[Any]:
    """A task succeeded after earlier failures: report + close its error post."""
    task_id = task.get("task_id")
    if remote is None or str(task.get("action") or "") == PLATFORM_ERROR_ACTION:
        return None
    try:
        post = find_error_post(remote, task_id)
    except Exception:
        return None
    if post is None or not _is_open(post):
        return None
    attempts = int(task.get("attempts") or 1)
    _comment(
        remote,
        getattr(post, "id", None),
        f"Recovered: attempt #{attempts} succeeded. Closing this post.",
    )
    try:
        remote.set_entry_closed(getattr(post, "id", None))
    except Exception:
        pass
    platform_log.log_event(
        "platform_error_recovered",
        task_id=task_id,
        error_post_id=getattr(post, "id", None),
        attempts=attempts,
    )
    return post


def _is_open(post: Any) -> bool:
    value = getattr(post, "is_open", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "")
    return bool(value)
