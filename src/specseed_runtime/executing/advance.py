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
from dataclasses import dataclass
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
# child tier -> (parent tier, the upward-link field on each child node). Children
# are found by THEIR link up, never by a downward list in the parent body: posts
# are created parent-before-child, so a parent never knows its child ids.
_PARENT_OF = {"issue": ("ticket", "ticket"), "ticket": ("epic", "epic")}

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
# merge gate
# --------------------------------------------------------------------------- #
# Markers stamped on a gate comment so both the runtime and the UI can tell which
# kind of approval box this is:
#   PURE merge gate     -> the work is already accepted; only the merge is left.
#                          👍 merges, 👎 leaves the branch unmerged for a human.
#   COMBINED work+merge -> the work needs sign-off AND the merge is gated.
#                          ❤️ approves+merges in one step, 👍 approves the work only
#                          (the merge becomes a pure gate), 👎 rejects the work.
MERGE_GATE_MARKER = "<!-- specseed:merge-gate -->"
WORK_MERGE_GATE_MARKER = "<!-- specseed:work-merge-gate -->"


@dataclass
class WorkTransition:
    """Outcome of a lifecycle transition. ``merge`` tells ``dispatch`` to run the
    issue-branch merge into primary now - advance owns remote STATE, dispatch owns
    git. ``detail`` is the human-readable line for the HandlerOutcome (None when
    nothing was applied / still waiting on a human)."""

    detail: Optional[str]
    merge: bool = False


def _item_body(item: Any) -> Optional[str]:
    if isinstance(item, dict):
        return item.get("body")
    return getattr(item, "body", None)


def _primary_branch_name(ctx: Any) -> str:
    return (getattr(ctx, "config", {}) or {}).get("specseed_primary_branch") or "main"


def _merge_gated(ctx: Any) -> bool:
    """True when merging into primary needs explicit human authorization."""
    try:
        return not ctx.permissions.can_merge_to_primary()
    except Exception:
        return False


def _issue_has_branch(entity: Any) -> bool:
    """Only code issues carry a git branch worth merging (tickets/epics roll up)."""
    return getattr(entity, "tier", None) == "issue"


def _pure_merge_gate(conversation: Any) -> bool:
    """The active awaiting_approval is a PURE merge gate (work already accepted)."""
    for item in conversation or []:
        body = _item_body(item)
        if body and MERGE_GATE_MARKER in str(body):
            return True
    return False


def close_issue_done(ctx: Any, entity: Any) -> str:
    """Settle an issue as done: set the label, close the entry, roll up the parent."""
    _set_status(ctx, entity, "done")
    _close(ctx, entity.post_id)
    rolled = roll_up(ctx, entity)
    return "done (closed)" + ("; " + rolled if rolled else "")


def park_unmerged(ctx: Any, entity: Any) -> None:
    """Park an issue ``blocked`` because its merge needs a human. The branch is left
    intact and the issue is NOT closed - an unmerged issue must never read as done."""
    _set_status(ctx, entity, "blocked")


def _merge_gate_comment(ctx: Any, entity: Any) -> str:
    primary = _primary_branch_name(ctx)
    return (
        "**Merge approval** — the work is complete and committed on this issue's "
        "branch. Merging into `{0}` is gated, so it needs your go-ahead:\n\n"
        "- react 👍 (or comment `approve {1}`) to merge into `{0}` now, or\n"
        "- react 👎 (or comment `reject {1}`) to leave the branch unmerged for you "
        "to merge manually.\n\n"
        "Until then this stays `awaiting_approval`.\n\n{2}".format(
            primary, entity.post_id, MERGE_GATE_MARKER
        )
    )


def _open_merge_gate(ctx: Any, entity: Any, reason: str) -> WorkTransition:
    """Park the issue awaiting_approval as a pure merge gate (merge pending)."""
    _set_status(ctx, entity, "awaiting_approval")
    _comment(ctx, entity.post_id, _merge_gate_comment(ctx, entity))
    return WorkTransition(
        "{0} -> awaiting_approval (merge gate; merge pending)".format(reason)
    )


