"""advance.py - apply work-entity state transitions to the remote, in code.

The agent only ever *does the work* (implement / review). Deciding what the work
means for the entity's lifecycle - claiming it, moving it through review, closing
it, looping a failed review back to implementation, or escalating to the human -
is judged here in plain code against the state machine and the ``review`` config.
This is the half ``dispatch._run_work`` was missing: it used to run the agent and
return success without ever swapping a ``:status:`` label.

Flow (driven from ``dispatch._run_work`` after a successful agent run):

* IMPLEMENT success -> advance the issue off ``todo`` per the configured gates:
  ``in_review`` (review on), else ``awaiting_approval`` (HITL gate), else ``done``
  (+ close the entry).
* REVIEW success -> read the reviewer's self-reported verdict+confidence from the
  agent output, post the review as a comment, then:
  - pass (approve & confidence >= threshold) -> ``done`` (+ close), or
    ``awaiting_approval`` when ``require_human_approval``;
  - fail (changes / low confidence) -> back to ``todo`` to reimplement, until
    ``max_attempts`` review cycles, after which a draft ``spec-change:adapt`` post
    is opened for the human and the issue is parked ``blocked``.

Separately, an ``awaiting_approval`` entity that a configured approver has approved
is resolved here too (no agent): completion gates close, pre-work gates resume.

Only Python stdlib is used.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.target_facing.specseed_target_src.entities.entity_base import (
    STATUS_LABEL_PREFIX,
)


# Hidden marker stamped on every review comment so attempts can be counted from
# the conversation without a side channel.
REVIEW_MARKER = "<!-- specseed:review-attempt -->"
_STATUS_INFIX = ":" + STATUS_LABEL_PREFIX  # ":status:"

_REVIEW_LINE_RE = re.compile(r"SPECSEED_REVIEW\b", re.IGNORECASE)
_VERDICT_RE = re.compile(r"verdict\s*=\s*(approve|changes)", re.IGNORECASE)
_CONFIDENCE_RE = re.compile(r"confidence\s*=\s*([0-9]*\.?[0-9]+)")


# --------------------------------------------------------------------------- #
# remote write helpers (idempotent; honour can_post_issues)
# --------------------------------------------------------------------------- #
def _can_write(ctx: Any) -> bool:
    try:
        return ctx.permissions.can_post_issues()
    except Exception:
        return True


def remote_status(ctx: Any, post_id: Any) -> Optional[str]:
    """Read the entity's CURRENT status from the remote (source of truth).

    The local mirror is a single snapshot for a whole drain pass, so several
    queued events about one entity (e.g. a status-label remove+add pair) all see
    the same stale status. Re-reading the remote lets a transition detect that an
    earlier task already advanced the entity and bow out, instead of acting twice
    (which otherwise duplicates agent runs and side effects like draft posts).
    """
    try:
        res = ctx.remote.get_entry(post_id)
    except Exception:
        return None
    data = getattr(res, "data", None)
    if data is None:
        return None
    names = [str(getattr(lbl, "name", lbl)) for lbl in getattr(data, "labels", []) or []]
    from src.target_facing.specseed_target_src.entities.entity_base import Entity
    return Entity.status_from_labels(names)


def _is_stale(ctx: Any, entity: Any) -> bool:
    """True if the remote has already moved the entity past the judged status."""
    current = remote_status(ctx, entity.post_id)
    return current is not None and current != entity.status


def _status_label(tier: Optional[str], status: str) -> str:
    return f"{tier or 'issue'}:status:{status}"


def _set_status(ctx: Any, entity: Any, new_status: str) -> None:
    """Swap the entity's status label to ``new_status`` (remove any prior form)."""
    post_id = entity.post_id
    for label in list(getattr(entity, "labels", []) or []):
        name = str(label)
        if name.startswith(STATUS_LABEL_PREFIX) or _STATUS_INFIX in name:
            ctx.remote.remove_entry_label(post_id, name)
    ctx.remote.add_entry_label(post_id, _status_label(entity.tier, new_status))


