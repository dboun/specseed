"""
agents_runner.py — the always-on orchestrator loop (see references/remote.md).

ONE laptop, single writer. Each iteration: (unless paused) claim + run the next ready
issue via the LOCAL `claude` CLI, then — if code review is on — run ONE review pass
(review an in_review issue, write review.json, apply review_gate.py). No Anthropic API
key — it shells out to the already-authenticated Claude Code CLI on this machine
(prompt piped on stdin). Per-role models come from `config.runner.models`
(implement/review/merge), each falling back to `config.runner.model`. QA needs no
special handling — a `type:qa` issue is claimed + run like any other issue.

Config comes from `.specseed/memory/config.json` (the portable "how-you-work"
file — validated on startup; the runner refuses to start if it's invalid or
missing). `config.backend.enabled` picks the backend; `config.runner` supplies
the model/effort/interval/turn-cap/allowed-tools/retry knobs below. Per-repo
mirror STATE (repo, issue map, cursors) lives separately in `remote.json`.

Works in BOTH backends, keyed off `config.backend.enabled`:
  - **local-only** (`backend.enabled:false`): just the work loop + file-based control.
    No mirror reconcile, no CONTROL issue, no comments.
  - **mirror** (`backend.enabled:true`): additionally reconcile the github/gitlab mirror
    each pass, process CONTROL-issue commands, and comment status transitions back.

Start (the skill writes a `<repo>_agents_runner.py` shim at the repo root):
    python <repo>_agents_runner.py &

Control (no launchd / daemon) — works in both backends:
    echo pause > .specseed/memory/runner.ctl   # finish current, then idle
    echo run   > .specseed/memory/runner.ctl   # resume
    echo stop  > .specseed/memory/runner.ctl   # graceful exit after current step
    Ctrl-C / kill <pid>                         # same graceful stop (signal handler)
In mirror mode you can also comment the verbs on the pinned CONTROL issue (e.g. phone).
"""

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

# Sibling script dirs: the portable config loader (core/) + the remote-mirror
# cluster (remote/).
sys.path.insert(0, str(Path(__file__).resolve().parent / "remote"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))
import config as cfgmod
import review_gate
import remote_config as rc
import remote_control
import remote_sync

# Built in main() from config.runner. Prompt is piped on stdin (so `-p` takes no
# positional prompt). CLAUDE_CMD is the default (the `implement` role); the review
# step builds its own command with the `review` model when one is configured.
CLAUDE_CMD = None
RUNNER = None


def build_claude_cmd(runner, model=None):
    return ["claude", "-p",
            "--model", str(model or runner["model"]),
            "--effort", str(runner["effort"]),
            "--permission-mode", "auto",
            "--allowedTools", ",".join(runner["allowed_tools"]),
            "--max-turns", str(runner["max_turns"])]


def cmd_for_role(cfg, role):
    """Claude command for a runner role ('implement'/'review'/'merge'); the model
    falls back to runner.model when no per-role override is set. `implement`
    reuses the prebuilt CLAUDE_CMD."""
    if role == "implement":
        return CLAUDE_CMD
    return build_claude_cmd(RUNNER, cfgmod.runner_model(cfg, role))

WORK_PROMPT = ("Per ./CLAUDE.md, claim and fully implement the next ready issue "
               "(run .specseed/scripts/core/claim_issue.py, then execute it to its "
               "finish flow). If no issue is ready, do nothing and say so.")

REVIEW_PROMPT = (
    "You are a CODE REVIEWER, not the implementer. Review the completed work for "
    "issue {iid} (status in_review) per ./CLAUDE.md. Inspect the diff on its branch "
    "and the issue's technical acceptance criteria "
    "(.specseed/project_management/issues/{iid}/{iid}.md). Then write your verdict to "
    ".specseed/project_management/issues/{iid}/review.json as: "
    '{{"confidence": <0-100 int, how sure the code is correct>, '
    '"verdict": "pass"|"fail"|"changes_requested", '
    '"difficulty_assessment": "easy"|"hard"|null, '
    '"findings": [<short strings>], "model": "<your model>", "reviewed_at": "<ISO8601>"}}. '
    "Do NOT edit source, do NOT change the issue status, do NOT merge — only write "
    "review.json. The gate script decides what happens next.")

_STOP = False