def _settle_or_merge(ctx: Any, entity: Any, reason: str) -> WorkTransition:
    """Reach the end of work: close now (no branch / merge auto-on closes after the
    merge), open a merge gate (merge gated), or hand dispatch the merge (auto-on)."""
    if not _issue_has_branch(entity):
        return WorkTransition("{0} -> {1}".format(reason, close_issue_done(ctx, entity)))
    if _merge_gated(ctx):
        return _open_merge_gate(ctx, entity, reason)
    # Auto-merge: leave the status as-is; dispatch merges then closes on success, so
    # the issue never shows `done` with code still off primary.
    return WorkTransition("{0} -> merging into primary".format(reason), merge=True)


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
) -> WorkTransition:
    """Apply the lifecycle transition implied by a successful agent run.

    ``intent`` is ``dispatch.AgentIntent.{IMPLEMENT,REVIEW}``. Returns a
    ``WorkTransition`` (detail + whether dispatch should now run the merge).
    """
    if not _can_write(ctx):
        return WorkTransition("remote writes not permitted; no transition applied")
    if _is_stale(ctx, entity):
        return WorkTransition(
            "stale event; remote already advanced past {0}, skipping".format(entity.status)
        )
    if intent == "implement":
        return _advance_after_implement(ctx, entity, state_result, result)
    if intent == "review":
        return _advance_after_review(ctx, entity, result, conversation)
    return WorkTransition("no transition for intent {0!r}".format(intent))


def _advance_after_implement(
    ctx: Any, entity: Any, state_result: Any, result: Any = None
) -> WorkTransition:
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
        return WorkTransition("implement recommends spec-change -> blocked + draft adapt")
    status = str(report.get("status") or "").lower()
    if status in ("blocked", "needs_input"):
        detail = report.get("summary") or "(no detail provided)"
        word = "blocked" if status == "blocked" else "a human decision"
        _comment(
            ctx, entity.post_id,
            "Implementation could not complete - reported {0}:\n\n{1}".format(word, detail),
        )
        _set_status(ctx, entity, "blocked")
        return WorkTransition("implement reported {0} -> blocked".format(status))
    if getattr(state_result, "review_required", False):
        _set_status(ctx, entity, "in_review")
        return WorkTransition("implement done -> in_review")
    if getattr(state_result, "hitl_required", False):
        _set_status(ctx, entity, "awaiting_approval")
        _comment(
            ctx, entity.post_id,
            "Implementation finished; human sign-off required. An approver must "
            "comment `approve {0}` to complete.".format(entity.post_id),
        )
        return WorkTransition("implement done -> awaiting_approval")
    return _settle_or_merge(ctx, entity, "implement done")


def _advance_after_review(
    ctx: Any, entity: Any, result: Any, conversation: Any
) -> WorkTransition:
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

    # approve + confident -> done (merge gate / merge / close per config).
    if verdict == "approve" and confidence >= threshold:
        return _settle_or_merge(ctx, entity, "review passed")

    # approve but UNDER the confidence bar -> a human looks, NOT a reimplement and
    # NOT a spec change. "Reviewer thinks it's fine but isn't sure" is the textbook
    # case for human sign-off; reimplementing fine code just burns cycles. When the
    # merge is also gated this becomes a COMBINED gate: ❤️ signs off AND merges.
    if verdict == "approve":
        _set_status(ctx, entity, "awaiting_approval")
        _comment(ctx, entity.post_id, _below_confidence_comment(ctx, entity, confidence, threshold))
        return WorkTransition("review approve below confidence bar -> awaiting_approval")

    # verdict == changes: reimplement until the loop is exhausted.
    if attempt < max_attempts:
        _set_status(ctx, entity, "todo")
        return WorkTransition(
            "review requested changes -> todo (reimplement, attempt {0})".format(attempt)
        )

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
        return WorkTransition("review exhausted + recommend_spec_change -> blocked + draft adapt")
    _comment(
        ctx, entity.post_id,
        "Review still requesting changes after {0} attempts. Parked `blocked` for a human "
        "decision: comment `approve {1}` to accept as-is, reply with guidance to retry, or "
        "open a `spec-change:adapt` if the spec itself is wrong.".format(
            max_attempts, entity.post_id
        ),
    )
    return WorkTransition("review exhausted -> blocked (human decision; no adapt)")


