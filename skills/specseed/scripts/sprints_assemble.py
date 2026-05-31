"""
sprints_assemble.py

Assembles .specseed/project_management/sprints.json from the per-sprint folders
under .specseed/project_management/sprints/<SPRINT-ID>/<SPRINT-ID>.md.

A sprint is a TIME-BOXED batch of tickets (~one week of effort, ~168h soft
budget). Sprints are ORTHOGONAL to epics: an epic groups tickets by outcome
(vertical), a sprint groups them by time (horizontal). A ticket belongs to one
epic AND one sprint. Sprints do NOT appear in ROADMAP.md (the strategic map) —
they drive TIMELINE.md (the execution schedule) and claim ordering.

Source of truth is the folders (frontmatter + prose). This script extracts the
YAML-ish frontmatter and writes the machine index that sprints_validate.py /
timeline_render.py / claim_issue.py consume.

Derived fields (computed here, not authored):
- effort_hours: sum of member tickets' effort_hours (themselves summed from
  issues by tickets_assemble.py).
- tickets_total / tickets_done: completion counts over member tickets.
- order: 0-based execution rank, by (starts date if present else id, id).

Run issues_assemble.py and tickets_assemble.py FIRST — the derived fields read
tickets.json. If tickets.json is absent, effort/counts fall back to 0 (warning).

status preserve: a sprint's status (planned|active|done) is authored in the
folder, but live status is preserved from an existing sprints.json when the
folder is still at the seed default `planned` (so a runtime `active` flag isn't
clobbered by a re-assemble). Auto-advances to `done` when the sprint has member
tickets and ALL are done/deprecated (unless deprecated/forced). --no-preserve
opts out.

Frontmatter format: flat `key: value`, structured values as inline JSON. See
issues_assemble.py docstring.

Exit: 0 OK, 1 structural error (dup/missing id, unparseable), 2 missing input.
"""

import argparse
import json
import sys
from pathlib import Path

DONE_STATES = {"done", "deprecated"}


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


def load_tickets(tickets_path):
    if not tickets_path.exists():
        print(f"WARNING: {tickets_path} not found — sprint effort_hours and "
              f"completion counts will be 0. Run tickets_assemble.py first.",
              file=sys.stderr)
        return None
    try:
        return json.loads(tickets_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"WARNING: malformed {tickets_path}: {e} — counts will be 0",
              file=sys.stderr)
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--tickets-path", default=None,
                   help="default: <pm-dir>/tickets.json")
    p.add_argument("--out", default=None, help="default: <pm-dir>/sprints.json")
    p.add_argument("--no-preserve", action="store_true",
                   help="take folder status verbatim; don't preserve live "
                        "status from an existing sprints.json")
    args = p.parse_args()

    pm_dir = Path(args.pm_dir)
    sprints_dir = pm_dir / "sprints"
    tickets_path = Path(args.tickets_path) if args.tickets_path else pm_dir / "tickets.json"
    out_path = Path(args.out) if args.out else pm_dir / "sprints.json"

    if not sprints_dir.exists():
        print(f"ERROR: {sprints_dir} not found", file=sys.stderr)
        sys.exit(2)

    prev = {}
    if not args.no_preserve and out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}

    tickets = load_tickets(tickets_path)

    out = {}
    errors = []

    for folder in sorted(sprints_dir.iterdir()):
        if not folder.is_dir():
            continue
        main_file = find_main_file(folder)
        if main_file is None:
            errors.append(f"{folder}: no main .md file (expected {folder.name}.md)")
            continue
        meta, _ = parse_md(main_file.read_text(encoding="utf-8"))
        sid = meta.get("id")
        if not sid:
            errors.append(f"{main_file}: missing 'id' in frontmatter")
            continue
        if sid != folder.name:
            errors.append(f"{main_file}: id {sid!r} != folder name {folder.name!r}")
            continue
        if sid in out:
            errors.append(f"{sid}: duplicate id")
            continue
        meta.setdefault("status", "planned")
        meta.setdefault("tickets", [])
        meta.setdefault("starts", None)
        meta.setdefault("ends", None)
        # Preserve live status when folder is still at seed default
        if meta["status"] == "planned" and sid in prev:
            meta["status"] = prev[sid].get("status", "planned")
        # Derived: effort + completion from member tickets
        member = meta.get("tickets", []) or []
        effort, total, done = 0, 0, 0
        if tickets:
            for tid in member:
                t = tickets.get(tid)
                if t is None:
                    continue
                total += 1
                eh = t.get("effort_hours", 0) or 0
                if isinstance(eh, bool) or not isinstance(eh, (int, float)):
                    eh = 0
                effort += eh
                if t.get("status") in DONE_STATES:
                    done += 1
        meta["effort_hours"] = effort
        meta["tickets_total"] = total
        meta["tickets_done"] = done
        # Auto-advance to done when all member tickets done
        if meta["status"] not in ("deprecated", "done") and total > 0 and done == total:
            meta["status"] = "done"
        out[sid] = meta

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Derive execution order: by (starts or far-future, id)
    def order_key(sid):
        s = out[sid].get("starts") or "9999-12-31"
        return (str(s), sid)

    for i, sid in enumerate(sorted(out.keys(), key=order_key)):
        out[sid]["order"] = i

    sorted_out = {k: out[k] for k in sorted(out.keys())}
    out_path.write_text(json.dumps(sorted_out, indent=2) + "\n", encoding="utf-8")
    print(f"OK: assembled {len(sorted_out)} sprint(s) → {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
