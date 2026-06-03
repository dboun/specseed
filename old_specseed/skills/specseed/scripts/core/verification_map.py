"""
verification_map.py

Produces the inverse map req → ticket(s) → issue(s) → test file(s) on demand.
Replaces the dropped SRS `Verified by` column.

Traceability now spans three tiers:
  req  --(ticket.satisfies_reqs)-->  ticket  --(ticket.issues)-->  issue
                                                   --(issue.artifacts.tests)-->  tests

Reads .specseed/spec/reqs.json + .specseed/project_management/tickets.json +
.specseed/project_management/issues.json.

Usage:
    python .specseed/scripts/core/verification_map.py
    python .specseed/scripts/core/verification_map.py --check
    python .specseed/scripts/core/verification_map.py --req SRS-API-001
    python .specseed/scripts/core/verification_map.py --format markdown
"""

import argparse
import json
import sys
from pathlib import Path


def load(path):
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed {path}: {e}", file=sys.stderr)
        sys.exit(2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true",
                   help="exit 1 if any req has no test coverage")
    p.add_argument("--req", default=None)
    p.add_argument("--format", choices=["json", "markdown"], default="json")
    p.add_argument("--reqs-path", default=".specseed/spec/reqs.json")
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--tickets-path", default=None)
    p.add_argument("--issues-path", default=None)
    p.add_argument("--include-deprecated", action="store_true",
                   help="also include abandoned (deprecated/wont_do) entries")
    args = p.parse_args()

    pm = Path(args.pm_dir)
    reqs = load(Path(args.reqs_path))
    tickets = load(Path(args.tickets_path) if args.tickets_path else pm / "tickets.json")
    issues = load(Path(args.issues_path) if args.issues_path else pm / "issues.json")

    def live(entry):
        return args.include_deprecated or entry.get("status") not in ("deprecated", "wont_do")

    # issue id -> tests, scoped to live issues
    issue_tests = {iid: list((ie.get("artifacts") or {}).get("tests", []) or [])
                   for iid, ie in issues.items() if live(ie)}

    req_map = {rid: {"tickets": [], "issues": set(), "tests": set()} for rid in reqs}

    for tid, t in tickets.items():
        if not live(t):
            continue
        # issues belonging to this ticket: prefer explicit list, else reverse-scan
        child = list(t.get("issues", []) or [])
        if not child:
            child = [iid for iid, ie in issues.items() if ie.get("ticket") == tid]
        for rid in t.get("satisfies_reqs", []) or []:
            if rid not in req_map:
                continue
            if tid not in req_map[rid]["tickets"]:
                req_map[rid]["tickets"].append(tid)
            for iid in child:
                if iid in issue_tests:
                    req_map[rid]["issues"].add(iid)
                    req_map[rid]["tests"].update(issue_tests[iid])

    for rid in req_map:
        req_map[rid]["tickets"].sort()
        req_map[rid]["issues"] = sorted(req_map[rid]["issues"])
        req_map[rid]["tests"] = sorted(req_map[rid]["tests"])

    if args.req:
        if args.req not in req_map:
            print(f"ERROR: req {args.req} not found", file=sys.stderr)
            sys.exit(2)
        req_map = {args.req: req_map[args.req]}

    if args.check:
        uncovered = []
        for rid, info in req_map.items():
            if info["tests"]:
                continue
            if not info["tickets"]:
                uncovered.append((rid, "no satisfying ticket"))
            elif not info["issues"]:
                uncovered.append((rid, f"satisfied by {', '.join(info['tickets'])} "
                                       f"but no issues"))
            else:
                uncovered.append((rid, f"issues {', '.join(info['issues'])} have no "
                                       f"artifacts.tests"))
        if uncovered:
            print("Reqs with no test coverage:", file=sys.stderr)
            for rid, reason in uncovered:
                print(f"  {rid}  ({reason})", file=sys.stderr)
            print(f"\n{len(uncovered)} of {len(req_map)} reqs uncovered.", file=sys.stderr)
            sys.exit(1)
        print(f"OK: all {len(req_map)} req(s) have test coverage")
        sys.exit(0)

    if args.format == "json":
        print(json.dumps(req_map, indent=2))
    else:
        print("| Req | Tickets | Issues | Tests |")
        print("|-----|---------|--------|-------|")
        for rid in sorted(req_map):
            info = req_map[rid]
            tk = ", ".join(info["tickets"]) or "—"
            iss = ", ".join(info["issues"]) or "—"
            ts = ", ".join(info["tests"]) or "—"
            print(f"| {rid} | {tk} | {iss} | {ts} |")
    sys.exit(0)


if __name__ == "__main__":
    main()