def _below_confidence_comment(ctx: Any, entity: Any, confidence: float, threshold: float) -> str:
    base = (
        "Review approved but confidence {0:.2f} is below the {1:.2f} bar. Parked for "
        "human sign-off.".format(confidence, threshold)
    )
    if _merge_gated(ctx) and _issue_has_branch(entity):
        # Combined gate: the work needs sign-off AND the merge is gated.
        primary = _primary_branch_name(ctx)
        return (
            "{0}\n\nMerging into `{1}` is also gated, so you have three choices:\n\n"
            "- react ❤️ (or comment `merge {2}`) to approve AND merge into `{1}` in one step,\n"
            "- react 👍 (or comment `approve {2}`) to approve the work now and leave the "
            "merge as a follow-up gate, or\n"
            "- reply with what to change to revise, or comment `retry` to re-review.\n\n"
            "{3}".format(base, primary, entity.post_id, WORK_MERGE_GATE_MARKER)
        )
    return (
        "{0} Comment `approve {1}` (or 👍) to complete, reply with what to change to "
        "revise, or comment `retry` to re-review.".format(base, entity.post_id)
    )


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
) -> WorkTransition:
    """Resolve an ``awaiting_approval`` entity from a human's signal.

    Returns a ``WorkTransition`` (``detail`` None = nothing applied / still waiting;
    ``merge`` True = dispatch should now run the issue-branch merge).

    Reaction vocabulary: 👍 approves the single primary gate; ❤️ approves every
    stacked gate at once (work AND merge); 👎 rejects. So:

    * COMBINED work+merge gate (review under the bar AND merge gated): ❤️ signs off
      and merges; 👍 signs off the work only and opens a pure merge gate; 👎 rejects.
    * PURE merge gate (work already accepted): 👍/❤️ authorize the merge; 👎 declines
      it and settles the issue done with the branch left for a manual merge.
    * plain completion gate (merge auto-on): 👍 completes (+ dispatch merges).
    * pre-work HITL gate: 👍 resumes the issue to ``todo``.
    * prose / ``retry`` -> ``todo`` to revise (work gate only); bare reject / 👎 with
      no guidance -> post the options prompt once and wait.
    """
    if entity.status != "awaiting_approval":
        return WorkTransition(None)
    approved = getattr(state_result, "approved_by", None)
    merge_approved = getattr(state_result, "merge_approved_by", None)
    rejected = getattr(state_result, "rejected_by", None)
    is_comment = action in _COMMENT_ACTIONS
    # Nothing to act on (a bare label re-sync, say): bow out before any remote read.
    if not approved and not merge_approved and not rejected and not is_comment:
        return WorkTransition(None)
    if not _can_write(ctx):
        return WorkTransition(None)
    if _is_stale(ctx, entity):
        return WorkTransition("stale event; remote already advanced past awaiting_approval")

    pure_merge = _pure_merge_gate(conversation)
    primary = _primary_branch_name(ctx)

    # Approval wins over a stray rejection if somehow both are present.
    if approved or merge_approved:
        approver = (merge_approved or approved)[0]
        # Pure merge gate: the work is already accepted; any approval authorizes the
        # merge. dispatch runs it then closes on success.
        if pure_merge:
            _comment(
                ctx, entity.post_id,
                "Merge approved by {0}; merging into `{1}`.".format(approver, primary),
            )
            return WorkTransition("merge gate approved by {0} -> merging".format(approver), merge=True)
        # Completion gate (work was reviewed). Pre-work/HITL gates (0 attempts) keep
        # the original resume-to-todo behavior.
        if _count_review_attempts(conversation) > 0:
            if merge_approved:
                # ❤️ : approve the work AND merge in one step.
                _comment(
                    ctx, entity.post_id,
                    "Approved + merge by {0}; merging into `{1}`.".format(approver, primary),
                )
                return WorkTransition(
                    "approval gate approved+merge by {0} -> merging".format(approver), merge=True
                )
            # 👍 : approve the work only. Merge gated -> open a pure merge gate.
            if _merge_gated(ctx) and _issue_has_branch(entity):
                _comment(ctx, entity.post_id, "Work approved by {0}.".format(approver))
                return _open_merge_gate(ctx, entity, "approval gate approved by {0}".format(approver))
            # Merge auto-on (or nothing to merge): complete now.
            settle = _settle_or_merge(ctx, entity, "approval gate approved by {0}".format(approver))
            _comment(ctx, entity.post_id, "Approved by {0}; completing.".format(approver))
            return settle
        _set_status(ctx, entity, "todo")
        _comment(ctx, entity.post_id, "Approved by {0}; work may proceed.".format(approver))
        return WorkTransition("approval gate -> todo (resume work)")

    # Rejection on a PURE merge gate = decline the runtime merge: settle done with the
    # branch left for a manual merge (the human owns it). Not a rework.
    if pure_merge and rejected:
        detail = close_issue_done(ctx, entity)
        _comment(
            ctx, entity.post_id,
            "Merge declined by {0}; issue marked done with the branch left unmerged for "
            "you to merge manually.".format(rejected[0]),
        )
        return WorkTransition("merge gate declined -> {0} (unmerged)".format(detail))

    # Not approved. A directive only counts off a fresh comment, never a stale one
    # surfaced by a label/reaction event. On a pure merge gate prose is not a rework.
    kind, _text = (None, None)
    if is_comment and not pure_merge:
        kind, _text = _human_directive(conversation, getattr(ctx, "config", {}))
    if kind == "retry":
        _set_status(ctx, entity, "todo")
        _comment(ctx, entity.post_id, "Retrying implementation with no new feedback.")
        return WorkTransition("approval gate -> todo (retry, no feedback)")
    if kind == "guidance":
        _set_status(ctx, entity, "todo")
        _comment(
            ctx, entity.post_id,
            "Taking your comment as change guidance; re-running implementation.",
        )
        return WorkTransition("approval gate -> todo (revise with guidance)")

    # Bare reject / 👎 with no guidance: ask what to do (once), then wait.
    if (kind == "reject_bare" or rejected) and not _options_already_posted(ctx, entity.post_id):
        _post_reject_options(ctx, entity)
        return WorkTransition("approval gate: rejected without guidance -> options prompt posted")
    return WorkTransition(None)


