"""
agents_runner.py — the always-on orchestrator loop (see references/remote.md).

ONE laptop, single writer. Each iteration: reconcile the remote mirror, process
CONTROL commands, and (unless paused) claim + run the next ready issue via the LOCAL
`claude` CLI. No Anthropic API key — it shells out to the already-authenticated
Claude Code CLI on this machine (prompt piped on stdin).

Start (the skill writes a `<repo>_agents_runner.py` shim at the repo root):
    python <repo>_agents_runner.py &

Control (no launchd / daemon):
    echo pause > .specseed/memory/runner.ctl   # finish current, then idle
    echo run   > .specseed/memory/runner.ctl   # resume
    echo stop  > .specseed/memory/runner.ctl   # graceful exit after current step
    Ctrl-C / kill <pid>                         # same graceful stop (signal handler)
Or, from a phone, comment the verbs on the pinned CONTROL issue.
"""

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import remote_config as rc
import remote_control
import remote_sync

# Hardcoded for now. Prompt is piped on stdin (so `-p` takes no positional prompt).
CLAUDE_CMD = ["claude", "-p", "--model", "opus", "--effort", "high",
              "--permission-mode", "auto", "--allowedTools", "Read,Edit,Bash",
              "--max-turns", "400"]

WORK_PROMPT = ("Per ./CLAUDE.md, claim and fully implement the next ready issue "
               "(run .specseed/scripts/claim_issue.py, then execute it to its "
               "finish flow). If no issue is ready, do nothing and say so.")

_STOP = False


def _on_signal(signum, frame):
    global _STOP
    _STOP = True


def _kill_flag(root):
    return rc.find_root(root) / ".specseed" / "memory" / "runner.kill"


def run_claude(root, prompt, log):
    """Run the local Claude Code CLI (prompt piped on stdin, output appended to
    runner.log); poll the kill flag and terminate on demand. Returns None if killed."""
    kf = _kill_flag(root)
    if kf.exists():
        kf.unlink()
    log(f"claude: {prompt[:60]}…")
    logf = open(rc.find_root(root) / ".specseed" / "memory" / "runner.log",
                "a", encoding="utf-8")
    proc = subprocess.Popen(CLAUDE_CMD, cwd=str(rc.find_root(root)),
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


def attempt_claude(root, cfg, prompt, log):
    """Run claude unless in cooldown. Returns 0 on success, 'cooldown' if skipped,
    None if killed, or the nonzero exit code (cooldown then armed)."""
    rem = cooldown_remaining(root)
    if rem > 0:
        log(f"claude in retry cooldown (~{int(rem // 60)}m left); skipping")
        return "cooldown"
    code = run_claude(root, prompt, log)
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


def work_step(root, cfg, remote, log):
    """Claim + run the next issue; comment on done/blocked transitions. Returns
    True if any issue status changed (so the caller re-pushes the mirror)."""
    before = _statuses(root)
    if attempt_claude(root, cfg, WORK_PROMPT, log) != 0:   # killed / cooldown / failed
        return False
    after = _statuses(root)
    changed = False
    for iid, st in after.items():
        if st == before.get(iid):
            continue
        changed = True
        n = remote_sync._num(cfg, iid)
        if not n:
            continue
        if st == "done":
            remote.comment(n, f"✓ Done — `{iid}`.")
        elif st == "blocked":
            remote.comment(n, f"⛔ Blocked — `{iid}`. See the issue's notes / spec_concern.")
    return changed


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
        elif verb in ("adapt", "plan-next"):
            slash = f"/specseed {verb}" + (f" {a['text']}" if a.get("text") else "")
            res = attempt_claude(root, cfg, slash, log)
            if res == 0:
                remote.comment(n, f"✅ Ran `{slash}`.")
            elif res == "cooldown":
                remote.comment(n, "⏳ In retry cooldown (a prior run hit a limit). "
                                  "Retry later, or `resume` after it clears.")
            else:
                remote.comment(n, f"⚠️ `{slash}` failed; will retry automatically.")


def one_pass(root, cfg, remote, log):
    state = remote_control.read_ctl(root)
    if state == "stop":
        return cfg, True
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
    # 3. work, unless paused; re-push promptly if a status changed
    if remote_control.read_ctl(root) == "run":
        try:
            if work_step(root, cfg, remote, log):
                remote_sync.sync_push(root, cfg, remote, log=log)
                remote_sync.push_dashboards(root, cfg, remote, log=log)
        except Exception as e:
            log(f"work error: {e}")
    rc.save_config(cfg, root)
    return cfg, False


def main(argv):
    ap = argparse.ArgumentParser(description="specseed remote orchestrator loop")
    ap.add_argument("--interval", type=int, default=45, help="seconds between passes")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    args = ap.parse_args(argv)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    root = rc.find_root()
    cfg = rc.load_config(root)
    if cfg is None or not cfg.get("enabled"):
        print("mirror off — nothing to run", file=sys.stderr)
        return 1
    remote = rc.Remote(cfg)
    logp = rc.find_root(root) / ".specseed" / "memory" / "runner.log"

    def log(msg):
        line = f"{rc.now_iso()} {msg}"
        print(line, flush=True)
        with open(logp, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log("runner up")
    while not _STOP:
        cfg, stop = one_pass(root, cfg, remote, log)
        if stop or args.once:
            break
        for _ in range(args.interval):           # interruptible sleep
            if _STOP or remote_control.read_ctl(root) == "stop":
                break
            time.sleep(1)
    log("runner down")
    return 0


SHIM = '''\
#!/usr/bin/env python3
"""Auto-generated by specseed. Starts the remote orchestrator loop.
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
