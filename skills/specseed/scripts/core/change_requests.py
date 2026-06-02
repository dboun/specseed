"""
change_requests.py — pure CR (change-request) entity I/O.

A CR (`CR-NNNN`) is a first-class spec-change entity, sibling of `spec/` and
`project_management/` under `.specseed/`. It is NOT a work item: it does not satisfy
a requirement, it MUTATES the requirements and regenerates work via `adapt`. The local
folder is ground truth; phases 3-5 (runner respec mode, remote mirror) import these
functions directly — keep the signatures stable.

Folder: `.specseed/change_requests/<CR-NNNN>/cr.md`. Flat `key: value` frontmatter with
the same inline-JSON convention as tickets/issues (so the parser stays stdlib-only).

Stdlib only. Pure I/O — no agent, no network.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfgmod  # noqa: E402

STATUSES = ("open", "respec_complete", "done", "rejected")
TURNS = ("agent", "human", None)

# frontmatter field order (the body — Request / Log — follows the closing ---).
_FM_FIELDS = (
    "id", "title", "status", "turn", "priority", "created_at",
    "session_id", "branch", "remote_issue", "comment_cursor",
)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_root(start=None):
    return cfgmod.find_root(start)


def cr_dir(root):
    return Path(root) / ".specseed" / "change_requests"


# --------------------------------------------------------------------------- #
# serialization helpers
# --------------------------------------------------------------------------- #
def _fmt(v):
    """Frontmatter value: None → null; everything else stringified bare. Round-trips
    via _parse_fm_value (json.loads first, bare-string fallback)."""
    if v is None:
        return "null"
    return str(v)


def _parse_fm_value(line):
    """Value after the first colon, JSON-parsed (falls back to bare string).
    Mirrors add_work.parse_fm_value so `null`/ints round-trip but bare ids/uuids/
    branches survive."""
    v = line.split(":", 1)[1].strip()
    try:
        return json.loads(v)
    except Exception:
        return v.strip().strip('"').strip("'")


def _validate(status, turn):
    if status not in STATUSES:
        raise ValueError(
            f"invalid CR status {status!r} (expected one of {', '.join(STATUSES)})")
    if turn not in TURNS:
        allowed = ", ".join(repr(t) for t in TURNS)
        raise ValueError(f"invalid CR turn {turn!r} (expected one of {allowed})")


def _serialize(cr):
    """dict → cr.md text. `request` / `log` are the two body sections."""
    _validate(cr.get("status"), cr.get("turn"))
    lines = ["---"]
    for k in _FM_FIELDS:
        lines.append(f"{k}: {_fmt(cr.get(k))}")
    lines.append("---")
    lines.append("## Request")
    lines.append((cr.get("request") or "").rstrip("\n"))
    lines.append("")
    lines.append("## Log")
    lines.append((cr.get("log") or "").rstrip("\n"))
    return "\n".join(lines).rstrip("\n") + "\n"


def _deserialize(text):
    """cr.md text → dict (frontmatter fields + `request` / `log` body sections)."""
    cr = {}
    lines = text.splitlines()
    i = 0
    if i < len(lines) and lines[i].strip() == "---":
        i += 1
        while i < len(lines) and lines[i].strip() != "---":
            line = lines[i]
            if ":" in line:
                key = line.split(":", 1)[0].strip()
                cr[key] = _parse_fm_value(line)
            i += 1
        i += 1  # skip closing ---
    # body: split ONLY on the exact headers we emit ("## Request" / "## Log"), so a
    # `## ...` line inside an ingested request body stays content instead of being
    # misread as a section header.
    section = None
    body = {"Request": [], "Log": []}
    while i < len(lines):
        line = lines[i]
        if line.strip() in ("## Request", "## Log"):
            section = line.strip()[3:].strip()
        elif section in body:
            body[section].append(line)
        i += 1
    cr["request"] = "\n".join(body["Request"]).strip("\n")
    cr["log"] = "\n".join(body["Log"]).strip("\n")
    return cr


# --------------------------------------------------------------------------- #
# ids
# --------------------------------------------------------------------------- #
def next_cr_id(crd):
    """Max existing CR-NNNN under `crd`, +1, zero-padded to 4."""
    n = 0
    crd = Path(crd)
    if crd.exists():
        for child in crd.iterdir():
            name = child.name
            if child.is_dir() and name.startswith("CR-"):
                tail = name[len("CR-"):]
                if tail.isdigit():
                    n = max(n, int(tail))
    return f"CR-{n + 1:04d}"


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def _cr_file(root, cr_id):
    return cr_dir(root) / cr_id / "cr.md"


def create_cr(root, title, request, priority="urgent", remote_issue=None):
    """Scaffold a new CR folder. Returns the new id (CR-NNNN). status=open, turn=agent
    (a fresh request is queued for the agent)."""
    crd = cr_dir(root)
    cr_id = next_cr_id(crd)
    cr = {
        "id": cr_id,
        "title": title,
        "status": "open",
        "turn": "agent",
        "priority": priority,
        "created_at": now_iso(),
        "session_id": None,
        "branch": None,
        "remote_issue": remote_issue,
        "comment_cursor": None,
        "request": request or "",
        "log": "",
    }
    f = _cr_file(root, cr_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_serialize(cr), encoding="utf-8")
    return cr_id


def load_cr(root, cr_id):
    f = _cr_file(root, cr_id)
    if not f.exists():
        raise FileNotFoundError(f"no such CR: {cr_id} ({f})")
    return _deserialize(f.read_text(encoding="utf-8"))


def save_cr(root, cr):
    """Write a CR dict back to its folder (validates status/turn). Returns the path."""
    cr_id = cr["id"]
    f = _cr_file(root, cr_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_serialize(cr), encoding="utf-8")
    return f


def list_crs(root):
    """All CRs as slim dicts (id, title, status, turn, priority, created_at,
    remote_issue, branch, session_id, comment_cursor), FIFO by created_at. This is what
    the runner loop + the `status` roll-up consume."""
    crd = cr_dir(root)
    out = []
    if crd.exists():
        for child in sorted(crd.iterdir()):
            if child.is_dir() and child.name.startswith("CR-") and (child / "cr.md").exists():
                cr = load_cr(root, child.name)
                out.append({k: cr.get(k) for k in (
                    "id", "title", "status", "turn", "priority", "created_at",
                    "remote_issue", "branch", "session_id", "comment_cursor")})
    out.sort(key=lambda c: (c.get("created_at") or "", c.get("id") or ""))
    return out


# --------------------------------------------------------------------------- #
# field setters (load → mutate one field → save)
# --------------------------------------------------------------------------- #
def set_status(root, cr_id, status, turn="__keep__"):
    cr = load_cr(root, cr_id)
    cr["status"] = status
    if turn != "__keep__":
        cr["turn"] = turn
    save_cr(root, cr)
    return cr


def set_turn(root, cr_id, turn):
    cr = load_cr(root, cr_id)
    cr["turn"] = turn
    save_cr(root, cr)
    return cr


def set_session(root, cr_id, session_id):
    cr = load_cr(root, cr_id)
    cr["session_id"] = session_id
    save_cr(root, cr)
    return cr


def set_branch(root, cr_id, branch):
    cr = load_cr(root, cr_id)
    cr["branch"] = branch
    save_cr(root, cr)
    return cr


def advance_cursor(root, cr_id, cursor):
    cr = load_cr(root, cr_id)
    cr["comment_cursor"] = cursor
    save_cr(root, cr)
    return cr


def append_log(root, cr_id, note):
    """Append a timestamped line to the CR's running Log section."""
    cr = load_cr(root, cr_id)
    stamp = now_iso()
    existing = cr.get("log") or ""
    cr["log"] = (existing + ("\n" if existing else "") + f"- {stamp} {note}").strip("\n")
    save_cr(root, cr)
    return cr


