"""
add_work.py — add a MANUAL work item (a bug, a chore, an urgent fix) to the
.specseed/ work layer, out of band from the normal spec-driven breakdown.

A manual item is scaffolded as a TICKET (PROJ-NNNN) + one issue under it — a ticket
(not a bare issue) so it gets roadmap visibility, sprint membership, and priority,
with the issue as its execution unit. Both get a `created_at` stamp (the FIFO claim
tiebreak).

Routing by priority (NO sprint replan is ever triggered):
  - high          → assigned to the current in_progress sprint, so it's claimable now
                    and jumps to the top of the pile via the priority sort key.
  - medium / low  → left in backlog (sprint: null); picked up at the next sprint planning.

After scaffolding, re-runs the assemble chain + ROADMAP/TIMELINE renders so the new
item shows up immediately. Source of truth stays the folders.

Usage (flags optional — you're prompted for any required field you omit):
  python .specseed/scripts/add_work.py \
      --title "Login throws 500 on empty password" \
      --type bug --priority high \
      --component api --effort 0.5 \
      --req SRS-API-012 --desc "Crashes instead of 400."

Stdlib only. Human-run (not part of the agent loop).
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))
import config as cfgmod  # noqa: E402

TYPE_PREFIX = {"feature": "FEAT", "bug": "BUG", "chore": "CHORE", "spike": "SPIKE"}
PRIORITIES = ("high", "medium", "low")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prompt(label, default=None, choices=None):
    suffix = f" [{default}]" if default is not None else ""
    if choices:
        suffix = f" ({'/'.join(choices)})" + suffix
    while True:
        val = input(f"{label}{suffix}: ").strip()
        if not val and default is not None:
            return default
        if not val:
            print("  (required)")
            continue
        if choices and val not in choices:
            print(f"  (choose one of: {', '.join(choices)})")
            continue
        return val


def next_id(folder, prefix):
    """Max existing <prefix>-NNNN under `folder`, +1, zero-padded to 4."""
    n = 0
    if folder.exists():
        for child in folder.iterdir():
            name = child.name
            if child.is_dir() and name.startswith(prefix + "-"):
                tail = name[len(prefix) + 1:]
                if tail.isdigit():
                    n = max(n, int(tail))
    return f"{prefix}-{n + 1:04d}"


def parse_fm_value(line):
    """value after the first colon, JSON-parsed (falls back to bare string)."""
    v = line.split(":", 1)[1].strip()
    try:
        return json.loads(v)
    except Exception:
        return v.strip().strip('"').strip("'")


def in_progress_sprint(pm):
    """(sprint_id, sprint_main_file) of the in_progress sprint, or (None, None)."""
    sprints_json = pm / "sprints.json"
    if not sprints_json.exists():
        return None, None
    try:
        data = json.loads(sprints_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, None
    for sid, s in data.items():
        if s.get("status") == "in_progress":
            mf = pm / "sprints" / sid / f"{sid}.md"
            return sid, (mf if mf.exists() else None)
    return None, None


def append_ticket_to_sprint(sprint_file, ticket_id):
    """Add ticket_id to the sprint folder's `tickets:` frontmatter list (source of
    truth; sprints_validate enforces back-consistency with ticket.sprint)."""
    lines = sprint_file.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("tickets:"):
            cur = parse_fm_value(line)
            if not isinstance(cur, list):
                cur = []
            if ticket_id not in cur:
                cur.append(ticket_id)
            lines[i] = "tickets: " + json.dumps(cur)
            sprint_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def main():
    p = argparse.ArgumentParser(description="add a manual work item (ticket + issue)")
    p.add_argument("--title")
    p.add_argument("--type", choices=list(TYPE_PREFIX))
    p.add_argument("--priority", choices=list(PRIORITIES))
    p.add_argument("--component", default=None)
    p.add_argument("--effort", type=float, default=None)
    p.add_argument("--req", action="append", default=[],
                   help="a requirement id this satisfies (repeatable)")
    p.add_argument("--desc", default=None)
    p.add_argument("--no-render", action="store_true",
                   help="skip the assemble + render chain (just write the folders)")
    args = p.parse_args()

    try:
        root = cfgmod.find_root()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    pm = root / ".specseed" / "project_management"
    if not pm.exists():
        print(f"ERROR: {pm} not found — is this a specseed repo with work?", file=sys.stderr)
        return 2

    title = args.title or prompt("Title")
    itype = args.type or prompt("Type", default="bug", choices=list(TYPE_PREFIX))
    priority = args.priority or prompt("Priority", default="high", choices=list(PRIORITIES))
    component = args.component or prompt("Component", default="general")
    effort = args.effort if args.effort is not None else float(prompt("Effort hours", default="1.0"))
    desc = args.desc if args.desc is not None else prompt("One-line description", default=title)
    reqs = args.req

    ts = now_iso()
    ticket_id = next_id(pm / "tickets", "PROJ")
    issue_id = next_id(pm / "issues", TYPE_PREFIX[itype])

    # decide sprint placement
    sprint_id, sprint_file = (None, None)
    if priority == "high":
        sid, sfile = in_progress_sprint(pm)
        if sid:
            sprint_id, sprint_file = sid, sfile

    # --- write the ticket folder ---
    tdir = pm / "tickets" / ticket_id
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"{ticket_id}.md").write_text(
        "---\n"
        f"id: {ticket_id}\n"
        f"title: {title}\n"
        "epic: null\n"
        f"type: {itype}\n"
        f"priority: {priority}\n"
        "status: todo\n"
        "approval_required: false\n"
        "depends_on: []\n"
        f"satisfies_reqs: {json.dumps(reqs)}\n"
        f"issues: {json.dumps([issue_id])}\n"
        f"sprint: {json.dumps(sprint_id)}\n"
        f"created_at: {ts}\n"
        "---\n"
        "## Story\n"
        f"{desc}\n\n"
        "## Description\n"
        f"{desc}\n\n"
        "## Acceptance criteria\n"
        "- <fill in what proves this is done>\n",
        encoding="utf-8")

    # --- write the issue folder ---
    idir = pm / "issues" / issue_id
    idir.mkdir(parents=True, exist_ok=True)
    (idir / f"{issue_id}.md").write_text(
        "---\n"
        f"id: {issue_id}\n"
        f"title: {title}\n"
        f"ticket: {ticket_id}\n"
        f"type: {itype}\n"
        f"component: {component}\n"
        f"effort_hours: {effort}\n"
        "difficulty: hard\n"
        "depends_on: []\n"
        "status: todo\n"
        "review_required: false\n"
        "approval_required: false\n"
        "claimed_at: null\n"
        "claimed_by: null\n"
        f"created_at: {ts}\n"
        'artifacts: {"touches": [], "tests": [], "migrations": []}\n'
        "---\n"
        "## Acceptance criteria\n"
        "- <fill in the technical pass/fail for this issue>\n\n"
        "## Notes\n"
        f"Manual item added via add_work.py on {ts}.\n",
        encoding="utf-8")

    # keep sprint folder's tickets[] in sync (back-consistency)
    placed = "backlog"
    if sprint_id and sprint_file:
        if append_ticket_to_sprint(sprint_file, ticket_id):
            placed = f"sprint {sprint_id} (in_progress)"
        else:
            placed = f"backlog (could not update sprint {sprint_id} folder — add {ticket_id} to its tickets: by hand)"
    elif priority == "high":
        placed = "backlog (no in_progress sprint — will be picked at next sprint planning)"

    print(f"Created ticket {ticket_id} + issue {issue_id} (priority {priority}) → {placed}")

    if args.no_render:
        return 0

    core = root / ".specseed" / "scripts" / "core"
    chain = [["issues_assemble.py"], ["tickets_assemble.py"]]
    if (pm / "sprints").exists():
        chain.append(["sprints_assemble.py"])
    chain.append(["roadmap_render.py"])
    if (pm / "sprints.json").exists():
        chain.append(["timeline_render.py"])
    for cmd in chain:
        script = core / cmd[0]
        if not script.exists():
            continue
        r = subprocess.run([sys.executable, str(script), *cmd[1:]],
                           cwd=str(root))
        if r.returncode not in (0,):
            print(f"WARNING: {cmd[0]} exited {r.returncode}", file=sys.stderr)
    print("Done. Run claim_issue.py to pick it up "
          "(high-priority items in the active sprint sort to the top).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
