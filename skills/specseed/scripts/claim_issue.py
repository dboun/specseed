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
intra-ticket depends_on all done, AND its parent ticket is reachable (the
ticket's depends_on tickets are all done/deprecated). Among ready issues,
ordering is prerequisite-first: by parent-ticket topo rank, then issue topo
rank, then id. NOTE: critical-path weighting is NOT applied here — readiness +
topo only. Run tickets_analyze.py for the ticket critical path.

Uses fcntl.flock on issues.json for the read-verify-write critical section.
tickets.json is read (unlocked) for parent-ticket reachability + ordering; if
absent, all parents are treated as reachable.

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

DONE_STATES = {"done", "deprecated"}
PICKABLE = {"todo", "blocked"}


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


def ticket_done_map(tickets, issues):
    """ticket_id -> bool. A ticket counts as done if its status is done/
    deprecated, OR it has issues and all of them are done/deprecated. This is
    derived LIVE from issues.json so dependent tickets unblock without needing
    a re-assemble during execution."""
    by_ticket = {}
    for ie in issues.values():
        tk = ie.get("ticket")
        if tk:
            by_ticket.setdefault(tk, []).append(ie.get("status"))
    done = {}
    for tid, t in (tickets or {}).items():
        if t.get("status") in DONE_STATES:
            done[tid] = True
            continue
        statuses = by_ticket.get(tid)
        done[tid] = bool(statuses) and all(s in DONE_STATES for s in statuses)
    return done


def ticket_reachable(parent, tickets, tdone):
    """Reachable if the parent exists, isn't deprecated, and all its depends_on
    tickets are done (per tdone). Unknown tickets.json / parent → True."""
    if tickets is None or parent is None:
        return True
    t = tickets.get(parent)
    if t is None:
        return True  # can't resolve; don't block
    if t.get("status") == "deprecated":
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
        if dep.get("status") not in DONE_STATES:
            return False
    return True


def pick_next(issues, tickets, tdone, skip):
    """Return id of next ready issue, or None."""
    ticket_graph = {}
    if tickets:
        ticket_graph = {tid: list(t.get("depends_on", []) or [])
                        for tid, t in tickets.items()}
    t_rank = topo_rank(ticket_graph) if ticket_graph else {}
    i_graph = {iid: [d for d in (ie.get("depends_on", []) or []) if d in issues]
               for iid, ie in issues.items()}
    i_rank = topo_rank(i_graph)

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
        eligible.append(iid)

    if not eligible:
        return None
    eligible.sort(key=lambda i: (
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

    if status in DONE_STATES:
        return {"claimed": False, "issue_id": iid,
                "reason": f"issue is {status}", "stale": False}, False
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
    args = p.parse_args()

    pm = Path(args.pm_dir)
    issues_path = Path(args.issues_path) if args.issues_path else pm / "issues.json"
    tickets_path = Path(args.tickets_path) if args.tickets_path else pm / "tickets.json"
    agent = args.agent or default_agent_id()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    if not issues_path.exists():
        print(f"ERROR: {issues_path} not found (run issues_assemble.py)", file=sys.stderr)
        sys.exit(2)

    tickets = load_tickets(tickets_path)

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
            target = pick_next(issues, tickets, tdone, skip)
            if target is None:
                report({"claimed": False, "issue_id": None,
                        "reason": "no ready issue to claim", "stale": False})

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
