"""
tickets_assemble.py

Assembles .specseed/project_management/tickets.json from the per-ticket folders
under .specseed/project_management/tickets/<TICKET-ID>/<TICKET-ID>.md.

Source of truth is the folders (frontmatter + prose). This script extracts the
YAML-ish frontmatter and writes the machine index that tickets_analyze.py /
tickets_validate.py / verification_map.py consume. The markdown body (story,
description, product-level acceptance criteria) is NOT copied into the JSON.

Derived fields (computed here, not authored):
- effort_hours: sum of child issues' effort_hours (critical path runs on this).
- issues_total / issues_done: completion counts (feed the ROADMAP "(X/Y)" tags).
  issues_done counts only real `done`; issues_total EXCLUDES wont_do + deprecated
  children (they drop out of the denominator — the bar reflects only live work).
- status: auto-resolves when a ticket has children and ALL are resolved
  (done/wont_do/deprecated): → `done` if ≥1 child is `done`, else `deprecated`
  if every child is `deprecated`, else `wont_do`. If the ticket carries
  `approval_required: true`, the `done` case stops at `awaiting_approval`
  instead (a human signs off → sets `done`). A status already terminal or
  `awaiting_approval` is never overridden (manual/forced wins). Live status is
  otherwise preserved from an existing tickets.json when the folder is still at
  seed `todo` (see issues_assemble.py for the same source-of-truth split).
  --no-preserve opts out.

Run issues_assemble.py FIRST — the derived fields read issues.json. If
issues.json is absent, effort_hours falls back to 0 and counts to 0 (with a
warning); critical-path analysis will be degenerate until issues are assembled.

Frontmatter format: flat `key: value`, structured values as inline JSON. See
issues_assemble.py docstring.

Exit: 0 OK, 1 structural error (dup/missing id, unparseable), 2 missing input.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

RESOLVED = {"done", "wont_do", "deprecated"}     # terminal — all children here ⇒ ticket resolves
DROP_FROM_COUNT = {"wont_do", "deprecated"}        # excluded from the (X/Y) denominator


def coerce(v):
    v = v.strip()
    if v == "":
        return ""
    try:
        return json.loads(v)
    except Exception:
        return v.strip().strip('"').strip("'")


def parse_md(text):
    if not text.startswith("---"):
        return {}, text
    nl = text.find("\n")
    if nl == -1:
        return {}, text
    rest = text[nl + 1:]
    end = rest.find("\n---")
    if end == -1:
        return {}, text
    fm_block = rest[:end]
    body = rest[end + 4:]
    if body.startswith("\n"):
        body = body[1:]
    meta = {}
    for line in fm_block.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = coerce(v.strip())
    return meta, body


def find_main_file(folder):
    named = folder / f"{folder.name}.md"
    if named.exists():
        return named
    mds = [p for p in folder.glob("*.md")]
    if len(mds) == 1:
        return mds[0]
    return None


def load_issues(issues_path):
    """Return issues dict, or None if not present/parseable (with warning)."""
    if not issues_path.exists():
        print(f"WARNING: {issues_path} not found — effort_hours and completion "
              f"counts will be 0. Run issues_assemble.py first.", file=sys.stderr)
        return None
    try:
        return json.loads(issues_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"WARNING: malformed {issues_path}: {e} — counts will be 0",
              file=sys.stderr)
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--issues-path", default=None,
                   help="default: <pm-dir>/issues.json")
    p.add_argument("--out", default=None,
                   help="default: <pm-dir>/tickets.json")
    p.add_argument("--no-preserve", action="store_true",
                   help="take folder status verbatim; don't preserve live "
                        "status from an existing tickets.json")
    args = p.parse_args()

    pm_dir = Path(args.pm_dir)
    tickets_dir = pm_dir / "tickets"
    issues_path = Path(args.issues_path) if args.issues_path else pm_dir / "issues.json"
    out_path = Path(args.out) if args.out else pm_dir / "tickets.json"

    if not tickets_dir.exists():
        print(f"ERROR: {tickets_dir} not found", file=sys.stderr)
        sys.exit(2)

    prev = {}
    if not args.no_preserve and out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}

    issues = load_issues(issues_path)

    # Pre-aggregate per-ticket issue effort + status breakdown from issues.json
    def fresh():
        return {"effort": 0, "children": 0, "done": 0, "active": 0,
                "resolved": 0, "deprecated": 0}
    agg = defaultdict(fresh)
    if issues:
        for iid, ie in issues.items():
            parent = ie.get("ticket")
            if not parent:
                continue
            st = ie.get("status")
            eh = ie.get("effort_hours", 0) or 0
            if isinstance(eh, bool) or not isinstance(eh, (int, float)):
                eh = 0
            a = agg[parent]
            a["effort"] += eh
            a["children"] += 1
            if st == "done":
                a["done"] += 1
            if st == "deprecated":
                a["deprecated"] += 1
            if st in RESOLVED:
                a["resolved"] += 1
            if st not in DROP_FROM_COUNT:     # denominator excludes wont_do/deprecated
                a["active"] += 1

    out = {}
    errors = []

    for folder in sorted(tickets_dir.iterdir()):
        if not folder.is_dir():
            continue
        main_file = find_main_file(folder)
        if main_file is None:
            errors.append(f"{folder}: no main .md file (expected {folder.name}.md)")
            continue
        meta, _ = parse_md(main_file.read_text(encoding="utf-8"))
        tid = meta.get("id")
        if not tid:
            errors.append(f"{main_file}: missing 'id' in frontmatter")
            continue
        if tid != folder.name:
            errors.append(f"{main_file}: id {tid!r} != folder name {folder.name!r}")
            continue
        if tid in out:
            errors.append(f"{tid}: duplicate id")
            continue
        meta.setdefault("status", "todo")
        meta.setdefault("depends_on", [])
        meta.setdefault("satisfies_reqs", [])
        meta.setdefault("sprint", None)   # time-box membership; orthogonal to epic
        meta.setdefault("approval_required", False)  # PM acceptance gate (HITL)
        # Preserve live status when folder is still at seed default
        if meta["status"] == "todo" and tid in prev:
            meta["status"] = prev[tid].get("status", "todo")
        # Derived fields
        a = agg.get(tid)
        meta["effort_hours"] = a["effort"] if a else 0
        meta["issues_total"] = a["active"] if a else 0   # excludes wont_do/deprecated
        meta["issues_done"] = a["done"] if a else 0
        # Auto-resolve when every child is resolved (done/wont_do/deprecated).
        # Never override a status already terminal or awaiting_approval.
        if (a and a["children"] > 0 and a["resolved"] == a["children"]
                and meta["status"] not in RESOLVED
                and meta["status"] != "awaiting_approval"):
            if a["done"] > 0:
                meta["status"] = ("awaiting_approval"
                                  if meta.get("approval_required") else "done")
            elif a["deprecated"] == a["children"]:
                meta["status"] = "deprecated"
            else:
                meta["status"] = "wont_do"
        out[tid] = meta

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    sorted_out = {k: out[k] for k in sorted(out.keys())}
    out_path.write_text(json.dumps(sorted_out, indent=2) + "\n", encoding="utf-8")
    print(f"OK: assembled {len(sorted_out)} ticket(s) → {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
