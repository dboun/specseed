"""
verification_map.py

Produces the inverse map req → ticket(s) → test file(s) on demand from
.specseed/spec/reqs.json + .specseed/spec/tickets.json. Replaces the dropped
SRS `Verified by` column.

Usage:
    python .specseed/scripts/verification_map.py
    python .specseed/scripts/verification_map.py --check
    python .specseed/scripts/verification_map.py --req SRS-API-001
    python .specseed/scripts/verification_map.py --format markdown

See module-spec docstring in skill notes for full contract.
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true",
                   help="exit 1 if any req has no test coverage")
    p.add_argument("--req", default=None, help="show map for a single req only")
    p.add_argument("--format", choices=["json", "markdown"], default="json")
    p.add_argument("--reqs-path", default=".specseed/spec/reqs.json")
    p.add_argument("--tickets-path", default=".specseed/spec/tickets.json")
    p.add_argument("--include-deprecated", action="store_true",
                   help="include deprecated tickets in the map (off by default)")
    args = p.parse_args()

    reqs_path = Path(args.reqs_path)
    tickets_path = Path(args.tickets_path)

    if not reqs_path.exists():
        print(f"ERROR: {reqs_path} not found", file=sys.stderr)
        sys.exit(2)
    if not tickets_path.exists():
        print(f"ERROR: {tickets_path} not found", file=sys.stderr)
        sys.exit(2)

    try:
        reqs = json.loads(reqs_path.read_text(encoding="utf-8"))
        tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed JSON: {e}", file=sys.stderr)
        sys.exit(2)

    # Build req → {tickets, tests} map. Initialize all reqs so output is exhaustive.
    req_map = {rid: {"tickets": [], "tests": set()} for rid in reqs}

    for tid, t in tickets.items():
        if not args.include_deprecated and t.get("status") == "deprecated":
            continue
        for rid in t.get("satisfies_reqs", []) or []:
            if rid in req_map:
                if tid not in req_map[rid]["tickets"]:
                    req_map[rid]["tickets"].append(tid)
                for test in t.get("artifacts", {}).get("tests", []) or []:
                    req_map[rid]["tests"].add(test)

    # Finalize — sort for stable output
    for rid in req_map:
        req_map[rid]["tickets"].sort()
        req_map[rid]["tests"] = sorted(req_map[rid]["tests"])

    # Filter by --req
    if args.req:
        if args.req not in req_map:
            print(f"ERROR: req {args.req} not found in {reqs_path}", file=sys.stderr)
            sys.exit(2)
        req_map = {args.req: req_map[args.req]}

    if args.check:
        uncovered = []
        for rid, info in req_map.items():
            if not info["tests"]:
                if not info["tickets"]:
                    uncovered.append((rid, "no satisfying ticket"))
                else:
                    tlist = ", ".join(info["tickets"])
                    uncovered.append((rid, f"satisfied by {tlist} but no artifacts.tests"))
        if uncovered:
            print("Reqs with no test coverage:", file=sys.stderr)
            for rid, reason in uncovered:
                print(f"  {rid}  ({reason})", file=sys.stderr)
            print(f"\n{len(uncovered)} reqs uncovered out of {len(req_map)} total.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"OK: all {len(req_map)} req(s) have test coverage")
        sys.exit(0)

    if args.format == "json":
        print(json.dumps(req_map, indent=2))
    else:  # markdown
        print("| Req | Tickets | Tests |")
        print("|-----|---------|-------|")
        for rid in sorted(req_map.keys()):
            info = req_map[rid]
            t_str = ", ".join(info["tickets"]) if info["tickets"] else "—"
            tests_str = ", ".join(info["tests"]) if info["tests"] else "—"
            print(f"| {rid} | {t_str} | {tests_str} |")

    sys.exit(0)


if __name__ == "__main__":
    main()
