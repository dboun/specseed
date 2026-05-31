"""
claim_ticket.py <ticket_id>

Atomic ticket claim. Replaces the raw `jq` claim one-liner so multiple
concurrent agents can't claim the same ticket and proceed.

Uses `fcntl.flock` (advisory POSIX lock) on `.specseed/spec/tickets.json` for the
read-verify-write critical section. Lock auto-releases on process exit
(killed/stuck agents can't block claims forever). Stale claims (older than
--stale-hours) are taken over.

Output: JSON to stdout. Exit 0 for normal outcomes (claimed or refused);
2 for lock-timeout, IO, or schema errors.

See module-spec docstring in the skill notes for full contract.
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


def default_agent_id():
    env = os.environ.get("CLAUDE_AGENT_ID")
    if env:
        return env
    return f"{socket.gethostname()}-{os.getpid()}"


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
    """Open file r+ and acquire exclusive flock with timeout.

    Returns the open file handle (with lock held) or None on timeout.
    """
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ticket_id")
    p.add_argument("--agent", default=None,
                   help="agent identifier (default: env CLAUDE_AGENT_ID or hostname-pid)")
    p.add_argument("--stale-hours", type=float, default=3.0,
                   help="claims older than this are stale (default 3)")
    p.add_argument("--no-stale-takeover", action="store_true",
                   help="do not take over stale claims; report conflict instead")
    p.add_argument("--lock-timeout", type=float, default=10.0,
                   help="max seconds to wait for file lock (default 10)")
    p.add_argument("--tickets-path", default=".specseed/spec/tickets.json")
    args = p.parse_args()

    agent = args.agent or default_agent_id()
    tickets_path = Path(args.tickets_path)

    if not tickets_path.exists():
        print(f"ERROR: {tickets_path} not found", file=sys.stderr)
        sys.exit(2)

    f = acquire_lock(tickets_path, args.lock_timeout)
    if f is None:
        # Lock timeout — surface as exit-2 error (NOT a normal claim-false outcome)
        print(json.dumps({
            "claimed": False,
            "ticket_id": args.ticket_id,
            "reason": "lock-timeout",
            "detail": f"could not acquire lock within {args.lock_timeout}s",
        }), file=sys.stderr)
        sys.exit(2)

    try:
        f.seek(0)
        try:
            tickets = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: malformed tickets.json: {e}", file=sys.stderr)
            sys.exit(2)

        tid = args.ticket_id
        if tid not in tickets:
            report({
                "claimed": False, "ticket_id": tid,
                "reason": "ticket not found in tickets.json",
                "stale": False,
            })

        t = tickets[tid]
        status = t.get("status", "todo")

        # Reject done/deprecated outright
        if status in ("done", "deprecated"):
            report({
                "claimed": False, "ticket_id": tid,
                "reason": f"ticket is {status}",
                "stale": False,
            })

        # Verify deps met
        for d in t.get("depends_on", []) or []:
            if d not in tickets:
                report({
                    "claimed": False, "ticket_id": tid,
                    "reason": f"depends_on references unknown ticket {d}",
                    "stale": False,
                })
            dep_status = tickets[d].get("status", "todo")
            if dep_status not in ("done", "deprecated"):
                report({
                    "claimed": False, "ticket_id": tid,
                    "reason": f"dependency {d} not done (status={dep_status})",
                    "stale": False,
                })

        previous_claim = None

        if status == "in_progress":
            ca = t.get("claimed_at")
            cb = t.get("claimed_by")
            ts = parse_iso(ca) if ca else None
            if ts is None:
                # in_progress without a parseable timestamp — treat as stale
                stale, age_h = True, None
            else:
                now = datetime.now(timezone.utc)
                age_h = (now - ts).total_seconds() / 3600.0
                stale = age_h >= args.stale_hours

            if stale and not args.no_stale_takeover:
                previous_claim = {"by": cb, "at": ca, "stale_hours": age_h}
            else:
                report({
                    "claimed": False, "ticket_id": tid,
                    "reason": "already claimed",
                    "current_claim": {"by": cb, "at": ca},
                    "stale": stale,
                })

        # Claim it
        new_ts = now_iso()
        t["status"] = "in_progress"
        t["claimed_at"] = new_ts
        t["claimed_by"] = agent

        # Write in-place while holding the lock
        new_content = json.dumps(tickets, indent=2) + "\n"
        f.seek(0)
        f.truncate()
        f.write(new_content)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

        report({
            "claimed": True,
            "ticket_id": tid,
            "claimed_at": new_ts,
            "claimed_by": agent,
            "previous_claim": previous_claim,
        })

    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


if __name__ == "__main__":
    main()
