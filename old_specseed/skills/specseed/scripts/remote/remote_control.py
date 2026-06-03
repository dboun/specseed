"""
remote_control.py — the command channel (see references/remote.md).

Polls EVERY new issue comment in one repo-wide pass, keeps only allowlisted authors,
and dispatches by WHERE the comment landed:
  - the permanent CONTROL issue → global runner ops (status / pause / resume / kill /
    crs / approvals roll-up) handled inline, plus the project work verbs (sync /
    claim-next / adapt / plan-next) RETURNED to the runner (agents_runner.py) which
    spawns the local CLI and replies.
  - a mapped WORK issue → per-issue gate resolution (approve / reject / hold an
    `APR-NNNN`) RETURNED to the runner; any other (free-form) text is appended to that
    issue's instruction inbox (`inbox.md`) for the runner's fresh-context inbox_step to
    batch-process — an ask/question, NOT a gate decision.

Standalone (debug):
  python remote_control.py poll    # process once; print returned work-actions
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import remote_config as rc
import remote_sync as rs              # reverse issue-map (remote number -> local id)

# the CR entity + the per-issue inbox live in core/; reuse their pure I/O (the roll-up +
# `crs` verb; the work-issue free-form intake)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import change_requests as crmod  # noqa: E402
import inbox  # noqa: E402

CONTROL_VERBS = {"status", "pause", "resume", "kill", "approvals", "crs"}
WORK_VERBS = {"sync", "claim-next", "adapt", "plan-next", "approve", "reject", "hold"}
# verbs that resolve a per-issue HITL gate (valid on a WORK issue or the CONTROL issue)
GATE_VERBS = {"approve", "reject", "hold"}
CHEATSHEET = ("verbs: status · sync · pause · resume · kill · claim-next · "
              "adapt <text> · plan-next · approvals · approve <ID> [opt] · "
              "reject <ID> <note> · hold <ID> · crs")
# our own bot replies — skip on re-read so the channel never loops on itself. Every
# reply this module (and the runner's surfaces) posts starts with one of these.
BOT_PREFIXES = ("**status**", "**approvals**", "**crs**", "Ingested",
                "✓", "⛔", "⚠️", "✅", "▶️", "⏸️", "🛑", "🔄", "⏳", "🔔", "📝")
APR_RE = re.compile(r"\bAPR-\d+\b", re.I)


def _mem(root):
    return rc.find_root(root) / ".specseed" / "memory"


def _pm(root):
    return rc.find_root(root) / ".specseed" / "project_management"


def ctl_path(root):
    return _mem(root) / "runner.ctl"


def read_ctl(root):
    p = ctl_path(root)
    return p.read_text(encoding="utf-8").strip() if p.exists() else "run"


def write_ctl(root, value):
    p = ctl_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value + "\n", encoding="utf-8")


def _allowed(cfg, remote, author):
    allow = cfg.get("allowlist") or []
    if not allow:                      # empty -> the PAT owner only (the single dev)
        try:
            owner = remote.whoami().get("login") or remote.whoami().get("username")
            allow = [owner]
        except Exception:
            return False
    return author in allow


def _parse(body):
    """First non-empty line -> (verb, rest)."""
    line = next((l.strip() for l in (body or "").splitlines() if l.strip()), "")
    if not line:
        return None, ""
    parts = line.split(None, 1)
    return parts[0].lower(), (parts[1] if len(parts) > 1 else "")


def _cooldown_line(root):
    rp = _mem(root) / "runner.retry"
    if not rp.exists():
        return ""
    try:
        import time
        text = rp.read_text(encoding="utf-8").strip()
        try:
            rem = float(text) - time.time()       # legacy bare timestamp
            return (f"\n- ⏳ retry cooldown: ~{int(rem // 60)}m left"
                    if rem > 0 else "")
        except ValueError:
            pass
        data = json.loads(text)
        cds = data.get("cooldowns") if isinstance(data, dict) else {}
        if not isinstance(cds, dict):
            return ""
        rems = []
        for until in cds.values():
            try:
                rem = float(until) - time.time()
            except (TypeError, ValueError):
                continue
            if rem > 0:
                rems.append(rem)
        if not rems:
            return ""
        label = "1 chain" if len(rems) == 1 else f"{len(rems)} chains"
        return f"\n- ⏳ retry cooldown: {label}, max ~{int(max(rems) // 60)}m left"
    except Exception:
        return ""


def _status_reply(root, cfg):
    pm = rc.find_root(root) / ".specseed" / "project_management"
    issues = json.loads((pm / "issues.json").read_text()) if (pm / "issues.json").exists() else {}
    sprints = json.loads((pm / "sprints.json").read_text()) if (pm / "sprints.json").exists() else {}
    in_flight = [i for i, v in issues.items() if v.get("status") == "in_progress"]
    ready = [i for i, v in issues.items() if v.get("status") in ("todo", "blocked")]
    active = [s.get("id") for s in sprints.values() if s.get("status") == "in_progress"]
    cooldown = _cooldown_line(root)
    cr_line = _cr_rollup(list_crs_safe(root))
    return (f"**status**\n- runner: `{read_ctl(root)}`\n"
            f"- active sprint: {', '.join(active) or '—'}\n"
            f"- in-flight: {', '.join(in_flight) or '—'}\n"
            f"- ready issues: {len(ready)}{cooldown}"
            + (f"\n- {cr_line}" if cr_line else ""))


def list_crs_safe(root):
    """change_requests.list_crs, tolerant of a tree with no change_requests/ dir."""
    try:
        return crmod.list_crs(root)
    except Exception:
        return []


def _cr_rollup(crs):
    """One-line CR summary for the `status` reply (None when no live CRs). 'live' = not
    yet done/rejected; respec_complete reads as 'regenerating'."""
    live = [c for c in crs if c.get("status") not in ("done", "rejected")]
    if not live:
        return None

    def phrase(c):
        if c.get("status") == "respec_complete":
            return f"{c['id']} regenerating"
        if c.get("turn") == "human":
            return f"{c['id']} awaiting you"
        return f"{c['id']} in progress"

    return f"CRs: {len(live)} open (" + ", ".join(phrase(c) for c in live) + ")"


def _crs_reply(root):
    """Full `crs` verb reply: every CR with its status/turn + issue link."""
    crs = list_crs_safe(root)
    if not crs:
        return "**crs**\n- none"
    lines = ["**crs** — spec-change requests:"]
    for c in crs:
        ref = f" (#{c['remote_issue']})" if c.get("remote_issue") else ""
        turn = c.get("turn")
        turn_s = f", turn: {turn}" if turn else ""
        lines.append(f"- `{c['id']}` {c.get('title', '')} — {c.get('status')}"
                     f"{turn_s}{ref}")
    return "\n".join(lines)


def _approvals_reply(root):
    pm = rc.find_root(root) / ".specseed" / "project_management"
    p = pm / "approvals.json"
    recs = json.loads(p.read_text()) if p.exists() else []
    if not recs:
        return "**approvals**\n- none pending ✅"
    lines = ["**approvals** — pending HITL gates:"]
    for r in recs:
        handle = r.get("apr") or r["issue"]
        lines.append(f"- `{r['issue']}` {r.get('apr') or 'A'+str(r['n'])}: "
                     f"{r.get('summary','')} ({r.get('kind','')}). "
                     f"Resolve: `approve {handle}` / `reject {handle} <note>`")
    return "\n".join(lines)


def _open_aprs(root, iid):
    """Open-gate handles for one issue id, read from approvals.json (the render's
    single-writer index). Each is an `APR-NNNN` (falls back to `A<n>` pre-stamp)."""
    p = rc.find_root(root) / ".specseed" / "project_management" / "approvals.json"
    if not p.exists():
        return []
    try:
        recs = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [r.get("apr") or ("A" + str(r.get("n"))) for r in recs
            if r.get("issue") == iid]


def _comment_id(c):
    v = c.get("id")
    return str(v) if v is not None else ""


def _mark(ts, cid):
    return {"created_at": ts or "", "id": str(cid or "")}


def _mark_key(m):
    return (m.get("created_at") or "", m.get("id") or "")


def _cursor_poll_since(cfg):
    """Remote APIs take a timestamp, but our local cursor also stores ids handled at
    that timestamp. Poll one second earlier when ids exist so same-second retries are
    still visible, then filter precisely in process()."""
    since = cfg.get("cli_cursor")
    if not since or not cfg.get("cli_cursor_ids"):
        return since
    try:
        dt = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return since
    return (dt - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _advance_cursor(cfg, mark):
    ts, cid = mark.get("created_at") or "", str(mark.get("id") or "")
    cur = cfg.get("cli_cursor") or ""
    ids = {str(x) for x in (cfg.get("cli_cursor_ids") or [])}
    if not ts:
        return
    if ts > cur:
        cfg["cli_cursor"] = ts
        cfg["cli_cursor_ids"] = [cid] if cid else []
    elif ts == cur and cid:
        ids.add(cid)
        cfg["cli_cursor_ids"] = sorted(ids)


def _work_action_text(root, iid, rest):
    """Build the resolver verb-remainder (`<handle> [note|option]`) for a work-issue
    gate verb, or None when the target gate is undetermined (caller asks which APR).
    An explicit `APR-NNNN` in the comment names the gate; otherwise a bare issue id is
    used iff the issue has exactly one open gate (the resolver picks it)."""
    if APR_RE.search(rest or ""):
        return rest                               # caller already checked issue ownership
    if len(_open_aprs(root, iid)) == 1:
        return (iid + (" " + rest if rest else "")).strip()   # resolver -> the one gate
    return None                                   # 0 or >1 open -> ambiguous


def _wrong_apr_reply(root, iid, apr):
    aprs = _open_aprs(root, iid)
    if aprs:
        return (f"⚠️ `{apr}` is not an open gate on `{iid}`. Open gate(s) here: "
                f"{', '.join(aprs)}.")
    return f"⚠️ `{apr}` is not an open gate on `{iid}`."


def _ambiguous_reply(root, iid):
    aprs = _open_aprs(root, iid)
    if not aprs:
        return f"⚠️ No open gate on `{iid}`. (`approvals` on CONTROL lists pending gates.)"
    return (f"⚠️ `{iid}` has {len(aprs)} open gates: {', '.join(aprs)}. "
            f"Which one? e.g. `approve {aprs[0]}`.")


def _inbox_ack(author):
    return (f"📝 @{author}: queued. I'll read the issue + the actual code and either act "
            f"or answer here. (For a HITL gate use `approve <APR-NNNN>` / "
            f"`reject <APR-NNNN> <note>` / `hold <APR-NNNN>`.)")


def _watermark(since, marks, actions, since_ids=None):
    """New cursor after one pass. Advances over comments fully handled in-process, but
    NEVER past the earliest still-pending (returned) action — the runner advances the
    rest per successful action in execute_actions, so a failed command is retried."""
    cap = min((_mark_key(a) for a in actions if a.get("created_at")), default=None)
    handled = [m for m in marks if m.get("created_at") and (cap is None or _mark_key(m) < cap)]
    cfg = {"cli_cursor": since, "cli_cursor_ids": sorted(str(x) for x in (since_ids or []))}
    for m in sorted(handled, key=_mark_key):
        _advance_cursor(cfg, m)
    return cfg.get("cli_cursor"), cfg.get("cli_cursor_ids") or []


def process(root, cfg, remote, log=print):
    """Process new issue comments repo-wide. Returns (work_actions, cfg). CONTROL +
    inline-handled comments reply in place; work/gate verbs are returned as
    [{'verb','text','reply_to','created_at'}...] for the runner to execute."""
    control_no = cfg["permanent"].get("control")
    if not control_no:
        return [], cfg
    since = cfg.get("cli_cursor")
    since_ids = {str(x) for x in (cfg.get("cli_cursor_ids") or [])}
    actions, marks = [], []                        # marks: ts of in-process-handled comments
    for c in remote.comments_since(_cursor_poll_since(cfg)):
        ts = c.get("created_at") or ""
        cid = _comment_id(c)
        if since and ts and ts < since:
            continue
        if since and ts == since and (not cid or cid in since_ids):
            continue
        body = c.get("body") or ""
        if body.startswith(BOT_PREFIXES) or rs.is_bot_comment(body):  # our own bot replies
            marks.append(_mark(ts, cid))
            continue
        cnum, author = c.get("issue_number"), c.get("author")
        if cnum == control_no:
            handled = _dispatch_control(root, cfg, remote, author, body, actions, ts, cid, log)
        else:
            iid = rs._iid(cfg, cnum)
            # CR issues are the CR relay's domain (remote_sync), NOT the work-issue control
            # channel — they're mapped only so reconcile won't re-ingest them. Skip here.
            if iid is None or str(iid).startswith("CR-"):   # ROADMAP / CR / unmapped — not ours
                marks.append(_mark(ts, cid))
                continue
            handled = _dispatch_work(root, cfg, remote, cnum, iid, author, body, actions, ts, cid)
        if not handled:
            marks.append(_mark(ts, cid))
    cfg["cli_cursor"], cfg["cli_cursor_ids"] = _watermark(since, marks, actions, since_ids)
    return actions, cfg


def _dispatch_control(root, cfg, remote, author, body, actions, ts, cid, log):
    """CONTROL-issue grammar. Returns True iff it queued a (returned) work action."""
    control_no = cfg["permanent"]["control"]
    if not _allowed(cfg, remote, author):
        remote.comment(control_no, f"⛔ @{author}: not authorized.")
        return False
    verb, rest = _parse(body)
    if verb in CONTROL_VERBS:
        _do_control(root, cfg, remote, control_no, verb, log)
        return False
    if verb in WORK_VERBS:
        action = {"verb": verb, "text": rest, "reply_to": control_no, "created_at": ts}
        if cid:
            action["id"] = cid
        actions.append(action)
        return True
    remote.comment(control_no, f"⚠️ unknown verb `{verb}`. {CHEATSHEET}")
    return False


def _dispatch_work(root, cfg, remote, num, iid, author, body, actions, ts, cid):
    """WORK-issue grammar: a gate verb (approve/reject/hold) resolves that issue's
    gate by `APR-NNNN`; any other (free-form) text is appended to the issue's
    instruction inbox for the runner's inbox_step to batch-process. Returns True iff it
    queued a (returned) resolve action (the inbox append is handled in-process)."""
    if not _allowed(cfg, remote, author):
        remote.comment(num, f"⛔ @{author}: not authorized.")
        return False
    verb, rest = _parse(body)
    if verb not in GATE_VERBS:
        # free-form ask/question → durable local inbox entry; ack once (the real reply
        # comes after the next inbox_step). The whole comment body is the instruction.
        inbox.append_entry(_pm(root), iid, author, body)
        remote.comment(num, _inbox_ack(author))
        return False
    explicit = APR_RE.search(rest or "")
    if explicit:
        apr = explicit.group(0).upper()
        if apr not in {a.upper() for a in _open_aprs(root, iid)}:
            remote.comment(num, _wrong_apr_reply(root, iid, apr))
            return False
    text = _work_action_text(root, iid, rest)
    if text is None:
        remote.comment(num, _ambiguous_reply(root, iid))
        return False
    action = {"verb": verb, "text": text, "reply_to": num, "created_at": ts}
    if cid:
        action["id"] = cid
    actions.append(action)
    return True


def _do_control(root, cfg, remote, control_no, verb, log):
    if verb == "status":
        remote.comment(control_no, _status_reply(root, cfg))
    elif verb == "approvals":
        remote.comment(control_no, _approvals_reply(root))
    elif verb == "crs":
        remote.comment(control_no, _crs_reply(root))
    elif verb == "pause":
        write_ctl(root, "pause")
        remote.comment(control_no, "⏸️ Pausing after the current issue finishes.")
    elif verb == "resume":
        write_ctl(root, "run")
        remote.comment(control_no, "▶️ Resumed.")
    elif verb == "kill":
        write_ctl(root, "pause")
        (_mem(root) / "runner.kill").write_text("1\n", encoding="utf-8")
        remote.comment(control_no, "🛑 Killing the in-flight agent; runner paused.")
    log(f"control verb: {verb}")


def main(argv):
    if not argv or argv[0] != "poll":
        print("usage: remote_control.py poll", file=sys.stderr)
        return 2
    root = rc.find_root()
    cfg, enabled, _ = rc.load_runtime(root)
    if not enabled:
        print("mirror off (backend.enabled=false in config.json)", file=sys.stderr)
        return 1
    actions, cfg = process(root, cfg, rc.Remote(cfg))
    rc.save_state(cfg, root)
    print(json.dumps(actions, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