def _on_signal(signum, frame):
    global _STOP
    _STOP = True


def _kill_flag(root):
    return rc.find_root(root) / ".specseed" / "memory" / "runner.kill"


def run_claude(root, prompt, log, cmd=None):
    """Run the local Claude Code CLI (prompt piped on stdin, output appended to
    runner.log); poll the kill flag and terminate on demand. Returns None if killed.
    `cmd` overrides the default CLAUDE_CMD (e.g. the review-model command)."""
    kf = _kill_flag(root)
    if kf.exists():
        kf.unlink()
    log(f"claude: {prompt[:60]}…")
    logf = open(rc.find_root(root) / ".specseed" / "memory" / "runner.log",
                "a", encoding="utf-8")
    proc = subprocess.Popen(cmd or CLAUDE_CMD, cwd=str(rc.find_root(root)),
                            stdin=subprocess.PIPE, stdout=logf,
                            stderr=subprocess.STDOUT, text=True)
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except Exception:
        pass
    killed = False
    while proc.poll() is None:
        if kf.exists() or _STOP:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            killed = True
            if kf.exists():
                kf.unlink()
            log("claude terminated (kill/stop)")
            break
        time.sleep(1)
    logf.close()
    return None if killed else proc.returncode


# --------------------------------------------------------------------------- #
# retry cooldown — a failed claude run (e.g. hit a session limit / exit!=0) pauses
# only CLAUDE attempts for `retry_delay_minutes`; reconcile + CONTROL keep running.
# --------------------------------------------------------------------------- #
def _retry_path(root):
    return rc.find_root(root) / ".specseed" / "memory" / "runner.retry"


def cooldown_remaining(root):
    """Seconds left in the retry cooldown, else 0."""
    p = _retry_path(root)
    if not p.exists():
        return 0
    try:
        rem = float(p.read_text(encoding="utf-8").strip()) - time.time()
    except Exception:
        return 0
    return rem if rem > 0 else 0


def attempt_claude(root, cfg, prompt, log, cmd=None):
    """Run claude unless in cooldown. Returns 0 on success, 'cooldown' if skipped,
    None if killed, or the nonzero exit code (cooldown then armed). `cmd` overrides
    the default command (per-role model)."""
    rem = cooldown_remaining(root)
    if rem > 0:
        log(f"claude in retry cooldown (~{int(rem // 60)}m left); skipping")
        return "cooldown"
    code = run_claude(root, prompt, log, cmd=cmd)
    if code is None:
        return None                                   # killed — not a failure
    if code != 0:
        delay = int(cfg.get("retry_delay_minutes", 30)) * 60
        _retry_path(root).write_text(str(time.time() + delay), encoding="utf-8")
        log(f"claude exit {code} (e.g. session limit) -> retry in {delay // 60}m")
        return code
    p = _retry_path(root)                             # success clears any cooldown
    if p.exists():
        p.unlink()
    return 0


def _statuses(root):
    p = rc.find_root(root) / ".specseed" / "project_management" / "issues.json"
    return {k: v.get("status") for k, v in json.loads(p.read_text()).items()} if p.exists() else {}


def _open_approval(root, iid):
    """The open approval record for issue `iid` from approvals.json, or None."""
    p = rc.find_root(root) / ".specseed" / "project_management" / "approvals.json"
    if not p.exists():
        return None
    for r in json.loads(p.read_text()):
        if r.get("issue") == iid:
            return r
    return None


def notify_changes(root, cfg, remote, before, after, log):
    """Compare status snapshots; comment done/blocked/awaiting_approval transitions
    on the mirror. Returns True if anything changed (so the caller re-pushes)."""
    changed = False
    for iid, st in after.items():
        if st == before.get(iid):
            continue
        changed = True
        if remote is None:
            continue                              # local-only: no mirror to notify
        n = remote_sync._num(cfg, iid)
        if not n:
            continue
        if st == "done":
            remote.comment(n, f"✓ Done — `{iid}`.")
        elif st == "blocked":
            remote.comment(n, f"⛔ Blocked — `{iid}`. See the issue's notes / spec_concern.")
        elif st == "awaiting_approval":
            ap = _open_approval(root, iid)
            if ap:
                remote.comment(n, (
                    f"🔔 Needs your approval — `{iid}` A{ap['n']}: {ap.get('summary','')}\n"
                    f"- why: {ap.get('why') or ap.get('kind','')}\n"
                    f"- options: {ap.get('options','—')}\n"
                    f"- detail: `.specseed/project_management/issues/{iid}/approval.md`\n"
                    f"Reply on the CONTROL issue: `approve {iid} <opt>` / `reject {iid} <note>`."))
            else:
                remote.comment(n, f"🔔 `{iid}` awaiting approval (no request detail found).")
    return changed


