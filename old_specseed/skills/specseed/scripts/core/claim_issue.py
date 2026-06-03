"""
claim_issue.py [issue_id] [--skip ID,ID...]

Atomic issue claim. Issues are the claimable/executable unit (they carry
plan.md, step_reports/, artifacts). Critical path runs at the TICKET level
(tickets_analyze.py); this script picks the next ready ISSUE and claims it.

Two modes:
  - `claim_issue.py`            → pick the next ready issue AND claim it, all
                                  under one lock (no pick/claim race window).
  - `claim_issue.py <issue_id>` → claim that specific issue.

"Ready" issue (for auto-pick): status in {todo, blocked}, unclaimed, its own
intra-ticket depends_on all resolved, AND its parent ticket is reachable (the
ticket's depends_on tickets are all resolved). "Resolved" = done | wont_do |
deprecated. Issues in a handoff state (in_review / awaiting_approval) are NOT
pickable — the work is in flight and the original claim is retained; another
agent claiming would steal it. Among ready issues,
ordering is: SPRINT rank, then PRIORITY (high>medium>low; issue `priority`
overrides, else inherits the parent ticket's, else medium), then `created_at`
(FIFO among manual items, which carry it; spec-derived issues lack it → +inf, so
they tie on this key and fall through to topo exactly as before — no regression),
then parent-ticket topo rank, then issue topo rank, then id. So a high-priority
manual issue placed in the in_progress sprint jumps to the top of the pile. NOTE:
critical-path weighting is NOT applied here — readiness + topo only. Run
tickets_analyze.py for the ticket critical path.

Sprint scoping (--sprint-scope, default `spill`): if sprints.json is present,
issues whose parent ticket is in the `in_progress` sprint are picked first; only
when NONE of those are ready does the picker spill to the next planned sprint (by
sprint `order`), and finally to backlog (tickets in no/done sprint). `current`
forbids the spill (in_progress sprint only); `all` ignores sprints entirely (legacy
behaviour). With no sprints.json, behaviour is always `all`. Sprint scoping
applies only to AUTO-PICK; claiming a specific issue id is never sprint-gated.

Uses fcntl.flock on issues.json for the read-verify-write critical section.
tickets.json + sprints.json are read (unlocked) for parent-ticket reachability,
sprint rank + ordering; if absent, all parents are reachable and sprint rank is
flat.

Output: JSON to stdout. Exit 0 for normal outcomes (claimed or refused);
2 for lock-timeout / IO / schema errors.
"""

import argparse
import fcntl
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# "Resolved" = terminal: stops blocking deps + counts a parent done.
RESOLVED = {"done", "wont_do", "deprecated"}
# Parent tickets in these states make their issues unreachable (abandoned work).
ABANDONED = {"wont_do", "deprecated"}
# In-flight handoff states: claim retained, NOT auto-pickable / not stealable.
HANDOFF = {"in_review", "awaiting_approval"}
PICKABLE = {"todo", "blocked"}

# priority sort tiers (lower = picked first). `normal` is accepted as an alias for
# `medium`; anything unknown/absent defaults to medium.
PRIORITY_RANK = {"high": 0, "medium": 1, "normal": 1, "low": 2}


def effective_priority_rank(issue, tickets):
    """Issue's own `priority` overrides; else inherit the parent ticket's; else medium."""
    pr = issue.get("priority")
    if pr is None and tickets:
        parent = tickets.get(issue.get("ticket")) if issue.get("ticket") else None
        if parent:
            pr = parent.get("priority")
    return PRIORITY_RANK.get(pr, 1)


def created_sort_key(issue):
    """Epoch seconds of `created_at` (manual items carry it); missing → +inf so
    spec-derived issues sort AFTER same-priority dated manuals only as a final
    tiebreak (they normally differ earlier in the key)."""
    ts = parse_iso(issue.get("created_at"))
    if ts is None:
        return float("inf")
    try:
        return ts.timestamp()
    except (OverflowError, OSError, ValueError):
        return float("inf")


def default_agent_id():
    env = os.environ.get("CLAUDE_AGENT_ID")
    return env if env else f"{socket.gethostname()}-{os.getpid()}"


