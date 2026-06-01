"""
sprint_plan.py  [--budget 168] [--respect-existing]

ADVISORY sprint-assignment proposer. Reads tickets.json, proposes a packing of
tickets into time-boxed sprints (~budget hours each). It does NOT write anything
— it emits a proposal to stdout. A human (or the skill) reviews, adjusts, and
writes the real `sprint:` assignments into the ticket/sprint folders.

This mirrors the analysis seam of tickets_analyze.py: shipped + editable, never
mutating source-of-truth folders.

NOT pure greedy. Packing uses Kahn's algorithm with a priority frontier, so the
order respects three things at once:
  1. dependencies (a ticket is only placed after all its deps are placed) — this
     guarantees no backward sprint deps by construction;
  2. the critical path is pulled early (CP tickets gate the schedule);
  3. cohesion — among ready tickets, prefer one in the same epic as the sprint
     currently being filled, so sprints don't become grab-bags.
The leftover judgement (business dates, deliberate splits, "keep X+Y together")
is the human's: refine the proposal, optionally guided by reusable notes in
.specseed/memory/sprint_planning.md, then assign.

Critical path is taken from tickets_analyze.analyze() if importable; otherwise CP
weighting is skipped (deps + cohesion still apply).

Exit: 0 OK, 1 cycle/structural error, 2 missing input.
"""

import argparse
import json
import sys
from pathlib import Path

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def critical_path_set(tickets):
    """Return set of ticket ids on the critical path, or empty set if the
    analyzer isn't importable / fails. Abandoned tickets (deprecated / wont_do)
    are excluded so the critical path matches the tickets actually scheduled."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import tickets_analyze
        active = {tid: t for tid, t in tickets.items()
                  if t.get("status", "todo") not in ("deprecated", "wont_do")}
        slim = {tid: {"effort_hours": t.get("effort_hours", 0) or 0,
                      "depends_on": [d for d in (t.get("depends_on", []) or [])
                                     if d in active],
                      "status": t.get("status", "todo")}
                for tid, t in active.items()}
        return set(tickets_analyze.analyze(slim).get("critical_path", []))
    except Exception:
        return set()


def plan(tickets, budget):
    """Cohesion-aware Kahn packing. Returns (sprints, diagnostics) or raises
    ValueError on cycle."""
    cp = critical_path_set(tickets)

    # Build graph restricted to known tickets; skip abandoned tickets entirely
    # (wont_do / deprecated — they will never be executed).
    active = {tid: t for tid, t in tickets.items()
              if t.get("status") not in ("deprecated", "wont_do")}
    deps_of = {tid: [d for d in (t.get("depends_on", []) or []) if d in active]
               for tid, t in active.items()}
    indeg = {tid: 0 for tid in active}
    succ = {tid: [] for tid in active}
    for tid, ds in deps_of.items():
        for d in ds:
            succ[d].append(tid)
            indeg[tid] += 1

    def eff(tid):
        e = active[tid].get("effort_hours", 0) or 0
        return e if isinstance(e, (int, float)) and not isinstance(e, bool) else 0

    ready = [tid for tid in active if indeg[tid] == 0]
    placed = 0
    sprints = []
    cur, cur_eff, cur_epics = [], 0.0, set()

    def best(ready_list, epics):
        # lower key sorts first
        def key(tid):
            t = active[tid]
            return (
                0 if tid in cp else 1,                       # CP first
                0 if t.get("epic") in epics else 1,          # cohesion
                PRIORITY_RANK.get(str(t.get("priority", "medium")).lower(), 1),
                tid,
            )
        return min(ready_list, key=key)

    while ready:
        tid = best(ready, cur_epics)
        ready.remove(tid)
        e = eff(tid)
        # close current sprint if adding would overflow (but never leave a sprint
        # empty; a single oversized ticket gets its own sprint)
        if cur and cur_eff + e > budget:
            sprints.append((cur, cur_eff, cur_epics))
            cur, cur_eff, cur_epics = [], 0.0, set()
        cur.append(tid)
        cur_eff += e
        ep = active[tid].get("epic")
        if ep:
            cur_epics.add(ep)
        placed += 1
        if cur_eff >= budget:
            sprints.append((cur, cur_eff, cur_epics))
            cur, cur_eff, cur_epics = [], 0.0, set()
        # release successors
        for s in succ[tid]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
    if cur:
        sprints.append((cur, cur_eff, cur_epics))

    if placed != len(active):
        raise ValueError("cycle detected in ticket depends_on — cannot plan")

    return sprints, cp


def serial_chain_len(ids, deps_of):
    idset = set(ids)
    memo, visiting = {}, set()

    def depth(n):
        if n in memo:
            return memo[n]
        if n in visiting:
            return 1
        visiting.add(n)
        best = 1
        for d in deps_of.get(n, []):
            if d in idset:
                best = max(best, 1 + depth(d))
        visiting.discard(n)
        memo[n] = best
        return best

    return max((depth(n) for n in ids), default=0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--tickets-path", default=None)
    p.add_argument("--budget", type=float, default=168.0,
                   help="soft per-sprint effort budget in hours (default 168)")
    args = p.parse_args()

    pm = Path(args.pm_dir)
    tickets_path = Path(args.tickets_path) if args.tickets_path else pm / "tickets.json"
    if not tickets_path.exists():
        print(f"ERROR: {tickets_path} not found (run tickets_assemble.py)", file=sys.stderr)
        sys.exit(2)
    try:
        tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed {tickets_path}: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        sprints, cp = plan(tickets, args.budget)
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    deps_of = {tid: (t.get("depends_on", []) or []) for tid, t in tickets.items()}
    unsized = [tid for tid, t in tickets.items()
               if t.get("status") not in ("deprecated", "wont_do")
               and not (t.get("effort_hours", 0) or 0)]

    proposed = []
    for i, (ids, eff, epics) in enumerate(sprints, start=1):
        proposed.append({
            "seq": i,
            "effort_hours": eff,
            "over_budget": eff > args.budget,
            "epics": sorted(e for e in epics if e),
            "serial_chain_len": serial_chain_len(ids, deps_of),
            "tickets": [{
                "id": tid,
                "title": tickets[tid].get("title", ""),
                "effort_hours": tickets[tid].get("effort_hours", 0) or 0,
                "epic": tickets[tid].get("epic"),
                "priority": tickets[tid].get("priority"),
                "on_critical_path": tid in cp,
            } for tid in ids],
        })

    out = {
        "budget_hours": args.budget,
        "proposed_sprints": proposed,
        "diagnostics": {
            "n_sprints": len(proposed),
            "n_tickets": sum(len(s["tickets"]) for s in proposed),
            "critical_path": sorted(cp),
            "unsized_tickets": unsized,
            "note": "ADVISORY. Assign real SPRINT_<YYYY>_W<WW>_<X> ids and review "
                    "cohesion/dates before writing `sprint:` into folders.",
        },
    }
    print(json.dumps(out, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