# --------------------------------------------------------------------------- #
# pending comment (the relay's input slot)
# --------------------------------------------------------------------------- #
# The next human comment to feed the conductor is stashed in a sidecar FILE, not in
# frontmatter — a comment is free markdown (newlines, `## ` headers) that the flat
# `key: value` parser cannot hold. Remote comment-ingest (phase 4) writes it; the
# runner's relay reads then clears it (consume-once). Multiple comments arriving
# between turns are concatenated by the writer.
def pending_comment_path(root, cr_id):
    return cr_dir(root) / cr_id / "pending_comment.txt"


def set_pending_comment(root, cr_id, text):
    """Overwrite the stashed comment with `text`."""
    p = pending_comment_path(root, cr_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text or "", encoding="utf-8")
    return p


def append_pending_comment(root, cr_id, text):
    """Append `text` to the stash (separated by a blank line) so comments that land
    between relay turns all reach the next turn."""
    p = pending_comment_path(root, cr_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    joined = (existing + ("\n\n" if existing and text else "") + (text or ""))
    p.write_text(joined, encoding="utf-8")
    return p


def read_pending_comment(root, cr_id):
    """Peek the stashed comment (does NOT clear it). Returns text or None."""
    p = pending_comment_path(root, cr_id)
    return p.read_text(encoding="utf-8") if p.exists() else None


def clear_pending_comment(root, cr_id):
    """Remove the stash (after a relay turn has consumed it)."""
    p = pending_comment_path(root, cr_id)
    if p.exists():
        p.unlink()
    return p


# --------------------------------------------------------------------------- #
# tiny read-only CLI (the runner imports the functions directly; this is for humans)
# --------------------------------------------------------------------------- #
def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="inspect change requests (read-only)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list all CRs (FIFO)")
    sh = sub.add_parser("show", help="show one CR")
    sh.add_argument("cr_id")
    args = p.parse_args(argv)

    try:
        root = find_root()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.cmd == "show":
        try:
            print(json.dumps(load_cr(root, args.cr_id), indent=2))
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        return 0
    # default: list
    print(json.dumps(list_crs(root), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