def _comment(ctx: Any, post_id: Any, body: str) -> None:
    ctx.remote.add_entry_comment(post_id, body)


def _close(ctx: Any, post_id: Any) -> None:
    ctx.remote.set_entry_closed(post_id)


# --------------------------------------------------------------------------- #
# review parsing
# --------------------------------------------------------------------------- #
def parse_review(stdout: Optional[str]) -> tuple[str, float]:
    """Pull ``verdict`` + ``confidence`` from the reviewer's trailing line.

    Missing or unparseable -> ('changes', 0.0): the safe default is to assume the
    work is not done so it loops back rather than silently closing.
    """
    if not stdout:
        return "changes", 0.0
    line = None
    for candidate in reversed(stdout.splitlines()):
        if _REVIEW_LINE_RE.search(candidate):
            line = candidate
            break
    if line is None:
        return "changes", 0.0
    verdict_match = _VERDICT_RE.search(line)
    conf_match = _CONFIDENCE_RE.search(line)
    verdict = verdict_match.group(1).lower() if verdict_match else "changes"
    try:
        confidence = float(conf_match.group(1)) if conf_match else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return verdict, confidence


def _count_review_attempts(conversation: Any) -> int:
    count = 0
    for item in conversation or []:
        body = item.get("body") if isinstance(item, dict) else getattr(item, "body", None)
        if body and REVIEW_MARKER in str(body):
            count += 1
    return count


def _review_config(ctx: Any) -> dict:
    review = (getattr(ctx, "config", {}) or {}).get("review")
    return review if isinstance(review, dict) else {}


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #
def apply_post_work_transition(
    ctx: Any,
    entity: Any,
    intent: str,
    result: Any,
    state_result: Any,
    conversation: Any = None,
) -> str:
    """Apply the lifecycle transition implied by a successful agent run.

    ``intent`` is ``dispatch.AgentIntent.{IMPLEMENT,REVIEW}``. Returns a short
    human-readable detail describing what was applied (for the HandlerOutcome).
    """
    if not _can_write(ctx):
        return "remote writes not permitted; no transition applied"
    if _is_stale(ctx, entity):
        return "stale event; remote already advanced past {0}, skipping".format(entity.status)
    if intent == "implement":
        return _advance_after_implement(ctx, entity, state_result)
    if intent == "review":
        return _advance_after_review(ctx, entity, result, conversation)
    return "no transition for intent {0!r}".format(intent)


def _advance_after_implement(ctx: Any, entity: Any, state_result: Any) -> str:
    if getattr(state_result, "review_required", False):
        _set_status(ctx, entity, "in_review")
        return "implement done -> in_review"
    if getattr(state_result, "hitl_required", False):
        _set_status(ctx, entity, "awaiting_approval")
        _comment(
            ctx, entity.post_id,
            "Implementation finished; human sign-off required. An approver must "
            "comment `approve {0}` to complete.".format(entity.post_id),
        )
        return "implement done -> awaiting_approval"
    _set_status(ctx, entity, "done")
    _close(ctx, entity.post_id)
    return "implement done -> done (closed)"


