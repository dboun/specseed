"""
issue_info.py <issue_id>

Prints everything an implementation agent needs for one issue, so it can load
only relevant context instead of ingesting all of issues.json / tickets.json /
reqs.json.

Issues are technical and don't carry satisfies_reqs themselves — requirements
live on the PARENT TICKET. This joins them: issue → parent ticket → reqs.

Output (JSON to stdout):
    {
      "issue_id": "FEAT-0101",
      "issue": { ...full issue entry from issues.json... },
      "ticket_id": "PROJ-0042" | null,
      "ticket": { ...full ticket entry... } | null,
      "reqs": {                       # parent ticket's satisfies_reqs, joined
        "SRS-API-012": { ...full req entry... },
        "SRS-API-999": { "_missing": true }
      }
    }

The markdown BODY (technical acceptance criteria, notes) is not included here —
read .specseed/project_management/issues/<id>/<id>.md directly for the prose.

Exit: 0 found; 1 issue id not found; 2 bad args / missing files / malformed JSON.
"""

import argparse
import json
import sys
from pathlib import Path


def load(path, required=True):
    if not path.exists():
        if required:
            print(f"ERROR: {path} not found", file=sys.stderr)
            sys.exit(2)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed {path}: {e}", file=sys.stderr)
        sys.exit(2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("issue_id")
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--issues-path", default=None)
    p.add_argument("--tickets-path", default=None)
    p.add_argument("--reqs-path", default=".specseed/spec/reqs.json")
    args = p.parse_args()

    pm = Path(args.pm_dir)
    issues_path = Path(args.issues_path) if args.issues_path else pm / "issues.json"
    tickets_path = Path(args.tickets_path) if args.tickets_path else pm / "tickets.json"
    reqs_path = Path(args.reqs_path)

    issues = load(issues_path, required=True)
    if args.issue_id not in issues:
        print(f"ERROR: issue {args.issue_id} not found in {issues_path}", file=sys.stderr)
        sys.exit(1)
    issue = issues[args.issue_id]

    tickets = load(tickets_path, required=False) or {}
    ticket_id = issue.get("ticket")
    ticket = tickets.get(ticket_id) if ticket_id else None

    # Join reqs from the PARENT TICKET's satisfies_reqs
    req_info = {}
    sat = (ticket or {}).get("satisfies_reqs", []) or []
    if sat:
        reqs = load(reqs_path, required=False)
        if reqs is not None:
            for rid in sat:
                req_info[rid] = reqs.get(rid, {"_missing": True})

    print(json.dumps({
        "issue_id": args.issue_id,
        "issue": issue,
        "ticket_id": ticket_id,
        "ticket": ticket,
        "reqs": req_info,
    }, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
