"""
agents_runner.py — the always-on orchestrator loop (see references/remote.md).

ONE laptop, single writer. Each iteration: (unless paused) peek the next ready issue
(`claim_issue.py --peek`), pick the agent chain for its (function, difficulty), and have
that agent claim + run it; then — if code review is on — run ONE review pass (review an
in_review issue, write review.json, apply review_gate.py). No Anthropic API key — it
shells out to the already-authenticated coding-agent CLI on this machine (prompt piped on
stdin). The agent matrix is `config.runner.agents`: FUNCTION (implement/review/qa) →
DIFFICULTY (easy/hard) → an ordered fallback chain of {provider, config_dir, model,
effort} specs (provider claude|codex). A type:qa issue routes to the `qa` function;
merges still ride the implement agent's finish flow.

Config comes from `.specseed/memory/config.json` (the portable "how-you-work"
file — validated on startup; the runner refuses to start if it's invalid or
missing). `config.backend.enabled` picks the backend; `config.runner` supplies
the agent matrix plus the global interval/turn-cap/allowed-tools/retry knobs. Per-repo
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
import os
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

# Global runner knobs (interval/max_turns/allowed_tools/retry), set in main() from
# config.runner. Per-task commands are built on the fly from the (function,difficulty)
# agent chain — see build_agent_cmd / run_agent_chain. Prompt is piped on stdin.
RUNNER = None


def build_claude_cmd(spec, runner):
    """(argv, env_overrides) for a Claude Code spec. config_dir → CLAUDE_CONFIG_DIR."""
    argv = ["claude", "-p",
            "--model", str(spec["model"]),
            "--effort", str(spec["effort"]),
            "--permission-mode", "auto",
            "--allowedTools", ",".join(runner["allowed_tools"]),
            "--max-turns", str(runner["max_turns"])]
    env = {"CLAUDE_CONFIG_DIR": str(spec["config_dir"])} if spec.get("config_dir") else {}
    return argv, env


def build_codex_cmd(spec, runner):
    """(argv, env_overrides) for a Codex spec. Prompt is piped on stdin (trailing
    '-'); config_dir → CODEX_HOME. max_turns/allowed_tools are Claude-only, ignored."""
    argv = ["codex", "exec",
            "--model", str(spec["model"]),
            "-c", f'model_reasoning_effort="{spec["effort"]}"',
            "--sandbox", "workspace-write",
            "--ask-for-approval", "never",
            "-"]
    env = {"CODEX_HOME": str(spec["config_dir"])} if spec.get("config_dir") else {}
    return argv, env


def build_agent_cmd(spec, runner):
    """(argv, env_overrides) for an agent spec, dispatched on spec.provider."""
    if spec.get("provider") == "codex":
        return build_codex_cmd(spec, runner)
    return build_claude_cmd(spec, runner)


WORK_PROMPT = ("Per ./CLAUDE.md, claim issue {iid} (run "
               "`python .specseed/scripts/core/claim_issue.py {iid}`) and then fully "
               "execute it to its finish flow (for a type:qa issue, follow the QA "
               "contract in CLAUDE.md). If the claim is refused — already resolved or "
               "in flight — do nothing and say so.")

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


def run_agent(root, prompt, log, argv, env=None):
    """Run a coding-agent CLI (`argv`, prompt piped on stdin, output appended to
    runner.log); poll the kill flag and terminate on demand. Returns None if killed.
    `env` (if given) is merged over os.environ — e.g. CLAUDE_CONFIG_DIR / CODEX_HOME."""
    kf = _kill_flag(root)
    if kf.exists():
        kf.unlink()
    log(f"agent: {prompt[:60]}…")
    logf = open(rc.find_root(root) / ".specseed" / "memory" / "runner.log",
                "a", encoding="utf-8")
    run_env = None
    if env:
        run_env = dict(os.environ)
        run_env.update(env)
    proc = subprocess.Popen(argv, cwd=str(rc.find_root(root)),
                            stdin=subprocess.PIPE, stdout=logf,
                            stderr=subprocess.STDOUT, text=True, env=run_env)
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
            log("agent terminated (kill/stop)")
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


def run_agent_chain(root, cfg, chain, prompt, log):
    """Run a task against an ordered fallback chain of specs. Returns 0 on the first
    success, 'cooldown' if skipped (in retry cooldown), None if killed, or the last
    nonzero exit code (cooldown then armed). A nonzero spec falls through to the next;
    the cooldown is armed only once the whole chain has failed."""
    rem = cooldown_remaining(root)
    if rem > 0:
        log(f"agent in retry cooldown (~{int(rem // 60)}m left); skipping")
        return "cooldown"
    last = None
    for i, spec in enumerate(chain):
        argv, env = build_agent_cmd(spec, RUNNER)
        tag = f"{spec.get('provider')}/{spec.get('model')}/{spec.get('effort')}"
        if i:
            log(f"fallback #{i} -> {tag}")
        code = run_agent(root, prompt, log, argv, env)
        if code is None:
            return None                               # killed — not a failure
        if code == 0:
            p = _retry_path(root)                     # success clears any cooldown
            if p.exists():
                p.unlink()
            return 0
        last = code
        log(f"agent exit {code} via {tag}")
    delay = int(cfg.get("retry_delay_minutes", 30)) * 60
    _retry_path(root).write_text(str(time.time() + delay), encoding="utf-8")
    log(f"all {len(chain)} agent spec(s) failed -> retry in {delay // 60}m")
    return last


def _bucket(difficulty):
    """Issue difficulty → agent-chain bucket ('easy'/'hard'; missing → hard)."""
    return "easy" if difficulty == "easy" else "hard"


def peek_next(root):
    """Read-only: the next ready issue as (iid, type, difficulty), or None. Shells
    `claim_issue.py --peek` (no claim) so the runner can pick the right agent chain
    before spawning."""
    claim = _pm(root).parent / "scripts" / "core" / "claim_issue.py"
    try:
        out = subprocess.run([sys.executable, str(claim), "--peek",
                              "--pm-dir", str(_pm(root))],
                             cwd=str(rc.find_root(root)),
                             capture_output=True, text=True, check=False)
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return None
    iid = data.get("issue_id")
    if not iid:
        return None
    return iid, data.get("type"), data.get("difficulty")


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
    """Peek the next ready issue, choose its (function, difficulty) agent chain, then
    have that agent claim + execute the issue. Function is `qa` for a type:qa issue,
    else `implement` (merges still ride the implement agent's finish flow). Returns
    True if any issue status changed (so the caller re-pushes the mirror)."""
    peek = peek_next(root)
    if peek is None:
        return False                                  # nothing ready
    iid, itype, difficulty = peek
    function = "qa" if itype == "qa" else "implement"
    pcfg = cfgmod.load_config(root) or {}
    chain = cfgmod.agent_chain(pcfg, function, _bucket(difficulty))
    before = _statuses(root)
    if run_agent_chain(root, cfg, chain, WORK_PROMPT.format(iid=iid), log) != 0:
        return False                                  # killed / cooldown / failed
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
        chain = cfgmod.agent_chain(pcfg, "review", _bucket(issues[target].get("difficulty")))
        res = run_agent_chain(root, cfg, chain, REVIEW_PROMPT.format(iid=target), log)
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
    # CONTROL-issue verbs (adapt/plan-next/approve/reject) are meta routes, not issue
    # execution — run them on the implement/hard agent chain (the default executor).
    pcfg = cfgmod.load_config(root) or {}
    ctl_chain = cfgmod.agent_chain(pcfg, "implement", "hard")
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
            res = run_agent_chain(root, cfg, ctl_chain, slash, log)
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
    global RUNNER
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

    impl = cfgmod.agent_main(config, "implement", "hard")
    log(f"runner up ({'mirror: ' + str(cfg.get('provider')) if mirror else 'local-only'}; "
        f"impl/hard {impl['provider']}:{impl['model']}/{impl['effort']}, interval {interval}s)")
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
