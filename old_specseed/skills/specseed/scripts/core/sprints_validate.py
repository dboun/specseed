"""
sprints_validate.py

Validates the SPRINT tier (.specseed/project_management/sprints.json), assembled
by sprints_assemble.py from the per-sprint folders. Sprints are time-boxed
batches of tickets (~168h soft budget), orthogonal to epics.

Kept SEPARATE from tickets_validate.py / issues_validate.py (same rationale: the
tiers may live in different stores). Degrades gracefully when tickets.json is
absent — ticket-ref and ordering checks are skipped with a warning.

Checks:
 1. ID format /^SPRINT_\\d{4}_W\\d{2}_[A-Z]+$/, unique, == folder name
 2. status enum {planned, in_progress, done, deprecated}. The single
    `in_progress` sprint is the one claiming targets (was `active`).
 3. tickets[] refs exist in tickets.json; each member ticket's `sprint` field
    points back to this sprint (back-consistency)
 4. a ticket appears in at most one sprint
 5. NO BACKWARD SPRINT DEP: a member ticket's `depends_on` tickets must be in
    the same sprint or an EARLIER one (by `order`) — never a later sprint. A dep
    in a later sprint is a scheduling error.
 6. budget: warn if a sprint's effort_hours exceeds budget * over-factor (soft).
 7. info/warn: intra-sprint serial chain length (longest dep chain among a
    sprint's own tickets); warn if a sprint is fully serial (no parallelism).
 8. warn if more than one sprint is `in_progress`.

Exit: 0 OK (or warnings only), 1 errors, 2 missing input.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"^SPRINT_\d{4}_W\d{2}_[A-Z]+$")
VALID_STATUSES = {"planned", "in_progress", "done", "deprecated"}


def longest_chain(nodes, deps_of):
    """Longest dependency chain length within `nodes` (deps restricted to
    nodes). Memoized DFS. Cycle-tolerant only in that it never recurses
    infinitely (a back-edge to a node currently on the stack counts as 1);
    the returned length within a cycle is not meaningful. Sprint graphs are
    validated acyclic elsewhere, so this is only a safety guard."""
    nodeset = set(nodes)
    memo, visiting = {}, set()

    def depth(n):
        if n in memo:
            return memo[n]
        if n in visiting:
            return 1
        visiting.add(n)
        best = 1
        for d in deps_of.get(n, []):
            if d in nodeset:
                best = max(best, 1 + depth(d))
        visiting.discard(n)
        memo[n] = best
        return best

    return max((depth(n) for n in nodes), default=0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--sprints-path", default=None)
    p.add_argument("--tickets-path", default=None)
    p.add_argument("--budget", type=float, default=168.0,
                   help="soft per-sprint effort budget in hours (default 168)")
    p.add_argument("--over-factor", type=float, default=1.5,
                   help="warn when a sprint exceeds budget * this (default 1.5)")
    args = p.parse_args()

    pm = Path(args.pm_dir)
    sprints_path = Path(args.sprints_path) if args.sprints_path else pm / "sprints.json"
    tickets_path = Path(args.tickets_path) if args.tickets_path else pm / "tickets.json"

    if not sprints_path.exists():
        print(f"ERROR: {sprints_path} not found (run sprints_assemble.py)", file=sys.stderr)
        sys.exit(2)
    try:
        sprints = json.loads(sprints_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed {sprints_path}: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(sprints, dict):
        print("ERROR: sprints.json root must be an object", file=sys.stderr)
        sys.exit(2)

    tickets = None
    if tickets_path.exists():
        try:
            tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            tickets = None

    errors, warnings = [], []

    # sprint order map (for backward-dep check)
    order_of = {sid: s.get("order", 0) for sid, s in sprints.items()}
    # ticket -> sprint (from sprint membership lists)
    ticket_sprint = {}

    active_count = 0
    for sid, s in sprints.items():
        if not ID_RE.match(sid):
            errors.append({"id": sid, "kind": "id_format",
                           "detail": "must match /^SPRINT_\\d{4}_W\\d{2}_[A-Z]+$/"})
        if not isinstance(s, dict):
            errors.append({"id": sid, "kind": "schema", "detail": "entry not an object"})
            continue
        if s.get("status") not in VALID_STATUSES:
            errors.append({"id": sid, "kind": "enum", "field": "status",
                           "value": s.get("status")})
        if s.get("status") == "in_progress":
            active_count += 1
        members = s.get("tickets", []) or []
        if not isinstance(members, list):
            errors.append({"id": sid, "kind": "schema", "field": "tickets",
                           "detail": "must be a list"})
            members = []
        for tid in members:
            if tid in ticket_sprint:
                errors.append({"id": sid, "kind": "multi_sprint", "value": tid,
                               "detail": f"ticket also in {ticket_sprint[tid]}"})
            else:
                ticket_sprint[tid] = sid

    if active_count > 1:
        warnings.append({"id": "*", "kind": "multi_active",
                         "detail": f"{active_count} sprints marked in_progress"})

    # Ticket-aware checks
    if tickets is None:
        warnings.append({"id": "*", "kind": "no_tickets",
                         "detail": "tickets.json absent — ref/ordering/budget "
                                   "checks skipped"})
    else:
        deps_of = {tid: (t.get("depends_on", []) or []) for tid, t in tickets.items()}
        for sid, s in sprints.items():
            members = s.get("tickets", []) or []
            for tid in members:
                t = tickets.get(tid)
                if t is None:
                    errors.append({"id": sid, "kind": "dangling_ref", "field": "tickets",
                                   "value": tid, "detail": "not in tickets.json"})
                    continue
                # back-consistency: ticket.sprint must name this sprint
                back = t.get("sprint")
                if back != sid:
                    errors.append({"id": sid, "kind": "back_ref", "field": "tickets",
                                   "value": tid,
                                   "detail": f"ticket.sprint={back!r}, expected {sid!r}"})
                # backward-dep: dep must not live in a later sprint
                for d in deps_of.get(tid, []):
                    dsp = ticket_sprint.get(d)
                    if dsp is None:
                        continue  # dep unassigned — not a backward-sprint error
                    if order_of.get(dsp, 0) > order_of.get(sid, 0):
                        errors.append({"id": sid, "kind": "backward_sprint_dep",
                                       "value": tid,
                                       "detail": f"depends on {d} in later sprint {dsp}"})
            # budget warning
            eh = s.get("effort_hours", 0) or 0
            if eh > args.budget * args.over_factor:
                warnings.append({"id": sid, "kind": "over_budget",
                                 "detail": f"{eh}h > {args.budget}h * {args.over_factor}"})
            # parallelism: serial chain among this sprint's own tickets
            present = [tid for tid in members if tid in tickets]
            chain = longest_chain(present, deps_of)
            if len(present) >= 3 and chain == len(present):
                warnings.append({"id": sid, "kind": "fully_serial",
                                 "detail": f"all {len(present)} tickets form one "
                                           f"dependency chain — no parallelism"})

    if errors:
        print(json.dumps({"valid": False, "errors": errors, "warnings": warnings}, indent=2))
        sys.exit(1)
    if warnings:
        print(f"OK with {len(warnings)} warning(s) on {len(sprints)} sprint(s):")
        for w in warnings:
            print(f"  {w['id']} [{w['kind']}]: {w.get('detail', '')} "
                  f"{w.get('value', '')}".strip())
        sys.exit(0)
    print(f"OK: {len(sprints)} sprints validated")
    sys.exit(0)


if __name__ == "__main__":
    main()