def _advance_after_review(ctx: Any, entity: Any, result: Any, conversation: Any) -> str:
    cfg = _review_config(ctx)
    threshold = float(cfg.get("confidence_threshold", 0.75))
    max_attempts = int(cfg.get("max_attempts", 3))
    require_approval = bool(cfg.get("require_human_approval", False))

    verdict, confidence = parse_review(getattr(result, "stdout", "") or "")
    summary = _review_summary(getattr(result, "stdout", "") or "")
    attempts_before = _count_review_attempts(conversation)
    attempt = attempts_before + 1

    passed = verdict == "approve" and confidence >= threshold
    header = "**Code review** (attempt {0}/{1}) — verdict `{2}`, confidence {3:.2f}".format(
        attempt, max_attempts, verdict, confidence
    )
    _comment(ctx, entity.post_id, "{0}\n\n{1}\n\n{2}".format(header, summary, REVIEW_MARKER))

    if passed:
        if require_approval:
            _set_status(ctx, entity, "awaiting_approval")
            _comment(
                ctx, entity.post_id,
                "Review passed (confidence {0:.2f}). Awaiting human approval: an "
                "approver must comment `approve {1}` to complete.".format(confidence, entity.post_id),
            )
            return "review passed -> awaiting_approval"
        _set_status(ctx, entity, "done")
        _close(ctx, entity.post_id)
        return "review passed -> done (closed)"

    # changes requested / low confidence
    if attempt < max_attempts:
        _set_status(ctx, entity, "todo")
        return "review requested changes -> todo (reimplement, attempt {0})".format(attempt)

    _escalate(ctx, entity, attempt, max_attempts, summary)
    _set_status(ctx, entity, "blocked")
    return "review exhausted {0} attempts -> blocked + draft adapt post".format(max_attempts)


def _escalate(ctx: Any, entity: Any, attempt: int, max_attempts: int, summary: str) -> str:
    """Open a draft spec-change:adapt post asking the human to discuss."""
    title = "Spec adapt needed: issue {0} failed review {1}x".format(entity.post_id, attempt)
    body = (
        "Automated code review could not get issue **{0}** ({1!r}) past its "
        "acceptance criteria after {2} attempts (limit {3}). The work keeps coming "
        "back with requested changes, which usually means the spec or the issue's "
        "scope is unclear or wrong rather than a simple coding miss.\n\n"
        "Latest review summary:\n\n{4}\n\n"
        "This post is a **draft** `spec-change:adapt` request. Discuss what should "
        "change, then drop the `draft` label to let the spec-change worker adapt the "
        "spec. The issue is parked `blocked` until then.".format(
            entity.post_id,
            getattr(entity, "title", None) or "(untitled)",
            attempt,
            max_attempts,
            summary,
        )
    )
    res = ctx.remote.add_entry(
        title=title,
        body=body,
        labels=["spec-change:adapt", "draft", "management"],
    )
    new_id = None
    data = getattr(res, "data", None)
    if data is not None:
        new_id = getattr(data, "id", None)
    _comment(
        ctx, entity.post_id,
        "Review failed {0} times; opened draft spec-adapt post{1} for discussion. "
        "Parked `blocked`.".format(
            max_attempts, " #{0}".format(new_id) if new_id is not None else ""
        ),
    )
    return "escalated"


def resolve_approval(ctx: Any, entity: Any, state_result: Any, conversation: Any = None) -> Optional[str]:
    """Resolve an ``awaiting_approval`` entity when a configured approver approved.

    Returns a detail string if a transition was applied, else None (still waiting).
    A completion gate (work already reviewed) closes the entry; a pre-work HITL
    gate resumes the issue to ``todo``.
    """
    if entity.status != "awaiting_approval":
        return None
    if not getattr(state_result, "approved_by", None):
        return None
    if not _can_write(ctx):
        return None
    if _is_stale(ctx, entity):
        return "stale event; remote already advanced past awaiting_approval"
    approver = state_result.approved_by[0]
    if _count_review_attempts(conversation) > 0:
        _set_status(ctx, entity, "done")
        _close(ctx, entity.post_id)
        _comment(ctx, entity.post_id, "Approved by {0}; completing.".format(approver))
        return "approval gate -> done (closed)"
    _set_status(ctx, entity, "todo")
    _comment(ctx, entity.post_id, "Approved by {0}; work may proceed.".format(approver))
    return "approval gate -> todo (resume work)"


def _review_summary(stdout: str, limit: int = 1500) -> str:
    """Strip the trailing SPECSEED_REVIEW line and cap length for the comment."""
    lines = [ln for ln in stdout.splitlines() if not _REVIEW_LINE_RE.search(ln)]
    text = "\n".join(lines).strip() or "(no review text)"
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n…(truncated)"
    return text
