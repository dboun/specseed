"""dashboards.py - keep the ROADMAP, CURRENT SPRINT, and SCHEDULE posts in sync.

The seed creates permanent ``management`` posts (ROADMAP, SCHEDULE, CONTROL,
CURRENT SPRINT) and their bodies say "the scheduler keeps this in sync". This
module is that maintenance: it renders the live epic -> ticket -> issue tree from
the work posts and rewrites ROADMAP + CURRENT SPRINT wholesale. SCHEDULE is
skill-authored (composition, order, hours, ★), so it is NOT re-rendered - the
runtime only refreshes its derived bits in place (per-ticket ``(done/total)`` and
each sprint's ``(done|ongoing|planned)`` state); see ``refresh_schedule_body``.

It is deliberately:

* **Idempotent / churn-free.** A dashboard body is rewritten only when the
  rendered text differs from what is already there, so a steady state produces no
  ``edit_entry`` calls (and therefore no ``updated_at`` sync storms).
* **Read-mostly.** It reads work posts from the source of truth and writes only
  the dashboards.

The scheduler gates how often this runs with a cheap :func:`work_signature` so the
(per-post body) reads only happen when something actually changed.

Only Python stdlib is used.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from specseed_runtime.entities.entity_base import Entity
from specseed_runtime.executing import platform_log
from specseed_runtime.executing import relationships

ROADMAP_TITLE = "ROADMAP"
CURRENT_SPRINT_TITLE = "CURRENT SPRINT"
SCHEDULE_TITLE = "SCHEDULE"

_TERMINAL = {"done", "wont_do", "deprecated"}
_WORK_TIERS = ("epic", "ticket", "issue")

ROADMAP_HEADER = (
    "# ROADMAP\n\n"
    "The current work breakdown: epics, their tickets, and the issues under each, "
    "with workflow status. The scheduler keeps this in sync from the work posts; "
    "treat it as a read-only overview. To change scope, open or edit a "
    "`spec-change:adapt` post, not this dashboard.\n"
)

CURRENT_SPRINT_HEADER = (
    "# CURRENT SPRINT\n\n"
    "The active (not-yet-finished) work and its status. The scheduler keeps this "
    "in sync; treat it as a read-only board. Plan the next sprint with a "
    "`spec-change:plan-next-sprint` post.\n"
)


def _label_names(obj: Any) -> list[str]:
    return [str(getattr(lbl, "name", lbl)) for lbl in getattr(obj, "labels", []) or []]


def work_signature(remote: Any) -> Optional[str]:
    """A cheap hash of every work post's id/tier/status/title/open-state.

    The scheduler compares this between polls and only re-renders when it changes,
    so the per-post body reads in :func:`refresh_dashboards` stay rare. Returns
    ``None`` if the listing fails (caller then skips the refresh).
    """
    res = remote.list_entries(is_open=None)
    if not getattr(res, "ok", False):
        return None
    parts = []
    for summary in getattr(res, "data", None) or []:
        names = _label_names(summary)
        tier = Entity.tier_from_labels(names)
        if tier not in _WORK_TIERS:
            continue
        parts.append(
            "{0}|{1}|{2}|{3}|{4}".format(
                summary.id,
                tier,
                Entity.status_from_labels(names),
                int(bool(getattr(summary, "is_open", True))),
                getattr(summary, "title", ""),
            )
        )
    parts.sort()
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def refresh_dashboards(remote: Any) -> dict:
    """Re-render ROADMAP + CURRENT SPRINT from the live work posts.

    Returns a small summary dict. Only rewrites a dashboard whose body actually
    changed.
    """
    summary = {"updated": [], "ok": True}
    listing = remote.list_entries(is_open=None)
    if not getattr(listing, "ok", False):
        return {"ok": False, "error": getattr(listing, "error", "list failed"), "updated": []}
    posts = list(getattr(listing, "data", None) or [])

    nodes = _collect_work(remote, posts)
    dashboards = {
        ROADMAP_TITLE: ROADMAP_HEADER + "\n" + _render_roadmap(nodes),
        CURRENT_SPRINT_TITLE: CURRENT_SPRINT_HEADER + "\n" + _render_sprint(nodes),
    }

    by_title = {getattr(p, "title", None): p for p in posts}
    for title, body in dashboards.items():
        post = by_title.get(title)
        if post is None:
            continue
        current = getattr(remote.get_entry(post.id).data, "body", None)
        if current == body:
            continue
        res = remote.edit_entry(post.id, body=body)
        if getattr(res, "ok", False):
            summary["updated"].append(title)
        else:
            platform_log.log_event("dashboard_edit_failed", title=title, error=getattr(res, "error", None))

    # SCHEDULE is skill-authored (composition, order, hours, ★); the runtime only
    # refreshes its derived bits (per-ticket (done/total) + each sprint's state) in
    # place, so completed work stops reading as stale.
    schedule_post = by_title.get(SCHEDULE_TITLE)
    if schedule_post is not None:
        current = getattr(remote.get_entry(schedule_post.id).data, "body", None)
        refreshed = refresh_schedule_body(current, nodes)
        if refreshed is not None and refreshed != current:
            res = remote.edit_entry(schedule_post.id, body=refreshed)
            if getattr(res, "ok", False):
                summary["updated"].append(SCHEDULE_TITLE)
            else:
                platform_log.log_event("dashboard_edit_failed", title=SCHEDULE_TITLE, error=getattr(res, "error", None))

    if summary["updated"]:
        platform_log.log_event("dashboards_refreshed", updated=summary["updated"])
    return summary


def _collect_work(remote: Any, posts: list) -> dict:
    """Build ``{id: node}`` for every work post; node has tier/status/title/parents."""
    nodes: dict[str, dict] = {}
    for post in posts:
        names = _label_names(post)
        tier = Entity.tier_from_labels(names)
        if tier not in _WORK_TIERS:
            continue
        body = getattr(remote.get_entry(post.id).data, "body", None)
        nodes[str(post.id)] = {
            "id": str(post.id),
            "tier": tier,
            "status": Entity.status_from_labels(names) or "todo",
            "title": getattr(post, "title", None) or "(untitled)",
            "epic": relationships.parent_id(body, "epic"),
            "ticket": relationships.parent_id(body, "ticket"),
        }
    return nodes


def _line(node: dict, indent: int) -> str:
    return "{0}- #{1} {2} — `{3}`".format("  " * indent, node["id"], node["title"], node["status"])


def _render_roadmap(nodes: dict) -> str:
    if not nodes:
        return "_No work posts yet. Open a `spec-change:adapt` request to begin._\n"
    epics = _sorted([n for n in nodes.values() if n["tier"] == "epic"])
    tickets = _sorted([n for n in nodes.values() if n["tier"] == "ticket"])
    issues = _sorted([n for n in nodes.values() if n["tier"] == "issue"])

    lines: list[str] = ["## Work breakdown\n"]
    rendered_tickets: set[str] = set()
    rendered_issues: set[str] = set()

    def emit_issues_for(ticket_id: str, indent: int) -> None:
        for issue in issues:
            if issue["ticket"] == ticket_id:
                lines.append(_line(issue, indent))
                rendered_issues.add(issue["id"])

    def emit_ticket(ticket: dict, indent: int) -> None:
        lines.append(_line(ticket, indent))
        rendered_tickets.add(ticket["id"])
        emit_issues_for(ticket["id"], indent + 1)

    for epic in epics:
        lines.append(_line(epic, 0))
        for ticket in tickets:
            if ticket["epic"] == epic["id"]:
                emit_ticket(ticket, 1)

    orphan_tickets = [t for t in tickets if t["id"] not in rendered_tickets]
    if orphan_tickets:
        lines.append("\n### Tickets without an epic\n")
        for ticket in orphan_tickets:
            emit_ticket(ticket, 0)

    orphan_issues = [i for i in issues if i["id"] not in rendered_issues]
    if orphan_issues:
        lines.append("\n### Issues without a ticket\n")
        for issue in orphan_issues:
            lines.append(_line(issue, 0))

    return "\n".join(lines) + "\n"


# A sprint section header (``## SPRINT_... — name  (state)``) vs the top ``# SCHEDULE``.
_SPRINT_HEADER_RE = re.compile(r"^##\s+\S")
# A ticket line carries its post id (``[PROJ-0001](#7)``) and a ``(done/total)`` counter.
_TICKET_ID_RE = re.compile(r"\(#(\d+)\)")
_COUNTER_RE = re.compile(r"\(\s*\d+\s*/\s*\d+\s*\)")
# The trailing sprint-state annotation, e.g. ``... Foundation sprint  (ongoing)``.
_STATE_RE = re.compile(r"\((?:done|ongoing|planned)\)\s*$")


def refresh_schedule_body(body: Optional[str], nodes: dict) -> Optional[str]:
    """Return ``body`` with derived bits recomputed from live work, else unchanged.

    Rewrites two things only, in place: each ticket line's ``(done/total)`` counter
    (its issues that are ``done`` over its total) and each sprint header's trailing
    ``(done|ongoing|planned)`` state. Everything else - which tickets sit in which
    sprint, their order, hours, ★ - is the skill's and is left exactly as written.
    """
    if not body:
        return body

    progress = _ticket_progress(nodes)
    ticket_status = {n["id"]: n["status"] for n in nodes.values() if n["tier"] == "ticket"}

    out: list[str] = []
    current_header: Optional[int] = None
    header_tickets: dict[int, list[str]] = {}

    for line in body.split("\n"):
        if _SPRINT_HEADER_RE.match(line):
            current_header = len(out)
            header_tickets[current_header] = []
            out.append(line)
            continue
        match = _TICKET_ID_RE.search(line)
        if match and _COUNTER_RE.search(line):
            tid = match.group(1)
            if current_header is not None:
                header_tickets[current_header].append(tid)
            if tid in progress:
                done, total = progress[tid]
                line = _COUNTER_RE.sub("({0}/{1})".format(done, total), line, count=1)
        out.append(line)

    for index, tids in header_tickets.items():
        header = out[index]
        if not _STATE_RE.search(header):
            continue  # no state annotation to maintain; leave the header alone
        state = _sprint_state([ticket_status[t] for t in tids if t in ticket_status])
        if state is not None:
            out[index] = _STATE_RE.sub("({0})".format(state), header)

    return "\n".join(out)


def _ticket_progress(nodes: dict) -> dict:
    """``{ticket_id: (done, total)}`` over issue nodes grouped by their ticket parent."""
    progress: dict[str, tuple] = {}
    for node in nodes.values():
        if node["tier"] != "issue":
            continue
        tid = node.get("ticket")
        if not tid:
            continue
        done, total = progress.get(tid, (0, 0))
        progress[tid] = (done + (1 if node["status"] == "done" else 0), total + 1)
    return progress


def _sprint_state(statuses: list) -> Optional[str]:
    """``done`` if every ticket is terminal, ``planned`` if none started, else ``ongoing``."""
    statuses = [s for s in statuses if s]
    if not statuses:
        return None
    if all(s in _TERMINAL for s in statuses):
        return "done"
    if all(s == "todo" for s in statuses):
        return "planned"
    return "ongoing"


def _render_sprint(nodes: dict) -> str:
    active = _sorted(
        [n for n in nodes.values() if n["tier"] == "issue" and n["status"] not in _TERMINAL]
    )
    if not active:
        return "_No active issues. All known work is finished or none is planned yet._\n"
    lines = ["## Active issues\n"]
    lines.extend(_line(n, 0) for n in active)
    return "\n".join(lines) + "\n"


def _sorted(nodes: list) -> list:
    return sorted(nodes, key=lambda n: int(n["id"]) if str(n["id"]).isdigit() else 0)
