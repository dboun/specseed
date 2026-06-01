"""
approvals_render.py — collect open human-approval requests into one index.

The implementation agent (and the optional remote mirror) park a gated action by
appending an entry to `.specseed/project_management/issues/<id>/approval.md`:

    ## A<N> — <summary>
    - **Opened:** <ISO>
    - **Kind:** gate:<category> | run-action | git-conflict | entity-approval
    - **Status:** open
    - **What I need ...:** ...
    ...
    ## Resolved A<N> (<ISO>): <decision> — <note>    <-- added on resolution

This script scans every issue's approval.md, finds the OPEN entries (a `## A<N>`
whose Status is `open` and which has no matching `## Resolved A<N>`), and writes:
  - APPROVALS.md  — human-readable pending list (the `approve` route reads this)
  - approvals.json — machine index (the runner / remote_sync read this)

Source of truth is the per-issue approval.md files; this index is generated, like
ROADMAP/TIMELINE. Run it after parking or resolving an approval.

Usage:
  python .specseed/scripts/approvals_render.py
  python .specseed/scripts/approvals_render.py --check   # exit 1 if index is stale

Exit: 0 OK (or --check clean), 1 (--check drift), 2 missing pm dir.
"""

import argparse
import json
import re
import sys
from pathlib import Path

HEAD_RE = re.compile(r"^##\s+A(\d+)\s*(?:—|-)?\s*(.*)$")
RESOLVED_RE = re.compile(r"^##\s+Resolved\s+A(\d+)\b", re.IGNORECASE)
FIELD_RE = re.compile(r"^-\s+\*\*(?P<key>[^:*]+):\*\*\s*(?P<val>.*)$")


def _parse_approval_file(text):
    """Return list of entry dicts: {n, summary, fields{}, resolved(bool)}."""
    lines = text.splitlines()
    resolved_ns = set()
    for ln in lines:
        m = RESOLVED_RE.match(ln)
        if m:
            resolved_ns.add(int(m.group(1)))

    entries, cur = [], None
    for ln in lines:
        if RESOLVED_RE.match(ln):
            cur = None
            continue
        h = HEAD_RE.match(ln)
        if h:
            cur = {"n": int(h.group(1)), "summary": h.group(2).strip(),
                   "fields": {}, "resolved": int(h.group(1)) in resolved_ns}
            entries.append(cur)
            continue
        if cur is not None:
            f = FIELD_RE.match(ln)
            if f:
                cur["fields"][f.group("key").strip().lower()] = f.group("val").strip()
    return entries


def collect(pm_dir):
    """Return list of open-approval records across all issues."""
    issues_dir = pm_dir / "issues"
    out = []
    if not issues_dir.is_dir():
        return out
    for issue_dir in sorted(p for p in issues_dir.iterdir() if p.is_dir()):
        af = issue_dir / "approval.md"
        if not af.exists():
            continue
        for e in _parse_approval_file(af.read_text(encoding="utf-8")):
            status = e["fields"].get("status", "open").lower()
            if e["resolved"] or status != "open":
                continue
            out.append({
                "issue": issue_dir.name,
                "n": e["n"],
                "summary": e["summary"],
                "kind": e["fields"].get("kind", ""),
                "opened": e["fields"].get("opened", ""),
                "why": e["fields"].get("why it's gated", ""),
                "options": e["fields"].get("options", ""),
            })
    return out


def render_md(records):
    L = ["# Pending approvals", ""]
    if not records:
        L.append("_None. No issue is awaiting human approval._")
        return "\n".join(L) + "\n"
    L.append(f"{len(records)} open request(s). Resolve via `/specseed approve` "
             "(or `approve <ID>` on the CONTROL issue if the mirror is on).")
    L.append("")
    for r in records:
        L.append(f"## {r['issue']} · A{r['n']} — {r['summary']}")
        if r["kind"]:
            L.append(f"- **Kind:** {r['kind']}")
        if r["opened"]:
            L.append(f"- **Opened:** {r['opened']}")
        if r["why"]:
            L.append(f"- **Why gated:** {r['why']}")
        if r["options"]:
            L.append(f"- **Options:** {r['options']}")
        L.append(f"- **Detail:** `.specseed/project_management/issues/{r['issue']}/approval.md`")
        L.append("")
    return "\n".join(L) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--check", action="store_true",
                   help="report drift and exit 1 without writing")
    args = p.parse_args()

    pm = Path(args.pm_dir)
    if not pm.is_dir():
        print(f"ERROR: {pm} not found", file=sys.stderr)
        sys.exit(2)

    records = collect(pm)
    md, js = render_md(records), json.dumps(records, indent=2) + "\n"
    md_path, js_path = pm / "APPROVALS.md", pm / "approvals.json"

    if args.check:
        stale = (not md_path.exists() or md_path.read_text(encoding="utf-8") != md
                 or not js_path.exists() or js_path.read_text(encoding="utf-8") != js)
        if stale:
            print("DRIFT: APPROVALS index is stale", file=sys.stderr)
            sys.exit(1)
        print("OK: approvals index up to date")
        sys.exit(0)

    md_path.write_text(md, encoding="utf-8")
    js_path.write_text(js, encoding="utf-8")
    print(f"OK: {len(records)} open approval(s) → {md_path}, {js_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
