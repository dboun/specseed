"""
approvals_render.py — collect open human-approval requests into one index.

The implementation agent (and the optional remote mirror) park a gated action by
appending an entry to `.specseed/project_management/issues/<id>/approval.md`:

    ## A<N> — <summary>
    - **Id:** APR-NNNN              <-- STAMPED by this script (left blank by the agent)
    - **Opened:** <ISO>
    - **Kind:** gate:<category> | run-action | handoff | git-conflict | entity-approval
    - **Status:** open
    - **What I need ...:** ...
    ...
    ## Resolved A<N> (<ISO>): <decision> — <note>    <-- added on resolution

`A<N>` is the per-issue in-file anchor (a local counter the impl agent writes with no
global coordination — multiple agents may park in parallel). `APR-NNNN` is the global,
human-typeable handle: this script is the single writer that ASSIGNS + STAMPS one onto
each open entry the first time it sees it (max-scan + 1 across all issues), rewriting
the source approval.md in place. It also stamps `- **Surfaced:** <ISO>` once the runner
has announced a gate (so the same gate is never re-announced).

This script scans every issue's approval.md, finds the OPEN entries (a `## A<N>`
whose Status is `open` and which has no matching `## Resolved A<N>`), and writes:
  - APPROVALS.md  — human-readable pending list (the `approve` route reads this)
  - approvals.json — machine index (the runner / remote_sync read this)

Source of truth is the per-issue approval.md files; this index is generated, like
ROADMAP/TIMELINE. Run it after parking or resolving an approval. NOTE (contract): unlike
the other renders this one MUTATES the source approval.md files (id/surfaced stamps) —
safe because it is the single writer inside the runner loop.

Usage:
  python .specseed/scripts/core/approvals_render.py
  python .specseed/scripts/core/approvals_render.py --check   # exit 1 if index is stale

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
ID_LINE_RE = re.compile(r"^-\s+\*\*Id:\*\*", re.IGNORECASE)
APR_FMT = "APR-{:04d}"
APR_TOKEN_RE = re.compile(r"APR-(\d+)")
APR_VALUE_RE = re.compile(r"^APR-\d+$")


def _parse_approval_file(text):
    """Return list of entry dicts: {n, summary, fields{}, resolved(bool), line(int)}.
    `line` is the 0-based index of the `## A<N>` header (used for in-place stamping)."""
    lines = text.splitlines()
    resolved_ns = set()
    for ln in lines:
        m = RESOLVED_RE.match(ln)
        if m:
            resolved_ns.add(int(m.group(1)))

    entries, cur = [], None
    for i, ln in enumerate(lines):
        if RESOLVED_RE.match(ln):
            cur = None
            continue
        h = HEAD_RE.match(ln)
        if h:
            cur = {"n": int(h.group(1)), "summary": h.group(2).strip(),
                   "fields": {}, "resolved": int(h.group(1)) in resolved_ns,
                   "line": i}
            entries.append(cur)
            continue
        if cur is not None:
            f = FIELD_RE.match(ln)
            if f:
                cur["fields"][f.group("key").strip().lower()] = f.group("val").strip()
    return entries


def _apr_value(fields):
    """The entry's real APR id (matches APR-NNNN), or None — a blank/placeholder Id
    field (e.g. the template's `<assigned by the runner>`) counts as 'needs an id'."""
    v = (fields.get("id") or "").strip()
    return v if APR_VALUE_RE.match(v) else None


def _max_apr_in_text(text):
    return max((int(m.group(1)) for m in APR_TOKEN_RE.finditer(text)), default=0)


def _next_apr(pm_dir):
    """Global next APR number = max APR-NNNN across every issue's approval.md, + 1."""
    issues_dir = pm_dir / "issues"
    mx = 0
    if issues_dir.is_dir():
        for issue_dir in issues_dir.iterdir():
            af = issue_dir / "approval.md"
            if af.exists():
                mx = max(mx, _max_apr_in_text(af.read_text(encoding="utf-8")))
    return mx + 1


def _entry_block_end(lines, header_idx):
    """Index of the next `## ` header after header_idx (bounds an entry's field block)."""
    for j in range(header_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            return j
    return len(lines)


def assign_ids(pm_dir):
    """Stamp a global APR-NNNN `Id:` onto every OPEN entry that lacks a real one.
    Mutates the source approval.md files in place (single writer = the runner loop) and
    is idempotent: an entry that already has an APR-NNNN id is never renumbered, even
    after it is resolved. A blank/placeholder Id line is REPLACED; a missing one is
    inserted right after the `## A<N>` header. Returns the count stamped."""
    issues_dir = pm_dir / "issues"
    if not issues_dir.is_dir():
        return 0
    counter = _next_apr(pm_dir)
    stamped = 0
    for issue_dir in sorted(p for p in issues_dir.iterdir() if p.is_dir()):
        af = issue_dir / "approval.md"
        if not af.exists():
            continue
        text = af.read_text(encoding="utf-8")
        had_nl = text.endswith("\n")
        lines = text.splitlines()
        ops = []  # (header_idx, existing_id_line_idx_or_None, apr) assigned top-down
        for e in _parse_approval_file(text):
            status = e["fields"].get("status", "open").lower()
            if e["resolved"] or status != "open" or _apr_value(e["fields"]):
                continue
            id_idx = None
            for j in range(e["line"] + 1, _entry_block_end(lines, e["line"])):
                if ID_LINE_RE.match(lines[j]):
                    id_idx = j
                    break
            ops.append((e["line"], id_idx, APR_FMT.format(counter)))
            counter += 1
            stamped += 1
        if not ops:
            continue
        for header_idx, id_idx, apr in sorted(ops, key=lambda t: t[0], reverse=True):
            line = f"- **Id:** {apr}"
            if id_idx is not None:
                lines[id_idx] = line
            else:
                lines.insert(header_idx + 1, line)
        af.write_text("\n".join(lines) + ("\n" if had_nl else ""), encoding="utf-8")
    return stamped


def stamp_field(pm_dir, issue, n, key, value):
    """Insert `- **<Key>:** <value>` right after the `## A<n>` header of `issue`'s
    approval.md, unless that field is already present (idempotent). Used by the runner to
    stamp `Surfaced:` after announcing a gate. Returns True if it wrote."""
    af = pm_dir / "issues" / issue / "approval.md"
    if not af.exists():
        return False
    text = af.read_text(encoding="utf-8")
    had_nl = text.endswith("\n")
    lines = text.splitlines()
    for e in _parse_approval_file(text):
        if e["n"] != n or e["resolved"]:
            continue
        if e["fields"].get(key.lower()):
            return False
        lines.insert(e["line"] + 1, f"- **{key}:** {value}")
        af.write_text("\n".join(lines) + ("\n" if had_nl else ""), encoding="utf-8")
        return True
    return False


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
                "apr": _apr_value(e["fields"]) or "",
                "n": e["n"],
                "summary": e["summary"],
                "kind": e["fields"].get("kind", ""),
                "opened": e["fields"].get("opened", ""),
                "why": e["fields"].get("why it's gated", ""),
                "options": e["fields"].get("options", ""),
                "handoff": e["fields"].get("handoff", ""),
                "surfaced": bool(e["fields"].get("surfaced")),
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
        handle = r.get("apr") or f"A{r['n']}"
        L.append(f"## {r['issue']} · {handle} — {r['summary']}")
        if r.get("apr"):
            L.append(f"- **Id:** {r['apr']}")
        if r["kind"]:
            L.append(f"- **Kind:** {r['kind']}")
        if r["opened"]:
            L.append(f"- **Opened:** {r['opened']}")
        if r["why"]:
            L.append(f"- **Why gated:** {r['why']}")
        if r["options"]:
            L.append(f"- **Options:** {r['options']}")
        if r.get("handoff"):
            L.append(f"- **Handoff:** {r['handoff']}")
        L.append(f"- **Detail:** `.specseed/project_management/issues/{r['issue']}/approval.md`")
        L.append("")
    return "\n".join(L) + "\n"


def write_index(pm_dir):
    """Render APPROVALS.md + approvals.json from the (already id-stamped) approval.md
    files. Returns the records written. Does NOT stamp ids — call assign_ids first."""
    records = collect(pm_dir)
    (pm_dir / "APPROVALS.md").write_text(render_md(records), encoding="utf-8")
    (pm_dir / "approvals.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return records


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

    md_path, js_path = pm / "APPROVALS.md", pm / "approvals.json"

    if args.check:
        # Read-only: never stamp ids / mutate sources in --check.
        records = collect(pm)
        md, js = render_md(records), json.dumps(records, indent=2) + "\n"
        stale = (not md_path.exists() or md_path.read_text(encoding="utf-8") != md
                 or not js_path.exists() or js_path.read_text(encoding="utf-8") != js)
        if stale:
            print("DRIFT: APPROVALS index is stale", file=sys.stderr)
            sys.exit(1)
        print("OK: approvals index up to date")
        sys.exit(0)

    assign_ids(pm)                      # stamp global APR-NNNN onto new open entries
    records = write_index(pm)
    print(f"OK: {len(records)} open approval(s) → {md_path}, {js_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
