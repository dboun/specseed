"""
remote_control.py — the command channel (see references/remote.md).

Polls comments on the permanent CONTROL github issue, keeps only allowlisted
authors, dispatches the fixed verb set, replies in-thread. Control verbs (status /
pause / resume / kill) are handled inline. Work verbs (sync / claim-next / adapt /
plan-next) need to spawn the local `claude` CLI, so they're RETURNED to the runner
(agents_runner.py), which executes them and replies.

Standalone (debug):
  python remote_control.py poll    # process once; print returned work-actions
"""

import json
import sys
from pathlib import Path

import remote_config as rc

CONTROL_VERBS = {"status", "pause", "resume", "kill", "approvals"}
WORK_VERBS = {"sync", "claim-next", "adapt", "plan-next", "approve", "reject"}
CHEATSHEET = ("verbs: status · sync · pause · resume · kill · claim-next · "
              "adapt <text> · plan-next · approvals · approve <ID> [opt] · "
              "reject <ID> <note>")


def _mem(root):
    return rc.find_root(root) / ".specseed" / "memory"


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


def _status_reply(root, cfg):
    pm = rc.find_root(root) / ".specseed" / "project_management"
    issues = json.loads((pm / "issues.json").read_text()) if (pm / "issues.json").exists() else {}
    sprints = json.loads((pm / "sprints.json").read_text()) if (pm / "sprints.json").exists() else {}
    in_flight = [i for i, v in issues.items() if v.get("status") == "in_progress"]
    ready = [i for i, v in issues.items() if v.get("status") in ("todo", "blocked")]
    active = [s.get("id") for s in sprints.values() if s.get("status") == "in_progress"]
    cooldown = ""
    rp = _mem(root) / "runner.retry"
    if rp.exists():
        try:
            import time
            rem = float(rp.read_text(encoding="utf-8").strip()) - time.time()
            if rem > 0:
                cooldown = f"\n- ⏳ retry cooldown: ~{int(rem // 60)}m left"
        except Exception:
            pass
    return (f"**status**\n- runner: `{read_ctl(root)}`\n"
            f"- active sprint: {', '.join(active) or '—'}\n"
            f"- in-flight: {', '.join(in_flight) or '—'}\n"
            f"- ready issues: {len(ready)}{cooldown}")


def _approvals_reply(root):
    pm = rc.find_root(root) / ".specseed" / "project_management"
    p = pm / "approvals.json"
    recs = json.loads(p.read_text()) if p.exists() else []
    if not recs:
        return "**approvals**\n- none pending ✅"
    lines = ["**approvals** — pending HITL gates:"]
    for r in recs:
        lines.append(f"- `{r['issue']}` A{r['n']}: {r.get('summary','')} "
                     f"({r.get('kind','')}). Resolve: `approve {r['issue']}` / "
                     f"`reject {r['issue']} <note>`")
    return "\n".join(lines)


def process(root, cfg, remote, log=print):
    """Process new CONTROL comments. Returns (work_actions, cfg). Control verbs
    replied inline; work verbs returned as [{'verb','text','reply_to'}...]."""
    control_no = cfg["permanent"].get("control")
    if not control_no:
        return [], cfg
    since = cfg.get("cli_cursor")
    actions, newest = [], since
    for c in remote.comments_since(since):
        newest = max(newest or "", c.get("created_at") or "")   # high-water mark over ALL comments
        if c["issue_number"] != control_no:
            continue
        if since and c.get("created_at") and c["created_at"] <= since:
            continue
        author, body = c.get("author"), c.get("body") or ""
        if body.startswith(("**status**", "✓", "⛔", "⚠️", "✅", "▶️", "⏸️", "🛑",
                            "🔄", "⏳", "🔔", "**approvals**", "Ingested")):
            continue                                  # our own bot replies
        if not _allowed(cfg, remote, author):
            remote.comment(control_no, f"@{author}: not authorized.")
            continue
        verb, rest = _parse(body)
        if verb in CONTROL_VERBS:
            _do_control(root, cfg, remote, control_no, verb, log)
        elif verb in WORK_VERBS:
            actions.append({"verb": verb, "text": rest, "reply_to": control_no})
        else:
            remote.comment(control_no, f"unknown verb `{verb}`. {CHEATSHEET}")
    if newest:
        cfg["cli_cursor"] = newest
    return actions, cfg


def _do_control(root, cfg, remote, control_no, verb, log):
    if verb == "status":
        remote.comment(control_no, _status_reply(root, cfg))
    elif verb == "approvals":
        remote.comment(control_no, _approvals_reply(root))
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
