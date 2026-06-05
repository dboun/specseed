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
  - pass (approve & confidence >= threshold) -> ``done`` (+ close). The review step
    itself is not human-gated; a human bounces work back via a request-changes
    comment (the ``in_review``/``awaiting_approval`` -> ``in_progress`` transition);
  - fail (changes / low confidence) -> back to ``todo`` to reimplement, until
    ``max_attempts`` review cycles, after which a draft ``spec-change:adapt`` post
    is opened for the human and the issue is parked ``blocked``.

Separately, an ``awaiting_approval`` entity that a configured approver has approved
is resolved here too (no agent): completion gates close, pre-work gates resume.

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
from specseed_runtime.scheduling.spec_change import spec_change_dir


# Hidden marker stamped on every review comment so attempts can be counted from
# the conversation without a side channel.
REVIEW_MARKER = "<!-- specseed:review-attempt -->"
_STATUS_INFIX = ":" + STATUS_LABEL_PREFIX  # ":status:"

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
    rolled = roll_up(ctx, entity)
    return "implement done -> done (closed)" + ("; " + rolled if rolled else "")


def _advance_after_review(ctx: Any, entity: Any, result: Any, conversation: Any) -> str:
    cfg = _review_config(ctx)
    threshold = float(cfg.get("confidence_threshold", 0.75))
    max_attempts = int(cfg.get("max_attempts", 3))

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
        _set_status(ctx, entity, "done")
        _close(ctx, entity.post_id)
        rolled = roll_up(ctx, entity)
        return "review passed -> done (closed)" + ("; " + rolled if rolled else "")

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


def resolve_approval(ctx: Any, entity: Any, state_result: Any, conversation: Any = None) -> Optional[str]:
    """Resolve an ``awaiting_approval`` entity when a configured approver approved.

    Returns a detail string if a transition was applied, else None (still waiting).
    A completion gate (work already reviewed) closes the entry; a pre-work HITL
    gate resumes the issue to ``todo``.
    """
    if entity.status != "awaiting_approval":
        return None
    approved = getattr(state_result, "approved_by", None)
    rejected = getattr(state_result, "rejected_by", None)
    if not approved and not rejected:
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
    rejecter = rejected[0]
    _set_status(ctx, entity, "blocked")
    _comment(
        ctx, entity.post_id,
        "Rejected by {0}; parked `blocked`. Re-approve to resume or cancel "
        "explicitly.".format(rejecter),
    )
    return "approval gate -> blocked (rejected)"


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
    if approved:
        approver = approved[0]
        settled = _settle_docs_for_request(ctx, entity.post_id)
        _set_spec_change_status(ctx, entity, "done")
        note = (
            "Approved by {0}. Spec settled ({1} doc(s)): {2}. Request done.".format(
                approver, len(settled), ", ".join(settled) or "none"
            )
        )
        _comment(ctx, entity.post_id, note)
        platform_log.log_event(
            "spec_change_settled", post_id=entity.post_id, approver=approver, docs=settled
        )
        return "spec-change approved -> settled ({0} docs) + done".format(len(settled))
    rejecter = rejected[0]
    _set_spec_change_status(ctx, entity, "rejected")
    _comment(ctx, entity.post_id, "Rejected by {0}; request rejected.".format(rejecter))
    return "spec-change rejected"


def _review_summary(stdout: str, limit: int = 1500) -> str:
    """Strip the trailing SPECSEED_REVIEW line and cap length for the comment."""
    lines = [ln for ln in stdout.splitlines() if not _REVIEW_LINE_RE.search(ln)]
    text = "\n".join(lines).strip() or "(no review text)"
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n…(truncated)"
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
