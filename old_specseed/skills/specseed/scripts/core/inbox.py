"""
inbox.py — per-issue free-form instruction inbox (pure I/O + processed cursor).

A work issue's `inbox.md` is the durable local record of free-form human asks /
questions that arrive on that issue (e.g. as comments on the mirror's work issue:
"add more comments", "don't do it that way", "why did you handle X like that?"). It is
NOT a decision channel — approve/reject/hold HITL gates resolve DETERMINISTICALLY via
approvals_resolve.py. The runner batch-processes the unprocessed entries with a
FRESH-CONTEXT agent (see agents_runner.inbox_step), records the agent's reply, and
advances the cursor here. The agent does the thinking; this module does the bookkeeping.

Layout (per issue, under project_management/issues/<id>/):
  inbox.md     append-only log. `# Inbox: <id>` header, then entries:
                 ### IN-<seq> — <ISO> — <author>
                 <message body>
               Agent replies carry `agent (re: IN-<seq>[, IN-<seq>])` as the author.
  inbox.state  one line `processed_through: IN-<seq>` — the runner's cursor.

`seq` is a monotonic per-issue counter (count-based, NOT a bare timestamp — that dodges
the same-second pitfall the CR/CONTROL cursors hit). Single writer (the runner), so no
locking is needed.

Stdlib only. Pure I/O — no agent, no network.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

# `### IN-<seq> — <ISO> — <author>`. The ISO field carries no em-dash, so a non-greedy
# capture splits cleanly on the ` — ` separators (em-dash is what we emit, like the
# approval.md machine artifact — humanizer's em-dash ban is prose-only).
HEAD_RE = re.compile(r"^###\s+IN-(\d+)\s+—\s+(.+?)\s+—\s+(.+?)\s*$")
_RE_REF_RE = re.compile(r"IN-(\d+)")
_CURSOR_RE = re.compile(r"processed_through:\s*IN-(\d+)")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inbox_path(pm_dir, issue):
    return Path(pm_dir) / "issues" / issue / "inbox.md"


def state_path(pm_dir, issue):
    return Path(pm_dir) / "issues" / issue / "inbox.state"


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #
def _parse_re(author):
    """The `IN-<seq>` numbers an agent reply answers, from its `(re: IN-1, IN-2)`."""
    m = re.search(r"re:\s*([^)]*)", author)
    return [int(x) for x in _RE_REF_RE.findall(m.group(1))] if m else []


def parse_entries(text):
    """List of {seq, iso, author, is_agent, re, body} from inbox.md text, in file order.
    Body is every line after a `### IN-…` header up to the next header (trailing/leading
    blank lines stripped)."""
    entries, cur = [], None
    for ln in (text or "").splitlines():
        m = HEAD_RE.match(ln)
        if m:
            author = m.group(3).strip()
            cur = {"seq": int(m.group(1)), "iso": m.group(2).strip(),
                   "author": author,
                   "is_agent": author == "agent" or author.startswith("agent ("),
                   "re": _parse_re(author), "body": []}
            entries.append(cur)
        elif cur is not None:
            cur["body"].append(ln)
    for e in entries:
        e["body"] = "\n".join(e["body"]).strip("\n")
    return entries


def read_entries(pm_dir, issue):
    p = inbox_path(pm_dir, issue)
    return parse_entries(p.read_text(encoding="utf-8")) if p.exists() else []


def max_seq(entries):
    return max((e["seq"] for e in entries), default=0)


def _next_seq(entries):
    return max_seq(entries) + 1


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #
def append_entry(pm_dir, issue, author, body, re_seqs=None, iso=None):
    """Append one entry to the issue's inbox.md; returns its (monotonic) seq. `author`
    is a remote username for a human ask, or the literal 'agent' for a runner-recorded
    reply — pass `re_seqs` to stamp `agent (re: IN-…)`. Creates the file + `# Inbox:`
    header on first write."""
    p = inbox_path(pm_dir, issue)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    seq = _next_seq(parse_entries(existing))
    label = author
    if author == "agent" and re_seqs:
        label = "agent (re: " + ", ".join(f"IN-{s}" for s in re_seqs) + ")"
    block = f"### IN-{seq} — {iso or now_iso()} — {label}\n{(body or '').strip(chr(10))}\n"
    if not existing:
        text = f"# Inbox: {issue}\n\n{block}"
    else:
        text = (existing if existing.endswith("\n") else existing + "\n") + "\n" + block
    p.write_text(text, encoding="utf-8")
    return seq


# --------------------------------------------------------------------------- #
# processed cursor (single-writer; no lock)
# --------------------------------------------------------------------------- #
def read_cursor(pm_dir, issue):
    """The processed-through seq (0 when nothing processed / no state file yet)."""
    p = state_path(pm_dir, issue)
    if not p.exists():
        return 0
    m = _CURSOR_RE.search(p.read_text(encoding="utf-8"))
    return int(m.group(1)) if m else 0


def write_cursor(pm_dir, issue, seq):
    p = state_path(pm_dir, issue)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"processed_through: IN-{seq}\n", encoding="utf-8")
    return p


def unprocessed(pm_dir, issue):
    """The next batch: human (non-agent) entries with seq > the processed cursor, in
    order. Agent replies are skipped so a recorded reply never re-enters the queue even
    though its seq is above the cursor."""
    cursor = read_cursor(pm_dir, issue)
    return [e for e in read_entries(pm_dir, issue)
            if not e["is_agent"] and e["seq"] > cursor]