def work_step(root, cfg, remote, log):
    """Claim + run the next issue; comment on done/blocked transitions. Returns
    True if any issue status changed (so the caller re-pushes the mirror)."""
    before = _statuses(root)
    if attempt_claude(root, cfg, WORK_PROMPT, log) != 0:   # killed / cooldown / failed
        return False
    after = _statuses(root)
    return notify_changes(root, cfg, remote, before, after, log)


def _pm(root):
    return rc.find_root(root) / ".specseed" / "project_management"


def review_step(root, cfg, remote, log):
    """If code review is ON, review at most one in_review issue per pass: run the
    reviewer (the `review`-role model) to write review.json, then apply review_gate.
    QA needs no step — a `type:qa` issue flows through the normal work loop. Returns
    True if any status changed."""
    issues_path = _pm(root) / "issues.json"
    if not issues_path.exists():
        return False
    try:
        issues = json.loads(issues_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    in_review = sorted(iid for iid, v in issues.items() if v.get("status") == "in_review")
    if not in_review:
        return False

    pcfg = cfgmod.load_config(root) or {}
    rcfg = cfgmod.review_config(pcfg)
    if not rcfg.get("enabled") or rcfg.get("scope") == "none":
        return False

    target, has_verdict = None, False
    for iid in in_review:
        # review_applies honors both the config scope (by difficulty) and an explicit
        # per-issue review_required flag.
        if not review_gate.review_applies(rcfg, issues[iid]):
            continue                              # out of review scope → leave for a human
        target = iid
        has_verdict = (_pm(root) / "issues" / iid / "review.json").exists()
        break
    if target is None:
        return False

    before = _statuses(root)
    if not has_verdict:
        res = attempt_claude(root, cfg, REVIEW_PROMPT.format(iid=target), log,
                             cmd=cmd_for_role(pcfg, "review"))
        if res != 0:                              # killed / cooldown / failed — retry next pass
            return False
    gate = _pm(root).parent / "scripts" / "core" / "review_gate.py"
    try:
        subprocess.run([sys.executable, str(gate), target, "--apply",
                        "--pm-dir", str(_pm(root))],
                       cwd=str(rc.find_root(root)), check=False)
    except Exception as e:
        log(f"review_gate error for {target}: {e}")
    after = _statuses(root)
    return notify_changes(root, cfg, remote, before, after, log)


def execute_actions(root, cfg, remote, actions, log):
    for a in actions:
        verb, n = a["verb"], a["reply_to"]
        if verb == "sync":
            remote.comment(n, "🔄 Synced.")          # reconcile already ran this loop
        elif verb == "claim-next":
            if work_step(root, cfg, remote, log):
                remote_sync.sync_push(root, cfg, remote, log=log)
                remote_sync.push_dashboards(root, cfg, remote, log=log)
            remote.comment(n, "▶️ Ran the next ready issue.")
        elif verb in ("adapt", "plan-next", "approve", "reject"):
            # approve/reject route to approve mode (its triggers include the bare
            # verbs) and flip issue status, so re-push the mirror after a good run.
            slash = f"/specseed {verb}" + (f" {a['text']}" if a.get("text") else "")
            res = attempt_claude(root, cfg, slash, log)
            if res == 0:
                remote.comment(n, f"✅ Ran `{slash}`.")
                if verb in ("approve", "reject"):
                    remote_sync.sync_push(root, cfg, remote, log=log)
                    remote_sync.push_dashboards(root, cfg, remote, log=log)
            elif res == "cooldown":
                remote.comment(n, "⏳ In retry cooldown (a prior run hit a limit). "
                                  "Retry later, or `resume` after it clears.")
            else:
                remote.comment(n, f"⚠️ `{slash}` failed; will retry automatically.")


def one_pass(root, cfg, remote, log):
    """One loop iteration. `remote is None` → local-only: the mirror reconcile +
    CONTROL channel are skipped; only the work step (and file-based control) run."""
    state = remote_control.read_ctl(root)
    if state == "stop":
        return cfg, True
    if remote is not None:
        # 1. reconcile mirror (pull new work, drift/heal, push, dashboards)
        try:
            remote_sync.sync_pull(root, cfg, remote, log=log)
            remote_sync.sync_push(root, cfg, remote, log=log)
            remote_sync.push_dashboards(root, cfg, remote, log=log)
        except Exception as e:
            log(f"reconcile error: {e}")
        # 2. CONTROL commands
        try:
            actions, cfg = remote_control.process(root, cfg, remote, log=log)
            execute_actions(root, cfg, remote, actions, log)
        except Exception as e:
            log(f"control error: {e}")
    # 3. work, unless paused; re-push promptly if a status changed (mirror only).
    #    Then a review pass (reviews at most one in_review issue per loop).
    if remote_control.read_ctl(root) == "run":
        try:
            if work_step(root, cfg, remote, log) and remote is not None:
                remote_sync.sync_push(root, cfg, remote, log=log)
                remote_sync.push_dashboards(root, cfg, remote, log=log)
        except Exception as e:
            log(f"work error: {e}")
        try:
            if review_step(root, cfg, remote, log) and remote is not None:
                remote_sync.sync_push(root, cfg, remote, log=log)
                remote_sync.push_dashboards(root, cfg, remote, log=log)
        except Exception as e:
            log(f"review error: {e}")
    if remote is not None:
        rc.save_state(cfg, root)
    return cfg, False


def main(argv):
    global CLAUDE_CMD, RUNNER
    ap = argparse.ArgumentParser(description="specseed orchestrator loop (local-only or github/gitlab mirror)")
    ap.add_argument("--interval", type=int, default=None, help="seconds between passes (default: config.runner.interval)")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    args = ap.parse_args(argv)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    root = rc.find_root()

    # Portable config — fail fast if missing/invalid (the runner's contract).
    config = cfgmod.load_config(root)
    if config is None:
        print("ERROR: no .specseed/memory/config.json — run `/specseed configure` first",
              file=sys.stderr)
        return 2
    errs = cfgmod.validate(config)
    if errs:
        print("ERROR: config.json invalid:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 2

    runner = config["runner"]
    RUNNER = runner
    CLAUDE_CMD = build_claude_cmd(runner)
    interval = args.interval if args.interval is not None else runner["interval"]

    cfg, mirror, _ = rc.load_runtime(root)       # cfg = mirror state ∪ {provider}
    cfg["retry_delay_minutes"] = runner["retry_delay_minutes"]  # runner-owned; dropped on save_state
    remote = rc.Remote(cfg) if mirror else None  # local-only: work loop + file control
    logp = rc.find_root(root) / ".specseed" / "memory" / "runner.log"

    def log(msg):
        line = f"{rc.now_iso()} {msg}"
        print(line, flush=True)
        with open(logp, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"runner up ({'mirror: ' + str(cfg.get('provider')) if mirror else 'local-only'}; "
        f"model {runner['model']}/{runner['effort']}, interval {interval}s)")
    while not _STOP:
        cfg, stop = one_pass(root, cfg, remote, log)
        if stop or args.once:
            break
        for _ in range(interval):                # interruptible sleep
            if _STOP or remote_control.read_ctl(root) == "stop":
                break
            time.sleep(1)
    log("runner down")
    return 0


SHIM = '''\
#!/usr/bin/env python3
"""Auto-generated by specseed. Starts the specseed orchestrator loop (local-only
or github/gitlab mirror, per .specseed/memory/remote.json).
Run: python {name} &   (see .specseed/scripts/agents_runner.py for control/env)"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / ".specseed" / "scripts"))
import agents_runner
if __name__ == "__main__":
    sys.exit(agents_runner.main(sys.argv[1:]))
'''


def write_shim(repo_name, root=None):
    """Write the repo-root `<repo>_agents_runner.py` shim. Returns its path."""
    root = rc.find_root(root)
    path = root / f"{repo_name}_agents_runner.py"
    path.write_text(SHIM.format(name=path.name), encoding="utf-8")
    path.chmod(0o755)
    return path


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--write-shim":
        print(write_shim(sys.argv[2]))
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