def parse_iso(ts):
    if not isinstance(ts, str):
        return None
    try:
        s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def acquire_lock(file_path, timeout_secs):
    try:
        f = open(file_path, "r+", encoding="utf-8")
    except OSError as e:
        print(f"ERROR: cannot open {file_path}: {e}", file=sys.stderr)
        return None
    deadline = time.monotonic() + timeout_secs
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except BlockingIOError:
            if time.monotonic() >= deadline:
                f.close()
                return None
            time.sleep(0.05)


def report(payload, exit_code=0):
    print(json.dumps(payload))
    sys.exit(exit_code)


def topo_rank(graph):
    """graph: {node: [deps]}. Returns {node: rank}; falls back to alpha on cycle."""
    from graphlib import TopologicalSorter, CycleError
    try:
        ts = TopologicalSorter({n: set(d) for n, d in graph.items()})
        order = list(ts.static_order())
    except CycleError:
        order = sorted(graph.keys())
    return {n: i for i, n in enumerate(order)}


def load_tickets(tickets_path):
    if not tickets_path.exists():
        return None
    try:
        return json.loads(tickets_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_sprints(sprints_path):
    if not sprints_path.exists():
        return None
    try:
        return json.loads(sprints_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# sprint rank tiers (lower = picked first)
CURRENT_TIER, PLANNED_TIER, BACKLOG_TIER = 0, 1, 2


def sprint_rank_map(sprints):
    """sprint_id -> (tier, order). The in_progress sprint ranks ahead of planned
    (by order); done/deprecated sprints are treated as backlog. Returns {} if no
    sprints data."""
    if not sprints:
        return {}
    out = {}
    for sid, s in sprints.items():
        status = s.get("status", "planned")
        order = s.get("order", 0)
        if status == "in_progress":
            out[sid] = (CURRENT_TIER, order)
        elif status == "planned":
            out[sid] = (PLANNED_TIER, order)
        else:  # done / deprecated → backlog priority
            out[sid] = (BACKLOG_TIER, order)
    return out


def ticket_sprint_map(tickets, sprints):
    """ticket_id -> sprint_id. Prefer the per-ticket `sprint` field; fall back
    to sprint membership lists."""
    out = {}
    if sprints:
        for sid, s in sprints.items():
            for tid in s.get("tickets", []) or []:
                out.setdefault(tid, sid)
    if tickets:
        for tid, t in tickets.items():
            sp = t.get("sprint")
            if sp:
                out[tid] = sp
    return out


def issue_sprint_rank(issue, tickets, t_sprint, s_rank):
    """(tier, order) for an issue, via its parent ticket's sprint. Backlog tier
    for unknown/unassigned. Flat (0,0) when there's no sprint data."""
    if not s_rank:
        return (0, 0)
    parent = issue.get("ticket")
    sid = t_sprint.get(parent) if parent else None
    if sid is None:
        return (BACKLOG_TIER, 0)
    return s_rank.get(sid, (BACKLOG_TIER, 0))


def ticket_done_map(tickets, issues):
    """ticket_id -> bool. A ticket counts as done if its status is resolved
    (done/wont_do/deprecated), OR it has issues and all of them are resolved.
    This is derived LIVE from issues.json so dependent tickets unblock without
    needing a re-assemble during execution."""
    by_ticket = {}
    for ie in issues.values():
        tk = ie.get("ticket")
        if tk:
            by_ticket.setdefault(tk, []).append(ie.get("status"))
    done = {}
    for tid, t in (tickets or {}).items():
        if t.get("status") in RESOLVED:
            done[tid] = True
            continue
        statuses = by_ticket.get(tid)
        done[tid] = bool(statuses) and all(s in RESOLVED for s in statuses)
    return done


def ticket_reachable(parent, tickets, tdone):
    """Reachable if the parent exists, isn't abandoned (wont_do/deprecated), and
    all its depends_on tickets are resolved (per tdone). Unknown tickets.json /
    parent → True."""
    if tickets is None or parent is None:
        return True
    t = tickets.get(parent)
    if t is None:
        return True  # can't resolve; don't block
    if t.get("status") in ABANDONED:
        return False
    for d in t.get("depends_on", []) or []:
        if d not in tickets:
            continue
        if not tdone.get(d, False):
            return False
    return True


def issue_deps_met(issue, issues):
    for d in issue.get("depends_on", []) or []:
        dep = issues.get(d)
        if dep is None:
            continue  # dangling dep — validator's job; don't block here
        if dep.get("status") not in RESOLVED:
            return False
    return True


def pick_next(issues, tickets, tdone, skip, t_sprint, s_rank, sprint_scope):
    """Return id of next ready issue, or None.

    sprint_scope ∈ {current, spill, all}. With sprint data present, eligible
    issues are sorted by sprint tier/order first (in_progress before planned
    before backlog) — so the picker spills to the next sprint only when nothing
    in the in_progress sprint is ready. `current` filters to the in_progress
    sprint only; `all` ignores sprint rank.
    """
    ticket_graph = {}
    if tickets:
        ticket_graph = {tid: list(t.get("depends_on", []) or [])
                        for tid, t in tickets.items()}
    t_rank = topo_rank(ticket_graph) if ticket_graph else {}
    i_graph = {iid: [d for d in (ie.get("depends_on", []) or []) if d in issues]
               for iid, ie in issues.items()}
    i_rank = topo_rank(i_graph)
    use_sprints = bool(s_rank) and sprint_scope != "all"

    eligible = []
    for iid, ie in issues.items():
        if iid in skip:
            continue
        if ie.get("status") not in PICKABLE:
            continue
        if ie.get("claimed_by") is not None:
            continue
        if not issue_deps_met(ie, issues):
            continue
        if not ticket_reachable(ie.get("ticket"), tickets, tdone):
            continue
        if use_sprints and sprint_scope == "current":
            tier, _ = issue_sprint_rank(ie, tickets, t_sprint, s_rank)
            if tier != CURRENT_TIER:
                continue
        eligible.append(iid)

    if not eligible:
        return None
    eligible.sort(key=lambda i: (
        issue_sprint_rank(issues[i], tickets, t_sprint, s_rank) if use_sprints else (0, 0),
        effective_priority_rank(issues[i], tickets),
        created_sort_key(issues[i]),
        t_rank.get(issues[i].get("ticket"), 1_000_000),
        i_rank.get(i, 1_000_000),
        i,
    ))
    return eligible[0]


def do_claim(iid, issues, agent, stale_hours, no_stale, tickets, tdone):
    """Mutate issues[iid] to claimed; return (payload, claimed_bool)."""
    if iid not in issues:
        return {"claimed": False, "issue_id": iid,
                "reason": "issue not found in issues.json", "stale": False}, False
    ie = issues[iid]
    status = ie.get("status", "todo")

    if status in RESOLVED:
        return {"claimed": False, "issue_id": iid,
                "reason": f"issue is {status}", "stale": False}, False
    if status in HANDOFF:
        # in_review / awaiting_approval: work is in flight, claim retained.
        # Don't let another agent steal it. A human/reviewer (or the owner)
        # moves it on, or resets it to in_progress to reclaim.
        return {"claimed": False, "issue_id": iid,
                "reason": f"issue is {status} (in handoff — not claimable; "
                          f"reset to in_progress to reclaim)",
                "current_claim": {"by": ie.get("claimed_by"),
                                  "at": ie.get("claimed_at")},
                "stale": False}, False
    if not issue_deps_met(ie, issues):
        return {"claimed": False, "issue_id": iid,
                "reason": "issue depends_on not all done", "stale": False}, False
    if not ticket_reachable(ie.get("ticket"), tickets, tdone):
        return {"claimed": False, "issue_id": iid,
                "reason": f"parent ticket {ie.get('ticket')} not reachable "
                          f"(its deps not done)", "stale": False}, False

    previous_claim = None
    if status == "in_progress":
        ca, cb = ie.get("claimed_at"), ie.get("claimed_by")
        ts = parse_iso(ca) if ca else None
        if ts is None:
            stale, age_h = True, None
        else:
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
            stale = age_h >= stale_hours
        if stale and not no_stale:
            previous_claim = {"by": cb, "at": ca, "stale_hours": age_h}
        else:
            return {"claimed": False, "issue_id": iid, "reason": "already claimed",
                    "current_claim": {"by": cb, "at": ca}, "stale": stale}, False

    new_ts = now_iso()
    ie["status"] = "in_progress"
    ie["claimed_at"] = new_ts
    ie["claimed_by"] = agent
    return {"claimed": True, "issue_id": iid, "claimed_at": new_ts,
            "claimed_by": agent, "previous_claim": previous_claim}, True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("issue_id", nargs="?", default=None,
                   help="claim this issue; omit to auto-pick the next ready one")
    p.add_argument("--skip", default="",
                   help="comma-separated issue IDs to exclude from auto-pick "
                        "(e.g. a parallel agent is already on them)")
    p.add_argument("--agent", default=None)
    p.add_argument("--stale-hours", type=float, default=3.0)
    p.add_argument("--no-stale-takeover", action="store_true")
    p.add_argument("--lock-timeout", type=float, default=10.0)
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--issues-path", default=None)
    p.add_argument("--tickets-path", default=None)
    p.add_argument("--sprints-path", default=None)
    p.add_argument("--sprint-scope", choices=["current", "spill", "all"],
                   default="spill",
                   help="auto-pick scope: current sprint only / spill to next "
                        "(default) / ignore sprints")
    p.add_argument("--peek", action="store_true",
                   help="read-only: print the next ready issue's id+type+difficulty "
                        "WITHOUT claiming it (no lock, no write). Lets the runner pick "
                        "a per-function/difficulty agent before spawning it.")
    args = p.parse_args()

    pm = Path(args.pm_dir)
    issues_path = Path(args.issues_path) if args.issues_path else pm / "issues.json"
    tickets_path = Path(args.tickets_path) if args.tickets_path else pm / "tickets.json"
    sprints_path = Path(args.sprints_path) if args.sprints_path else pm / "sprints.json"
    agent = args.agent or default_agent_id()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    if not issues_path.exists():
        print(f"ERROR: {issues_path} not found (run issues_assemble.py)", file=sys.stderr)
        sys.exit(2)

    tickets = load_tickets(tickets_path)
    sprints = load_sprints(sprints_path)
    s_rank = sprint_rank_map(sprints)
    t_sprint = ticket_sprint_map(tickets, sprints)

    # --peek: read-only next-ready selection (no lock, no claim). The runner uses
    # this to learn the issue's type+difficulty so it can choose the right agent
    # config before spawning. Race-free under the single-writer invariant.
    if args.peek:
        try:
            issues = json.loads(issues_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"ERROR: malformed issues.json: {e}", file=sys.stderr)
            sys.exit(2)
        tdone = ticket_done_map(tickets, issues)
        target = args.issue_id or pick_next(issues, tickets, tdone, skip, t_sprint,
                                            s_rank, args.sprint_scope)
        ie = issues.get(target) if target else None
        report({"peek": True, "issue_id": target,
                "type": (ie or {}).get("type"),
                "difficulty": (ie or {}).get("difficulty")})

    f = acquire_lock(issues_path, args.lock_timeout)
    if f is None:
        print(json.dumps({"claimed": False, "reason": "lock-timeout",
                          "detail": f"could not acquire lock within {args.lock_timeout}s"}),
              file=sys.stderr)
        sys.exit(2)
    try:
        f.seek(0)
        try:
            issues = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: malformed issues.json: {e}", file=sys.stderr)
            sys.exit(2)

        tdone = ticket_done_map(tickets, issues)

        target = args.issue_id
        if target is None:
            target = pick_next(issues, tickets, tdone, skip, t_sprint, s_rank,
                               args.sprint_scope)
            if target is None:
                reason = ("no ready issue in the in_progress sprint"
                          if (s_rank and args.sprint_scope == "current")
                          else "no ready issue to claim")
                report({"claimed": False, "issue_id": None,
                        "reason": reason, "stale": False})

        payload, claimed = do_claim(target, issues, agent, args.stale_hours,
                                    args.no_stale_takeover, tickets, tdone)
        if not claimed:
            report(payload)

        new_content = json.dumps(issues, indent=2) + "\n"
        f.seek(0)
        f.truncate()
        f.write(new_content)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
        report(payload)
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


if __name__ == "__main__":
    main()
