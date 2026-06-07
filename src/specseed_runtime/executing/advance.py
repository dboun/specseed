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
  (+ close the entry). If the implementer set ``recommend_spec_change`` the issue is
  parked ``blocked`` with a draft ``spec-change:adapt`` post and review is skipped.
* REVIEW success -> read the reviewer's verdict+confidence (+ ``recommend_spec_change``)
  from the result file, post the review as a comment, then:
  - approve & confidence >= threshold -> ``done`` (+ close);
  - approve but BELOW the bar -> ``awaiting_approval`` for human sign-off (NOT a
    reimplement: reviewer likes it but isn't sure - that is a human call);
  - changes -> back to ``todo`` to reimplement, until ``max_attempts`` cycles. When
    exhausted, only a reviewer ``recommend_spec_change`` opens a draft adapt; otherwise
    the issue parks ``blocked`` for a human decision (we don't presume the spec wrong).

Separately, human signals on a parked entity are resolved here (no agent):
``resolve_approval`` handles ``awaiting_approval`` (approve -> done/resume; prose or
``reject <prose>`` -> ``todo`` with guidance; ``retry`` -> ``todo`` blind; bare reject
/ 👎 -> post the options prompt and wait) and ``resolve_blocked`` lets a human bypass
a block (approve -> force done; guidance/retry -> ``todo``).

Only Python stdlib is used.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any, Optional

from specseed_runtime.entities.entity_base import (
    Entity,
    STATUS_LABEL_PREFIX,
)
from specseed_runtime.executing import platform_log
from specseed_runtime.executing import relationships
from specseed_runtime.platform_identity import (
    is_platform_comment,
    platform_comment,
    platform_username,
)
from specseed_runtime.scheduling.spec_change import (
    DEFAULT_SCRIPT_NAME,
    enqueue_spec_change_run,
    spec_change_dir,
)
from specseed_runtime.state_machines.base import (
    APPROVAL_COMMAND_RE,
    ID_SPLIT_RE,
    REJECT_COMMAND_RE,
)


# Hidden marker stamped on every review comment so attempts can be counted from
# the conversation without a side channel.
REVIEW_MARKER = "<!-- specseed:review-attempt -->"
# Stamped on the "you rejected without guidance" prompt so it posts at most once.
OPTIONS_MARKER = "<!-- specseed:reject-options -->"
_STATUS_INFIX = ":" + STATUS_LABEL_PREFIX  # ":status:"

# Comment-bearing sync actions: only these may carry a human directive (prose /
# retry). A bare label/reaction event must not redirect off a STALE comment.
_COMMENT_ACTIONS = ("handle_comment_added", "handle_comment_updated")

_RETRY_RE = re.compile(r"^\s*retry\b", re.IGNORECASE)
_ID_TOKEN_RE = re.compile(r"^(?:APR-?\d+|#?\d+)$", re.IGNORECASE)

# A parent (ticket/epic) is closed once every child is in one of these.
TERMINAL_TIER_STATUSES = {"done", "wont_do", "deprecated"}
# child tier -> (parent tier, the body-link kind that lists the parent's children)
_PARENT_OF = {"issue": ("ticket", "issues"), "ticket": ("epic", "tickets")}

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
    from specseed_runtime.entities.entity_base import Entity
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
    # Prefix marks it as ours so the next sync never turns it back into work.
    ctx.remote.add_entry_comment(post_id, platform_comment(body))


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
# human directives on a parked post (guidance / retry / bare reject)
# --------------------------------------------------------------------------- #
def _comment_field(item: Any, field: str) -> Any:
    return item.get(field) if isinstance(item, dict) else getattr(item, field, None)


def _latest_human_comment(conversation: Any, config: Any) -> tuple[Optional[str], Optional[str]]:
    """The most recent non-platform comment's (author, body), or (None, None).

    Platform-authored comments (our own gate notes, review summaries) are skipped
    so a directive is always read from the human, never from our own words.
    """
    username = platform_username(config or {})
    for item in reversed(list(conversation or [])):
        body = str(_comment_field(item, "body") or "")
        if not body.strip():
            continue
        author = _comment_field(item, "author")
        if is_platform_comment(author=author, body=body, username=username):
            continue
        return author, body
    return None, None


def _reject_prose(body: str) -> Optional[str]:
    """If ``body`` is a ``reject`` command, return any prose after the ids.

    ``reject`` / ``reject APR-1`` -> "" (bare, no guidance). ``reject do X
    instead`` -> "do X instead". Not a reject command -> None.
    """
    match = REJECT_COMMAND_RE.match(body.strip())
    if match is None:
        return None
    tail = match.group("ids").strip()
    if not tail:
        return ""
    prose = [tok for tok in ID_SPLIT_RE.split(tail) if tok and not _ID_TOKEN_RE.match(tok)]
    return " ".join(prose).strip()


def _human_directive(conversation: Any, config: Any) -> tuple[Optional[str], Optional[str]]:
    """Classify the latest human comment on a parked post.

    Returns one of:
      ("retry", None)         - re-run with no new feedback
      ("guidance", <text>)    - free prose (or ``reject <prose>``): revise per it
      ("reject_bare", None)   - ``reject`` with no prose: needs the options prompt
      (None, None)            - approve command (handled via approved_by) or nothing
    """
    _author, body = _latest_human_comment(conversation, config)
    if body is None:
        return None, None
    stripped = body.strip()
    if _RETRY_RE.match(stripped):
        return "retry", None
    prose = _reject_prose(stripped)
    if prose is not None:  # it WAS a reject command
        return ("guidance", prose) if prose else ("reject_bare", None)
    if APPROVAL_COMMAND_RE.match(stripped):
        return None, None  # an approve command is handled by approved_by
    return "guidance", stripped


def _options_already_posted(ctx: Any, post_id: Any) -> bool:
    """True if the reject-options prompt is already on the post (remote = truth).

    Reading the remote (not the local snapshot, which lags a poll) keeps a 👎 then a
    second event before the next sync from posting the prompt twice.
    """
    try:
        res = ctx.remote.get_entry(post_id)
    except Exception:
        return False
    data = getattr(res, "data", None)
    for item in getattr(data, "comments", None) or []:
        if OPTIONS_MARKER in str(_comment_field(item, "body") or ""):
            return True
    return False


def _post_reject_options(ctx: Any, entity: Any) -> None:
    _comment(
        ctx, entity.post_id,
        "Rejected, but no guidance was given. Pick one:\n"
        "- **reply** with what to change - the implementation agent revises using "
        "your notes.\n"
        "- comment **`retry`** - the agent re-runs with no new feedback.\n\n"
        "Nothing runs until you reply.\n\n" + OPTIONS_MARKER,
    )


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
        return _advance_after_implement(ctx, entity, state_result, result)
    if intent == "review":
        return _advance_after_review(ctx, entity, result, conversation)
    return "no transition for intent {0!r}".format(intent)


def _advance_after_implement(ctx: Any, entity: Any, state_result: Any, result: Any = None) -> str:
    # The agent's structured report decides whether work really happened. rc==0
    # alone used to advance a "Blocked: cannot make changes" run to review.
    report = getattr(result, "report", None) or {}
    # The implementer can say up front "this can't be done as written; the SPEC is
    # wrong" - block + draft an adapt for a human and skip review entirely (no point
    # reviewing code the agent declined to write).
    if bool(report.get("recommend_spec_change")):
        summary = report.get("summary") or "(no detail provided)"
        new_id = _draft_adapt_post(
            ctx, entity,
            title="Spec adapt needed: issue {0} - implementer flagged the spec".format(entity.post_id),
            reason=(
                "The implementation agent could not satisfy issue **{0}** ({1!r}) as "
                "written and flagged the SPEC/issue scope as the problem, not a coding "
                "obstacle.".format(entity.post_id, getattr(entity, "title", None) or "(untitled)")
            ),
            summary=summary,
        )
        _set_status(ctx, entity, "blocked")
        _comment(
            ctx, entity.post_id,
            "Implementer recommends a spec change; opened draft spec-adapt post{0} for "
            "discussion. Parked `blocked` (review skipped). Approve to accept anyway, or "
            "comment guidance to retry.".format(" #{0}".format(new_id) if new_id is not None else ""),
        )
        return "implement recommends spec-change -> blocked + draft adapt"
    status = str(report.get("status") or "").lower()
    if status in ("blocked", "needs_input"):
        detail = report.get("summary") or "(no detail provided)"
        word = "blocked" if status == "blocked" else "a human decision"
        _comment(
            ctx, entity.post_id,
            "Implementation could not complete - reported {0}:\n\n{1}".format(word, detail),
        )
        _set_status(ctx, entity, "blocked")
        return "implement reported {0} -> blocked".format(status)
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
    rolled = roll_up(ctx, entity)
    return "implement done -> done (closed)" + ("; " + rolled if rolled else "")


def _advance_after_review(ctx: Any, entity: Any, result: Any, conversation: Any) -> str:
    cfg = _review_config(ctx)
    threshold = float(cfg.get("confidence_threshold", 0.95))
    max_attempts = int(cfg.get("max_attempts", 2))

    # The structured report is the contract; fall back to scraping stdout only if
    # it is somehow absent (dispatch hard-gates implement/review on a valid report).
    report = getattr(result, "report", None) or {}
    if report.get("verdict"):
        verdict = str(report["verdict"]).lower()
        confidence = float(report.get("confidence") or 0.0)
        summary = report.get("summary") or "(no review summary)"
        recommend = bool(report.get("recommend_spec_change"))
    else:
        verdict, confidence = parse_review(getattr(result, "stdout", "") or "")
        summary = _review_summary(getattr(result, "stdout", "") or "")
        recommend = False
    attempts_before = _count_review_attempts(conversation)
    attempt = attempts_before + 1

    header = "**Code review** (attempt {0}/{1}) — verdict `{2}`, confidence {3:.2f}".format(
        attempt, max_attempts, verdict, confidence
    )
    _comment(ctx, entity.post_id, "{0}\n\n{1}\n\n{2}".format(header, summary, REVIEW_MARKER))

    # approve + confident -> done.
    if verdict == "approve" and confidence >= threshold:
        _set_status(ctx, entity, "done")
        _close(ctx, entity.post_id)
        rolled = roll_up(ctx, entity)
        return "review passed -> done (closed)" + ("; " + rolled if rolled else "")

    # approve but UNDER the confidence bar -> a human looks, NOT a reimplement and
    # NOT a spec change. "Reviewer thinks it's fine but isn't sure" is the textbook
    # case for human sign-off; reimplementing fine code just burns cycles.
    if verdict == "approve":
        _set_status(ctx, entity, "awaiting_approval")
        _comment(
            ctx, entity.post_id,
            "Review approved but confidence {0:.2f} is below the {1:.2f} bar. Parked for "
            "human sign-off: comment `approve {2}` (or 👍) to complete, reply with what to "
            "change to revise, or comment `retry` to re-review.".format(
                confidence, threshold, entity.post_id
            ),
        )
        return "review approve below confidence bar -> awaiting_approval"

    # verdict == changes: reimplement until the loop is exhausted.
    if attempt < max_attempts:
        _set_status(ctx, entity, "todo")
        return "review requested changes -> todo (reimplement, attempt {0})".format(attempt)

    # Loop exhausted. Only escalate to a spec change when the REVIEWER recommended
    # it; otherwise park for a human decision (don't presume the spec is wrong).
    _set_status(ctx, entity, "blocked")
    if recommend:
        new_id = _draft_adapt_post(
            ctx, entity,
            title="Spec adapt needed: issue {0} failed review {1}x".format(entity.post_id, attempt),
            reason=(
                "Automated code review could not get issue **{0}** ({1!r}) past its "
                "acceptance criteria after {2} attempts (limit {3}), and the reviewer "
                "flagged the SPEC/issue scope as the cause rather than a coding "
                "miss.".format(
                    entity.post_id, getattr(entity, "title", None) or "(untitled)",
                    attempt, max_attempts,
                )
            ),
            summary=summary,
        )
        _comment(
            ctx, entity.post_id,
            "Review failed {0} times and recommends a spec change; opened draft "
            "spec-adapt post{1}. Parked `blocked`. Approve to accept anyway, or comment "
            "guidance to retry.".format(
                max_attempts, " #{0}".format(new_id) if new_id is not None else ""
            ),
        )
        return "review exhausted + recommend_spec_change -> blocked + draft adapt"
    _comment(
        ctx, entity.post_id,
        "Review still requesting changes after {0} attempts. Parked `blocked` for a human "
        "decision: comment `approve {1}` to accept as-is, reply with guidance to retry, or "
        "open a `spec-change:adapt` if the spec itself is wrong.".format(
            max_attempts, entity.post_id
        ),
    )
    return "review exhausted -> blocked (human decision; no adapt)"


def _draft_adapt_post(ctx: Any, entity: Any, title: str, reason: str, summary: str) -> Any:
    """Open a draft spec-change:adapt post for the human. Returns the new post id (or None).

    The caller sets the issue's status and posts the cross-link note; this only
    creates the discussion post so review-exhaust and implement-recommend share one
    shape.
    """
    body = (
        "{0}\n\n"
        "Latest agent summary:\n\n{1}\n\n"
        "This post is a **draft** `spec-change:adapt` request. Discuss what should "
        "change, then drop the `draft` label to let the spec-change worker adapt the "
        "spec. The issue is parked `blocked` until then.".format(reason, summary)
    )
    res = ctx.remote.add_entry(
        title=title,
        body=body,
        labels=["spec-change:adapt", "draft", "management"],
    )
    data = getattr(res, "data", None)
    return getattr(data, "id", None) if data is not None else None


def park_for_implement_approval(ctx: Any, entity: Any) -> str:
    """Park a ready issue ``awaiting_approval`` because ``auto_implement_issue`` is off.

    The issue waits until a configured approver comments ``approve <id>``; the gate is
    then resolved by ``resolve_approval`` (pre-work gate -> back to ``todo``), and the
    next ``todo`` event runs the implementation agent (approval is now on record).
    """
    if not _can_write(ctx):
        return "remote writes not permitted; not parking"
    if _is_stale(ctx, entity):
        return "stale event; remote already advanced past {0}".format(entity.status)
    _set_status(ctx, entity, "awaiting_approval")
    _comment(
        ctx, entity.post_id,
        "Implementation requires human approval (`auto_implement_issue` is off). An "
        "approver must comment `approve {0}` before work begins.".format(entity.post_id),
    )
    return "todo -> awaiting_approval (implement approval required)"


def resolve_approval(
    ctx: Any, entity: Any, state_result: Any, conversation: Any = None, action: Any = None
) -> Optional[str]:
    """Resolve an ``awaiting_approval`` entity from a human's signal.

    Returns a detail string if anything was applied, else None (still waiting).

    * approve (👍 / ``approve <id>``) -> a completion gate (work reviewed) closes the
      entry; a pre-work HITL gate resumes the issue to ``todo``.
    * prose comment, or ``reject <prose>`` -> ``todo`` so the implementer revises with
      that guidance (the comment is already on the thread for it to read).
    * ``retry`` -> ``todo`` to re-run with no new feedback.
    * bare ``reject`` / 👎 with no guidance -> post the options prompt once and wait;
      nothing is run until the human says what to do.
    """
    if entity.status != "awaiting_approval":
        return None
    approved = getattr(state_result, "approved_by", None)
    rejected = getattr(state_result, "rejected_by", None)
    is_comment = action in _COMMENT_ACTIONS
    # Nothing to act on (a bare label re-sync, say): bow out before any remote read.
    if not approved and not rejected and not is_comment:
        return None
    if not _can_write(ctx):
        return None
    if _is_stale(ctx, entity):
        return "stale event; remote already advanced past awaiting_approval"
    # Approval wins over a stray rejection if somehow both are present.
    if approved:
        approver = approved[0]
        if _count_review_attempts(conversation) > 0:
            _set_status(ctx, entity, "done")
            _close(ctx, entity.post_id)
            _comment(ctx, entity.post_id, "Approved by {0}; completing.".format(approver))
            rolled = roll_up(ctx, entity)
            return "approval gate -> done (closed)" + ("; " + rolled if rolled else "")
        _set_status(ctx, entity, "todo")
        _comment(ctx, entity.post_id, "Approved by {0}; work may proceed.".format(approver))
        return "approval gate -> todo (resume work)"

    # Not approved. A directive only counts off a fresh comment, never a stale one
    # surfaced by a label/reaction event.
    kind, _text = (None, None)
    if is_comment:
        kind, _text = _human_directive(conversation, getattr(ctx, "config", {}))
    if kind == "retry":
        _set_status(ctx, entity, "todo")
        _comment(ctx, entity.post_id, "Retrying implementation with no new feedback.")
        return "approval gate -> todo (retry, no feedback)"
    if kind == "guidance":
        _set_status(ctx, entity, "todo")
        _comment(
            ctx, entity.post_id,
            "Taking your comment as change guidance; re-running implementation.",
        )
        return "approval gate -> todo (revise with guidance)"

    # Bare reject / 👎 with no guidance: ask what to do (once), then wait.
    if (kind == "reject_bare" or rejected) and not _options_already_posted(ctx, entity.post_id):
        _post_reject_options(ctx, entity)
        return "approval gate: rejected without guidance -> options prompt posted"
    return None


def resolve_blocked(
    ctx: Any, entity: Any, state_result: Any, conversation: Any = None, action: Any = None
) -> Optional[str]:
    """Human bypass for a ``blocked`` issue (e.g. parked by a spec-change recommend).

    The recommendation is a suggestion, never a forced path: a human can still
    approve the work (force done) or hand the implementer guidance (re-run). Returns
    a detail string if a transition was applied, else None.
    """
    if getattr(entity, "status", None) != "blocked":
        return None
    approved = getattr(state_result, "approved_by", None)
    is_comment = action in _COMMENT_ACTIONS
    # Only a human approval or a fresh comment can move a block; skip otherwise.
    if not approved and not is_comment:
        return None
    if not _can_write(ctx):
        return None
    if _is_stale(ctx, entity):
        return "stale event; remote already advanced past blocked"
    if approved:
        _set_status(ctx, entity, "done")
        _close(ctx, entity.post_id)
        _comment(ctx, entity.post_id, "Approved by {0} over the block; completing.".format(approved[0]))
        rolled = roll_up(ctx, entity)
        return "blocked -> done (human override)" + ("; " + rolled if rolled else "")
    kind, _text = (None, None)
    if is_comment:
        kind, _text = _human_directive(conversation, getattr(ctx, "config", {}))
    if kind in ("retry", "guidance"):
        _set_status(ctx, entity, "todo")
        note = (
            "Retrying implementation with no new feedback."
            if kind == "retry"
            else "Taking your comment as change guidance; re-running implementation."
        )
        _comment(ctx, entity.post_id, note)
        return "blocked -> todo ({0})".format("retry" if kind == "retry" else "revise with guidance")
    return None


# --------------------------------------------------------------------------- #
# spec-change request approval (settle the spec, finalize the request)
# --------------------------------------------------------------------------- #
_SPEC_CHANGE_STATUS_PREFIX = "spec-change:status:"


def _spec_change_status_label(labels: Any) -> Optional[str]:
    for label in labels or []:
        name = str(label)
        if name.startswith(_SPEC_CHANGE_STATUS_PREFIX):
            return name
    return None


def _set_spec_change_status(ctx: Any, entity: Any, new_status: str) -> None:
    """Swap the request's ``spec-change:status:*`` label (tier-less, so not _set_status)."""
    current = _spec_change_status_label(getattr(entity, "labels", []) or [])
    if current is not None:
        ctx.remote.remove_entry_label(entity.post_id, current)
    ctx.remote.add_entry_label(entity.post_id, _SPEC_CHANGE_STATUS_PREFIX + new_status)


def _settle_doc(path: Path, when: str) -> bool:
    """Stamp ``settled: true`` + ``settled_at`` into a spec doc's frontmatter.

    Updates the keys in an existing ``---`` frontmatter block, or prepends one.
    Returns True if the file was written.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            block = lines[1:end]
            block = [ln for ln in block if not re.match(r"\s*settled(_at)?\s*:", ln)]
            block.append("settled: true")
            block.append(f"settled_at: {when}")
            new = ["---", *block, "---", *lines[end + 1:]]
            path.write_text("\n".join(new) + "\n", encoding="utf-8")
            return True
    header = ["---", "settled: true", f"settled_at: {when}", "---", ""]
    path.write_text("\n".join(header) + "\n" + text, encoding="utf-8")
    return True


_APPLY_KEYS = ("creates", "edits", "labels", "comments", "closes", "deletes")


def _remote_spec_change_status(ctx: Any, post_id: Any) -> Optional[str]:
    """The request's CURRENT ``spec-change:status:*`` suffix from the remote, or None."""
    try:
        res = ctx.remote.get_entry(post_id)
    except Exception:
        return None
    data = getattr(res, "data", None)
    if data is None:
        return None
    for lbl in getattr(data, "labels", []) or []:
        name = str(getattr(lbl, "name", lbl))
        if name.startswith(_SPEC_CHANGE_STATUS_PREFIX):
            return name[len(_SPEC_CHANGE_STATUS_PREFIX):]
    return None


def _plan_route(ctx: Any, request_id: Any) -> Optional[str]:
    plan_path = spec_change_dir(str(request_id), ctx.storage) / "plan.json"
    try:
        return json.loads(plan_path.read_text(encoding="utf-8")).get("route")
    except (OSError, ValueError, AttributeError):
        return None


def _enqueue_apply_on_approval(ctx: Any, request_id: Any, route: Optional[str]) -> bool:
    """On approval, queue the request's deferred ``apply.py`` (the doer).

    Plan-first: ``apply.py`` is NOT run before approval - the propose step only
    posted the plan summary. Now that a human approved, run it so the epics /
    tickets / issues get created on the remote. Returns True if a run was queued
    (the script exists AND the plan has remote mutations to make), so the caller
    can decide whether to close the request here (nothing to apply) or leave it
    open for ``apply.py`` to finalize.
    """
    script = spec_change_dir(str(request_id), ctx.storage) / DEFAULT_SCRIPT_NAME
    if not script.exists():
        return False
    plan_path = spec_change_dir(str(request_id), ctx.storage) / "plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        plan = {}
    if not any(plan.get(key) for key in _APPLY_KEYS):
        return False  # spec-only run: nothing to create/edit on the remote
    if not _can_write(ctx):
        return False
    # close_request: this approval-path apply is the run that ends the request, so
    # the executor closes the request post on success - we don't trust the agent's
    # plan.json.closes to list it. resolve_spec_change_request's remote-truth guard
    # ensures only the first approval ever reaches here, so only ONE apply is tagged.
    enqueue_spec_change_run(
        script, request_id=request_id, route=route, db=ctx.db, close_request=True
    )
    platform_log.log_event("spec_change_apply_enqueued", post_id=request_id, route=route)
    return True


def _settle_docs_for_request(ctx: Any, request_id: Any) -> list[str]:
    """Read ``plan.json.settle_docs`` for the request and stamp each doc. Returns paths done."""
    plan_path = spec_change_dir(request_id, ctx.storage) / "plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    specseed_dir = Path(ctx.storage).parent
    when = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    done: list[str] = []
    for rel in plan.get("settle_docs", []) or []:
        candidate = specseed_dir / rel
        if not candidate.exists():
            candidate = specseed_dir / "spec" / rel
        if candidate.exists() and _settle_doc(candidate, when):
            done.append(rel)
    return done


def resolve_spec_change_request(ctx: Any, entity: Any, state_result: Any) -> Optional[str]:
    """Finalize an ``awaiting_approval`` spec-change REQUEST once an approver acts.

    Deterministic, no agent: an approval settles the spec docs the worker listed in
    ``plan.json.settle_docs`` and moves the request to ``done``; a rejection moves it to
    ``rejected``. Returns a detail string if a transition was applied, else None (still
    waiting - the caller then lets a wake comment re-run the worker, e.g. a clarification
    answer that is not an approval).
    """
    approved = getattr(state_result, "approved_by", None)
    rejected = getattr(state_result, "rejected_by", None)
    if not approved and not rejected:
        return None
    if not _can_write(ctx):
        return None
    # Remote-truth guard: the local snapshot is one frame for a whole drain, so two
    # queued approval events for one request (a 👍 reaction AND an approve comment)
    # both see it `awaiting_approval`. Re-read the remote: if an earlier task already
    # resolved it, bow out - otherwise we would settle twice and, worse, enqueue the
    # creating apply.py a SECOND time (duplicate posts).
    current = _remote_spec_change_status(ctx, entity.post_id)
    if current in ("done", "rejected"):
        return "stale spec-change approval; request already {0}".format(current)
    if approved:
        approver = approved[0]
        settled = _settle_docs_for_request(ctx, entity.post_id)
        _set_spec_change_status(ctx, entity, "done")
        route = _plan_route(ctx, entity.post_id)
        # Plan-first: the approved plan's apply.py creates the work NOW (nothing
        # was created before approval). When there is work to apply, apply.py owns
        # finalizing + closing the request; a spec-only run (nothing to apply) we
        # close here so the request doesn't linger open.
        apply_enqueued = _enqueue_apply_on_approval(ctx, entity.post_id, route)
        tail = "Creating the approved work now." if apply_enqueued else "Request done."
        _comment(
            ctx, entity.post_id,
            "Approved by {0}. Spec settled ({1} doc(s)): {2}. {3}".format(
                approver, len(settled), ", ".join(settled) or "none", tail
            ),
        )
        if not apply_enqueued:
            _close(ctx, entity.post_id)
        platform_log.log_event(
            "spec_change_settled", post_id=entity.post_id, approver=approver,
            docs=settled, apply_enqueued=apply_enqueued,
        )
        return "spec-change approved -> settled ({0} docs); {1}".format(
            len(settled), "apply enqueued" if apply_enqueued else "done (closed)"
        )
    rejecter = rejected[0]
    # Rejected before any work exists (plan-first): nothing to tear down, just close.
    _set_spec_change_status(ctx, entity, "rejected")
    _comment(ctx, entity.post_id, "Rejected by {0}; request rejected. No work was created.".format(rejecter))
    _close(ctx, entity.post_id)
    return "spec-change rejected (closed)"


def _review_summary(stdout: str, limit: int = 1500) -> str:
    """Strip the trailing SPECSEED_REVIEW line and cap length for the comment.

    Fallback only (the structured report.summary is preferred). Keep the TAIL, not
    the head: a CLI prints its banner + echoed prompt first and the actual findings
    last, so head-truncation posted pure noise to the tracker.
    """
    lines = [ln for ln in stdout.splitlines() if not _REVIEW_LINE_RE.search(ln)]
    text = "\n".join(lines).strip() or "(no review text)"
    if len(text) > limit:
        text = "…(truncated)\n" + text[-limit:].lstrip()
    return text


# --------------------------------------------------------------------------- #
# parent roll-up (close a ticket/epic once its children are all terminal)
# --------------------------------------------------------------------------- #
def roll_up(ctx: Any, entity: Any) -> Optional[str]:
    """Close the parent chain above a just-finished entity, when complete.

    Called after an issue (or ticket) reaches a terminal state. Walks up the
    body-link graph: an issue's ticket closes once every issue under it is
    terminal, then that ticket's epic closes once every ticket under it is
    terminal. Idempotent and remote-truth checked, so re-running is safe and a
    parent already terminal is left alone. Returns a short detail string, or
    ``None`` when nothing rolled up.
    """
    if not _can_write(ctx):
        return None
    try:
        summaries = _entry_summaries(ctx)
    except Exception as exc:
        platform_log.log_event("rollup_error", post_id=getattr(entity, "post_id", None), error=repr(exc))
        return None
    closed = _roll_up_from(ctx, str(entity.post_id), entity.tier, summaries)
    return "rolled up: {0}".format(", ".join(closed)) if closed else None


def _roll_up_from(ctx: Any, child_id: str, child_tier: Optional[str], summaries: dict) -> list[str]:
    """Recursively close parents above ``child_id``; return ids closed (top-down)."""
    spec = _PARENT_OF.get(child_tier or "")
    if spec is None:
        return []
    parent_tier, child_kind = spec

    child_details = _details(ctx, child_id)
    parent_id = relationships.parent_id(getattr(child_details, "body", None), parent_tier)
    if parent_id is None:
        return []
    info = summaries.get(str(parent_id))
    if info is None or not info.get("is_open", True) or info.get("status") in TERMINAL_TIER_STATUSES:
        return []

    parent_details = _details(ctx, parent_id)
    siblings = relationships.child_ids(getattr(parent_details, "body", None), child_kind)
    if not siblings:
        return []

    statuses = []
    for sid in siblings:
        sib = summaries.get(str(sid))
        if sib is None:
            return []  # a listed child is missing from the remote; do not close blindly
        statuses.append(sib.get("status"))
    if not all(s in TERMINAL_TIER_STATUSES for s in statuses):
        return []

    new_status = "done" if any(s == "done" for s in statuses) else "wont_do"
    _close_parent(ctx, parent_id, parent_details, parent_tier, new_status, len(siblings))
    # reflect locally so the recursion sees the parent as terminal, then go up.
    summaries[str(parent_id)] = {"status": new_status, "tier": parent_tier, "is_open": False}
    platform_log.log_event(
        "rollup_closed", post_id=str(parent_id), tier=parent_tier, status=new_status, children=len(siblings)
    )
    return [str(parent_id)] + _roll_up_from(ctx, str(parent_id), parent_tier, summaries)


def _close_parent(ctx: Any, post_id: Any, details: Any, tier: str, new_status: str, n_children: int) -> None:
    entity = Entity.for_labels(
        post_id=str(post_id),
        labels=[str(getattr(lbl, "name", lbl)) for lbl in getattr(details, "labels", []) or []],
    )
    _set_status(ctx, entity, new_status)
    _close(ctx, post_id)
    _comment(
        ctx, post_id,
        "All {0} child {1} are finished; rolling this {2} up to `{3}` and closing.".format(
            n_children, "issues" if tier == "ticket" else "tickets", tier, new_status
        ),
    )


def _entry_summaries(ctx: Any) -> dict:
    """One ``list_entries`` call -> ``{id: {status, tier, is_open}}`` for all posts."""
    res = ctx.remote.list_entries(is_open=None)
    out: dict[str, dict] = {}
    for summary in getattr(res, "data", None) or []:
        names = [str(getattr(lbl, "name", lbl)) for lbl in getattr(summary, "labels", []) or []]
        out[str(summary.id)] = {
            "status": Entity.status_from_labels(names),
            "tier": Entity.tier_from_labels(names),
            "is_open": bool(getattr(summary, "is_open", True)),
        }
    return out


def _details(ctx: Any, post_id: Any) -> Any:
    res = ctx.remote.get_entry(post_id)
    return getattr(res, "data", None)
