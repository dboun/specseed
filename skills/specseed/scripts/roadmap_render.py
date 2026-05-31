"""
roadmap_render.py

Refreshes the "(X/Y complete)" issue-count annotations on ticket-title lines in
.specseed/project_management/ROADMAP.md from the assembled tickets.json
(per-ticket issues_done / issues_total).

In-place + structure-preserving: it only rewrites the count suffix on lines that
mention exactly one ticket id; phases, epics, prose, and ordering are untouched.
The counts are display-only — nothing depends on them (claim_issue.py derives
unblocking from issues.json), so this is pure convenience. Run it after
tickets_assemble.py (finish flow / adapt cascade).

On a ticket line: an existing trailing "(\\d+/\\d+ complete)" is replaced; if
absent, " (X/Y complete)" is appended.

Usage:
    python .specseed/scripts/roadmap_render.py
    python .specseed/scripts/roadmap_render.py --check   # exit 1 if drift, don't write

Exit: 0 OK (or --check with no drift), 1 (--check found drift), 2 missing input.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ANNOT_RE = re.compile(r"\s*\(\d+/\d+ complete\)\s*$")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--roadmap-path", default=None)
    p.add_argument("--tickets-path", default=None)
    p.add_argument("--check", action="store_true",
                   help="report drift and exit 1 without writing")
    args = p.parse_args()

    pm = Path(args.pm_dir)
    roadmap_path = Path(args.roadmap_path) if args.roadmap_path else pm / "ROADMAP.md"
    tickets_path = Path(args.tickets_path) if args.tickets_path else pm / "tickets.json"

    if not roadmap_path.exists():
        print(f"ERROR: {roadmap_path} not found", file=sys.stderr)
        sys.exit(2)
    if not tickets_path.exists():
        print(f"ERROR: {tickets_path} not found (run tickets_assemble.py)", file=sys.stderr)
        sys.exit(2)
    try:
        tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed {tickets_path}: {e}", file=sys.stderr)
        sys.exit(2)

    ids = set(tickets.keys())
    id_re = re.compile(r"\b(" + "|".join(re.escape(i) for i in ids) + r")\b") if ids else None

    lines = roadmap_path.read_text(encoding="utf-8").splitlines(keepends=True)
    out, changed = [], 0

    for line in lines:
        if id_re is None:
            out.append(line)
            continue
        found = id_re.findall(line)
        if len(set(found)) != 1:   # skip lines with 0 or >1 ticket ids (e.g. epic lines)
            out.append(line)
            continue
        tid = found[0]
        t = tickets[tid]
        done, total = t.get("issues_done", 0), t.get("issues_total", 0)
        annot = f"({done}/{total} complete)"

        newline = line.rstrip("\n")
        eol = line[len(newline):]
        if ANNOT_RE.search(newline):
            newline = ANNOT_RE.sub("", newline)
        newline = f"{newline.rstrip()} {annot}"
        rebuilt = newline + (eol if eol else "\n")
        if rebuilt != line:
            changed += 1
        out.append(rebuilt)

    if args.check:
        if changed:
            print(f"DRIFT: {changed} ticket line(s) have stale counts", file=sys.stderr)
            sys.exit(1)
        print("OK: ROADMAP counts up to date")
        sys.exit(0)

    if changed:
        roadmap_path.write_text("".join(out), encoding="utf-8")
    print(f"OK: refreshed {changed} ticket line(s) in {roadmap_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