def resolve_blocked(
    ctx: Any, entity: Any, state_result: Any, conversation: Any = None, action: Any = None
) -> WorkTransition:
    """Human bypass for a ``blocked`` issue (e.g. parked by a spec-change recommend).

    The recommendation is a suggestion, never a forced path: a human can still
    approve the work (force done) or hand the implementer guidance (re-run). Returns
    a ``WorkTransition`` (``detail`` None = nothing applied; ``merge`` True = dispatch
    should run the merge). A force-approve routes through the same merge gate as any
    other completion, so an overridden block never lands code on primary unreviewed.
    """
    if getattr(entity, "status", None) != "blocked":
        return WorkTransition(None)
    approved = getattr(state_result, "approved_by", None)
    merge_approved = getattr(state_result, "merge_approved_by", None)
    is_comment = action in _COMMENT_ACTIONS
    # Only a human approval or a fresh comment can move a block; skip otherwise.
    if not approved and not merge_approved and not is_comment:
        return WorkTransition(None)
    if not _can_write(ctx):
        return WorkTransition(None)
    if _is_stale(ctx, entity):
        return WorkTransition("stale event; remote already advanced past blocked")
    primary = _primary_branch_name(ctx)
    if approved or merge_approved:
        approver = (merge_approved or approved)[0]
        no_branch = not _issue_has_branch(entity)
        # ❤️ authorizes the merge outright (bypasses the gate); 👍 with merge gated
        # opens a merge gate first (an override must still get a merge sign-off before
        # code lands on primary); 👍 with merge auto-on (or no branch) completes now.
        if merge_approved or no_branch or not _merge_gated(ctx):
            if no_branch:
                detail = close_issue_done(ctx, entity)
                _comment(ctx, entity.post_id, "Approved by {0} over the block; completing.".format(approver))
                return WorkTransition("blocked -> human override by {0} -> {1}".format(approver, detail))
            _comment(
                ctx, entity.post_id,
                "Approved by {0} over the block; merging into `{1}`.".format(approver, primary),
            )
            return WorkTransition(
                "blocked -> human override by {0} -> merging into primary; completing".format(approver),
                merge=True,
            )
        _comment(ctx, entity.post_id, "Approved by {0} over the block.".format(approver))
        return _open_merge_gate(ctx, entity, "blocked -> human override by {0}".format(approver))
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
        return WorkTransition(
            "blocked -> todo ({0})".format("retry" if kind == "retry" else "revise with guidance")
        )
    return WorkTransition(None)


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

    Called after an issue (or ticket) reaches a terminal state. Children are found
    by THEIR upward body link (an issue's ``Ticket: #N``, a ticket's ``Epic: #N``):
    a parent closes once every post that links up to it is terminal, walking issue
    -> ticket -> epic. Upward links are the only ones that reliably exist - posts
    are created parent-before-child, so a parent body never lists child ids it could
    not know at creation. Idempotent and remote-truth checked, so re-running is safe
    and a parent already terminal is left alone. Returns a short detail string, or
    ``None`` when nothing rolled up.
    """
    if not _can_write(ctx):
        return None
    try:
        nodes = _work_nodes(ctx)
    except Exception as exc:
        platform_log.log_event("rollup_error", post_id=getattr(entity, "post_id", None), error=repr(exc))
        return None
    closed = _roll_up_from(ctx, str(entity.post_id), entity.tier, nodes)
    return "rolled up: {0}".format(", ".join(closed)) if closed else None


def _roll_up_from(ctx: Any, child_id: str, child_tier: Optional[str], nodes: dict) -> list[str]:
    """Recursively close parents above ``child_id``; return ids closed (top-down)."""
    spec = _PARENT_OF.get(child_tier or "")
    if spec is None:
        return []
    parent_tier, link_key = spec

    child = nodes.get(str(child_id))
    parent_id = child.get(link_key) if child else None
    if parent_id is None:
        return []
    parent = nodes.get(str(parent_id))
    if parent is None or not parent.get("is_open", True) or parent.get("status") in TERMINAL_TIER_STATUSES:
        return []

    # Siblings = every post of the child's tier that links up to this parent.
    siblings = [
        nid for nid, node in nodes.items()
        if node.get("tier") == child_tier and str(node.get(link_key)) == str(parent_id)
    ]
    if not siblings:
        return []
    statuses = [nodes[s].get("status") for s in siblings]
    if not all(s in TERMINAL_TIER_STATUSES for s in statuses):
        return []

    new_status = "done" if any(s == "done" for s in statuses) else "wont_do"
    _close_parent(ctx, parent_id, parent, parent_tier, new_status, len(siblings))
    # reflect locally so the recursion sees the parent as terminal, then go up.
    nodes[str(parent_id)] = {**parent, "status": new_status, "is_open": False}
    platform_log.log_event(
        "rollup_closed", post_id=str(parent_id), tier=parent_tier, status=new_status, children=len(siblings)
    )
    return [str(parent_id)] + _roll_up_from(ctx, str(parent_id), parent_tier, nodes)


def _close_parent(ctx: Any, post_id: Any, node: dict, tier: str, new_status: str, n_children: int) -> None:
    entity = Entity.for_labels(post_id=str(post_id), labels=list(node.get("labels") or []))
    _set_status(ctx, entity, new_status)
    _close(ctx, post_id)
    _comment(
        ctx, post_id,
        "All {0} child {1} are finished; rolling this {2} up to `{3}` and closing.".format(
            n_children, "issues" if tier == "ticket" else "tickets", tier, new_status
        ),
    )


def _work_nodes(ctx: Any) -> dict:
    """``{id: {status, tier, is_open, labels, ticket, epic}}`` for every work post.

    One ``list_entries`` plus a body read per post to resolve the upward parent
    links. roll-up runs only when a post reaches terminal, so the extra reads stay
    rare.
    """
    res = ctx.remote.list_entries(is_open=None)
    out: dict[str, dict] = {}
    for summary in getattr(res, "data", None) or []:
        names = [str(getattr(lbl, "name", lbl)) for lbl in getattr(summary, "labels", []) or []]
        tier = Entity.tier_from_labels(names)
        if tier not in ("epic", "ticket", "issue"):
            continue
        body = getattr(_details(ctx, summary.id), "body", None)
        out[str(summary.id)] = {
            "status": Entity.status_from_labels(names),
            "tier": tier,
            "is_open": bool(getattr(summary, "is_open", True)),
            "labels": names,
            "ticket": relationships.parent_id(body, "ticket"),
            "epic": relationships.parent_id(body, "epic"),
        }
    return out


def _details(ctx: Any, post_id: Any) -> Any:
    res = ctx.remote.get_entry(post_id)
    return getattr(res, "data", None)
