"""
issues_assemble.py

Assembles .specseed/project_management/issues.json from the per-issue folders
under .specseed/project_management/issues/<ISSUE-ID>/<ISSUE-ID>.md.

Source of truth is the folders (frontmatter + prose). This script extracts the
YAML-ish frontmatter from each issue's main file and writes the machine index
that claim_issue.py / issues_validate.py / verification_map.py consume. The
markdown body (technical acceptance criteria, notes) is NOT copied into the
JSON — agents read the .md directly for prose.

Frontmatter format: flat `key: value`, one per line. Structured values
(lists, objects) use inline JSON, e.g.:
    depends_on: ["FEAT-0100"]
    artifacts: {"touches": ["src/api/"], "tests": ["tests/test_x.py"], "migrations": []}
Bare (unquoted) strings are allowed for scalar values: `status: todo`.

Run this BEFORE tickets_assemble.py (ticket effort + completion counts are
summed from issues) and before claim_issue.py / issues_validate.py.

Source-of-truth split: the FOLDERS own authored content (title, component,
artifacts, deps, ...). The generated issues.json owns LIVE RUNTIME STATE
(status / claimed_at / claimed_by) once execution starts. So when re-assembling
over an existing issues.json, runtime state is PRESERVED rather than reset:
if the folder's `status` is the default `todo`, the existing index status +
claim fields are kept (don't regress an in-flight/done issue). If the folder
sets an explicit non-`todo` status (author deprecating/forcing), the folder
wins. Pass --no-preserve to take folder values verbatim.

Exit: 0 OK, 1 structural error (dup/missing id, unparseable), 2 missing input.
"""

import argparse
import json
import sys
from pathlib import Path


def coerce(v):
    """Parse a frontmatter scalar. Try JSON (numbers, null, bool, lists,
    objects, quoted strings); fall back to a bare unquoted string."""
    v = v.strip()
    if v == "":
        return ""
    try:
        return json.loads(v)
    except Exception:
        return v.strip().strip('"').strip("'")


def parse_md(text):
    """Split YAML-ish frontmatter from body. Returns (meta_dict, body_str)."""
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
    """Prefer <folder-name>.md; else the only .md in the folder."""
    named = folder / f"{folder.name}.md"
    if named.exists():
        return named
    mds = [p for p in folder.glob("*.md")]
    if len(mds) == 1:
        return mds[0]
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--out", default=None,
                   help="output path (default: <pm-dir>/issues.json)")
    p.add_argument("--no-preserve", action="store_true",
                   help="take folder status/claim verbatim, don't preserve "
                        "live runtime state from an existing issues.json")
    args = p.parse_args()

    pm_dir = Path(args.pm_dir)
    issues_dir = pm_dir / "issues"
    out_path = Path(args.out) if args.out else pm_dir / "issues.json"

    if not issues_dir.exists():
        print(f"ERROR: {issues_dir} not found", file=sys.stderr)
        sys.exit(2)

    prev = {}
    if not args.no_preserve and out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}

    out = {}
    errors = []

    for folder in sorted(issues_dir.iterdir()):
        if not folder.is_dir():
            continue
        main_file = find_main_file(folder)
        if main_file is None:
            errors.append(f"{folder}: no main .md file (expected {folder.name}.md)")
            continue
        meta, _ = parse_md(main_file.read_text(encoding="utf-8"))
        iid = meta.get("id")
        if not iid:
            errors.append(f"{main_file}: missing 'id' in frontmatter")
            continue
        if iid != folder.name:
            errors.append(f"{main_file}: id {iid!r} != folder name {folder.name!r}")
            continue
        if iid in out:
            errors.append(f"{iid}: duplicate id")
            continue
        # Normalize the claim/status defaults so downstream tools see full shape
        meta.setdefault("status", "todo")
        meta.setdefault("claimed_at", None)
        meta.setdefault("claimed_by", None)
        meta.setdefault("depends_on", [])
        meta.setdefault("artifacts", {})
        # Preserve live runtime state when the folder is still at the seed default
        if meta["status"] == "todo" and iid in prev:
            p = prev[iid]
            meta["status"] = p.get("status", "todo")
            meta["claimed_at"] = p.get("claimed_at")
            meta["claimed_by"] = p.get("claimed_by")
        out[iid] = meta

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    sorted_out = {k: out[k] for k in sorted(out.keys())}
    out_path.write_text(json.dumps(sorted_out, indent=2) + "\n", encoding="utf-8")
    print(f"OK: assembled {len(sorted_out)} issue(s) → {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
