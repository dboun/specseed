"""
ticket_info.py <ticket_id>

Prints all information about a ticket for the implementation agent so it
can load only relevant context for its current ticket rather than
ingesting all of tickets.json + all of reqs.json.

Output (JSON to stdout):
    {
      "ticket_id": "FEAT-0001",
      "ticket": { ...full ticket entry... },
      "reqs": {
        "SRS-API-001": { ...full req entry... },
        "SRS-NONEXIST-99": { "_missing": true }
      }
    }

Exit codes:
  0 — ticket found, output written
  1 — ticket ID not found
  2 — bad args, missing files, malformed JSON

Optional flags:
  --tickets-path <p>  Override default `spec/tickets.json`
  --reqs-path <p>     Override default `spec/reqs.json`
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ticket_id")
    p.add_argument("--tickets-path", default="spec/tickets.json")
    p.add_argument("--reqs-path", default="spec/reqs.json")
    args = p.parse_args()

    tickets_path = Path(args.tickets_path)
    reqs_path = Path(args.reqs_path)

    if not tickets_path.exists():
        print(f"ERROR: {tickets_path} not found", file=sys.stderr)
        sys.exit(2)

    try:
        tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed {tickets_path}: {e}", file=sys.stderr)
        sys.exit(2)

    if args.ticket_id not in tickets:
        print(f"ERROR: ticket {args.ticket_id} not found in {tickets_path}",
              file=sys.stderr)
        sys.exit(1)

    ticket = tickets[args.ticket_id]

    # Join reqs (best-effort — missing reqs.json is allowed but warned)
    req_info = {}
    if reqs_path.exists():
        try:
            reqs = json.loads(reqs_path.read_text(encoding="utf-8"))
            for rid in ticket.get("satisfies_reqs", []):
                if rid in reqs:
                    req_info[rid] = reqs[rid]
                else:
                    req_info[rid] = {"_missing": True}
        except json.JSONDecodeError as e:
            print(f"WARNING: malformed {reqs_path}: {e}", file=sys.stderr)

    output = {
        "ticket_id": args.ticket_id,
        "ticket": ticket,
        "reqs": req_info,
    }
    print(json.dumps(output, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
