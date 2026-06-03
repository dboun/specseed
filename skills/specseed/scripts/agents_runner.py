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

Start (run the shipped runner directly — no repo-root shim):
    python .specseed/scripts/agents_runner.py &

Control (no launchd / daemon) — works in both backends:
    echo pause > .specseed/memory/runner.ctl   # finish current, then idle
    echo run   > .specseed/memory/runner.ctl   # resume
    echo stop  > .specseed/memory/runner.ctl   # graceful exit after current step
    Ctrl-C / kill <pid>                         # same graceful stop (signal handler)
In mirror mode you can also comment the verbs on the pinned CONTROL issue (e.g. phone).
"""

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

# Sibling script dirs: the portable config loader (core/) + the remote-mirror
# cluster (remote/).
sys.path.insert(0, str(Path(__file__).resolve().parent / "remote"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))
import approvals_render
import change_requests
import config as cfgmod
import inbox
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

INBOX_PROMPT = (
    "Per ./CLAUDE.md, process the unhandled instruction-inbox messages for issue {iid}: "
    "every `IN-<n>` entry after IN-{cursor} in "
    ".specseed/project_management/issues/{iid}/inbox.md. Treat them as ONE batch. Work "
    "FRESH: read the issue body, its plan.md / step reports, and the ACTUAL code + git "
    "diff for {iid} — do not rely on any earlier session's memory. For each message: a "
    "QUESTION → answer it; an IN-SCOPE instruction → do the rework within this issue's "
    "existing scope (if the issue is already done, re-open + re-claim it, do the work, "
    "re-close it, re-running the review gate if it applies); a SPEC / requirement / "
    "scope change → REJECT it and tell the user to file a CR (`add_change_request` or the "
    "`change-request` label); genuinely NEW work → REJECT it and point to `add_work`. "
    "NEVER edit settled spec docs from here and NEVER auto-file a CR. Finish by printing "
    "a single concise reply to the user: what you did, answered, or rejected (and why).")


def control_prompt(verb, text=""):
    """Natural-language prompt that auto-triggers the specseed skill for a CONTROL
    work-verb that genuinely needs a model (adapt / plan-next). NOT a `/specseed …`
    slash command — in `claude -p` (headless) mode user-invoked slash commands are
    not available, so the verb must be described as a task instead (same reason the
    CR relay uses RELAY_PROMPT). approve/reject do NOT come here: they resolve
    deterministically via approvals_resolve.py (see resolve_gate)."""
    text = (text or "").strip()
    if verb == "adapt":
        mode = "adapt"
        instr = (f"Update the spec per this request: {text}" if text
                 else "Update the spec per the latest request in this thread.")
        instr += (" This adapt was invoked by agents_runner from the CONTROL channel; "
                  "claiming has been paused before this run, so do not refuse on the "
                  "runner-running preflight.")
    elif verb == "plan-next":
        mode = "plan-next"
        instr = "Spec and break down the next roadmap slice."
        if text:
            instr += f" {text}"
    else:
        mode, instr = verb, text
    return f"Use the specseed skill in {mode} mode. {instr}".strip()


_STOP = False


def _on_signal(signum, frame):
    global _STOP
    _STOP = True


def _kill_flag(root):
    return rc.find_root(root) / ".specseed" / "memory" / "runner.kill"


def _pid_path(root):
    return rc.find_root(root) / ".specseed" / "memory" / "runner.pid"


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
# retry cooldown — a failed agent chain (e.g. hit a session limit / exit!=0) pauses
# only that function+difficulty+chain for `retry_delay_minutes`; other ready work with
# a different chain may still run. Reconcile + CONTROL keep running.
# --------------------------------------------------------------------------- #
def _retry_path(root):
    return rc.find_root(root) / ".specseed" / "memory" / "runner.retry"


def _chain_retry_key(function, bucket, chain):
    """Stable cooldown key for one runner function/bucket/ordered agent chain."""
    sig = json.dumps(chain or [], sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(sig.encode("utf-8")).hexdigest()[:12]
    return f"{function}:{bucket}:{digest}"


def _read_retry_state(root):
    """Return (legacy_until, cooldowns). Old runner.retry files were a bare float."""
    p = _retry_path(root)
    if not p.exists():
        return None, {}
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return None, {}
    try:
        return float(text), {}
    except Exception:
        pass
    try:
        data = json.loads(text)
    except Exception:
        return None, {}
    cds = data.get("cooldowns") if isinstance(data, dict) else {}
    if not isinstance(cds, dict):
        return None, {}
    out = {}
    for key, until in cds.items():
        try:
            out[str(key)] = float(until)
        except (TypeError, ValueError):
            continue
    return None, out


def _write_retry_state(root, cooldowns):
    p = _retry_path(root)
    now = time.time()
    active = {k: v for k, v in (cooldowns or {}).items() if v > now}
    if not active:
        if p.exists():
            p.unlink()
        return
    p.write_text(json.dumps({"version": 1, "cooldowns": active}, sort_keys=True),
                 encoding="utf-8")


def cooldown_remaining(root, retry_key=None):
    """Seconds left in retry cooldown, else 0. `retry_key=None` returns max active."""
    legacy_until, cooldowns = _read_retry_state(root)
    now = time.time()
    if legacy_until is not None:
        rem = legacy_until - now
        return rem if rem > 0 else 0
    if retry_key is not None:
        rem = cooldowns.get(retry_key, 0) - now
        return rem if rem > 0 else 0
    if not cooldowns:
        return 0
    rem = max(cooldowns.values()) - now
    return rem if rem > 0 else 0


def _arm_cooldown(root, retry_key, delay_seconds):
    legacy_until, cooldowns = _read_retry_state(root)
    if legacy_until is not None and legacy_until > time.time():
        cooldowns["legacy"] = legacy_until
    cooldowns[retry_key] = time.time() + delay_seconds
    _write_retry_state(root, cooldowns)


def _clear_cooldown(root, retry_key):
    legacy_until, cooldowns = _read_retry_state(root)
    if legacy_until is not None:
        return
    if retry_key in cooldowns:
        cooldowns.pop(retry_key, None)
        _write_retry_state(root, cooldowns)


def run_agent_chain(root, cfg, chain, prompt, log, retry_key=None):
    """Run a task against an ordered fallback chain of specs. Returns 0 on the first
    success, 'cooldown' if skipped (in retry cooldown), None if killed, or the last
    nonzero exit code (cooldown then armed). A nonzero spec falls through to the next;
    the cooldown is armed only once the whole chain has failed."""
    retry_key = retry_key or _chain_retry_key("agent", "default", chain)
    rem = cooldown_remaining(root, retry_key)
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
            _clear_cooldown(root, retry_key)          # success clears its cooldown
            return 0
        last = code
        log(f"agent exit {code} via {tag}")
    delay = int(cfg.get("retry_delay_minutes", 30)) * 60
    _arm_cooldown(root, retry_key, delay)
    log(f"all {len(chain)} agent spec(s) failed -> retry in {delay // 60}m")
    return last


def _bucket(difficulty):
    """Issue difficulty → agent-chain bucket ('easy'/'hard'; missing → hard)."""
    return "easy" if difficulty == "easy" else "hard"


def peek_next(root, skip=None):
    """Read-only: the next ready issue as (iid, type, difficulty), or None. Shells
    `claim_issue.py --peek` (no claim) so the runner can pick the right agent chain
    before spawning."""
    claim = _pm(root).parent / "scripts" / "core" / "claim_issue.py"
    try:
        argv = [sys.executable, str(claim), "--peek", "--pm-dir", str(_pm(root))]
        skip = [s for s in (skip or []) if s]
        if skip:
            argv += ["--skip", ",".join(skip)]
        out = subprocess.run(argv,
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


def notify_changes(root, cfg, remote, before, after, log):
    """Compare status snapshots; comment done/blocked transitions on the mirror. The
    `awaiting_approval` surface is NOT done here — it is per-gate and deterministic (see
    notify_gates), so a 2nd gate on an already-parked issue still gets announced. Returns
    True if any status changed (so the caller re-pushes)."""
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
    return changed


def _gate_comment(r, iid):
    """The 🔔 surface comment for one open approval record."""
    handle = r.get("apr") or f"A{r['n']}"
    return (f"🔔 Needs your approval — `{iid}` {handle}: {r.get('summary','')}\n"
            f"- why: {r.get('why') or r.get('kind','')}\n"
            f"- options: {r.get('options','—')}\n"
            f"- detail: `.specseed/project_management/issues/{iid}/approval.md`\n"
            f"Reply here: `approve {handle}` / `reject {handle} <note>` / `hold {handle}`.")


def notify_gates(root, cfg, remote, log):
    """Per-gate, deterministic HITL surface (replaces the old status-change trigger).

    Each pass: stamp any new APR ids + refresh the index (single writer), then — on the
    mirror — post one 🔔 on each open gate's WORK issue that hasn't been announced yet,
    keyed off the gate's `Surfaced:` stamp (not the issue status). This fixes the
    missed-2nd-gate bug: a 2nd gate opening on an already-`awaiting_approval` issue is
    unsurfaced, so it gets its own 🔔. Idempotent — a stamped gate is never re-announced.

    Local-only (`remote is None`): stamp ids + refresh the index, but DON'T mark gates
    surfaced — so a later mirror opt-in announces the backlog once."""
    pm = _pm(root)
    if not (pm / "issues").is_dir():
        return                                    # no work yet → nothing to surface
    try:
        approvals_render.assign_ids(pm)
        records = approvals_render.collect(pm)
    except Exception as e:
        log(f"approvals scan error: {e}")
        return
    if remote is not None:
        for r in records:
            if r.get("surfaced"):
                continue
            iid = r["issue"]
            n = remote_sync._num(cfg, iid)
            if not n:
                continue
            remote.comment(n, _gate_comment(r, iid))
            approvals_render.stamp_field(pm, iid, r["n"], "Surfaced", rc.now_iso())
    try:
        approvals_render.write_index(pm)          # reflect new ids + surfaced stamps
    except Exception as e:
        log(f"approvals index error: {e}")


def work_step(root, cfg, remote, log):
    """Run the first ready issue whose agent chain is not cooling down. If the next
    ready issue is cooling down, skip only that issue for this pass and ask
    claim_issue.py for the next candidate. Function is `qa` for a type:qa issue, else
    `implement` (merges still ride the implement agent's finish flow). Returns True if
    any issue status changed (so the caller re-pushes the mirror)."""
    pcfg = cfgmod.load_config(root) or {}
    skipped = []
    before_all = _statuses(root)
    attempted = False
    while True:
        peek = peek_next(root, skip=skipped)
        if peek is None:
            if attempted:
                return notify_changes(root, cfg, remote, before_all, _statuses(root), log)
            return False                              # nothing ready outside cooldown
        iid, itype, difficulty = peek
        function = "qa" if itype == "qa" else "implement"
        bucket = _bucket(difficulty)
        chain = cfgmod.agent_chain(pcfg, function, bucket)
        retry_key = _chain_retry_key(function, bucket, chain)
        rem = cooldown_remaining(root, retry_key)
        if rem > 0:
            log(f"{iid}: {function}/{bucket} in retry cooldown "
                f"(~{int(rem // 60)}m left); checking next ready issue")
            skipped.append(iid)
            continue
        attempted = True
        res = run_agent_chain(root, cfg, chain, WORK_PROMPT.format(iid=iid), log,
                              retry_key=retry_key)
        if res == 0:
            return notify_changes(root, cfg, remote, before_all, _statuses(root), log)
        if res == "cooldown":
            skipped.append(iid)
            continue
        if res is None:
            return False                              # killed
        skipped.append(iid)                           # failed; try other chains


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
        bucket = _bucket(issues[target].get("difficulty"))
        chain = cfgmod.agent_chain(pcfg, "review", bucket)
        retry_key = _chain_retry_key("review", bucket, chain)
        res = run_agent_chain(root, cfg, chain, REVIEW_PROMPT.format(iid=target), log,
                              retry_key=retry_key)
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


# --------------------------------------------------------------------------- #
# instruction inbox — free-form per-issue asks/questions (Phase 4). Runs BEFORE
# work_step (answering humans takes precedence over grinding new work) and acts on issues
# in ANY state (incl. blocked/done), unlike claiming. Fresh context, never a resumed
# session: a resumed session reasons against a phantom tree (other agents may have
# committed since) — re-derive from real code + step reports. The agent does the thinking;
# the runner records its reply + advances the cursor (pure inbox.py bookkeeping).
# --------------------------------------------------------------------------- #
def next_inbox(root, skip=None):
    """The next issue whose inbox has unprocessed entries, as (iid, entries), or None.
    Pure disk read (no agent) — issues are scanned in sorted order; `skip` (the issue
    work_step will target this pass) is passed over so one pass never has two writers on
    the same issue. Processes at most one inbox per pass (parity with work/review)."""
    pm = _pm(root)
    issues_dir = pm / "issues"
    if not issues_dir.is_dir():
        return None
    for d in sorted(p for p in issues_dir.iterdir() if p.is_dir()):
        if d.name == skip:
            continue
        pend = inbox.unprocessed(pm, d.name)
        if pend:
            return d.name, pend
    return None


def _inbox_reply_comment(iid, reply):
    """The 📝-prefixed work-issue comment carrying the agent's inbox reply (the bot prefix
    keeps comment-ingest from feeding it back as a new instruction)."""
    return f"📝 Re: your note on `{iid}`\n\n{reply}"


def inbox_step(root, cfg, remote, log):
    """Process ONE issue's unprocessed inbox batch with a fresh-context agent, record the
    reply + advance the cursor, and post the reply on the mirror. Skips the issue
    work_step will claim this pass. Returns True if any issue status changed (the agent
    may re-open/re-close a done issue), so the caller re-pushes the mirror."""
    if next_inbox(root) is None:                          # nothing pending → skip peek cost
        return False
    skip = peek_next(root)                                # don't double-write the work target
    picked = next_inbox(root, skip=skip[0] if skip else None)
    if picked is None:
        return False
    iid, entries = picked
    pm = _pm(root)

    pcfg = cfgmod.load_config(root) or {}
    issues = _load_issues(root)
    bucket = _bucket((issues.get(iid) or {}).get("difficulty"))
    chain = cfgmod.agent_chain(pcfg, "implement", bucket)
    retry_key = _chain_retry_key("inbox", bucket, chain)
    rem = cooldown_remaining(root, retry_key)
    if rem > 0:
        log(f"{iid}: inbox in retry cooldown (~{int(rem // 60)}m left); will retry")
        return False

    cursor = inbox.read_cursor(pm, iid)
    prompt = INBOX_PROMPT.format(iid=iid, cursor=cursor)
    before = _statuses(root)
    seqs = [e["seq"] for e in entries]
    # Capture the agent's reply (JSON output, no session resume — fresh each time).
    for i, spec in enumerate(chain):
        argv, env = build_relay_cmd(spec, RUNNER)        # --output-format json, no --resume
        if i:
            log(f"{iid}: inbox fallback #{i} -> {spec.get('provider')}/{spec.get('model')}")
        code, out = run_relay_agent(root, prompt, log, argv, env)
        if code is None:
            return False                                  # killed — leave unprocessed
        if code == 0:
            _clear_cooldown(root, retry_key)
            reply = parse_reply(out, spec.get("provider")) or \
                "(processed; see the issue's step reports for detail)"
            inbox.append_entry(pm, iid, "agent", reply, re_seqs=seqs)
            inbox.write_cursor(pm, iid, max(seqs))        # advance to the snapshot's max
            if remote is not None:
                n = remote_sync._num(cfg, iid)
                if n:
                    remote.comment(n, _inbox_reply_comment(iid, reply))
            log(f"{iid}: inbox batch ({len(seqs)}) processed -> IN-{max(seqs)}")
            return notify_changes(root, cfg, remote, before, _statuses(root), log)
        log(f"{iid}: inbox exit {code}")
    delay = int(cfg.get("retry_delay_minutes", 30)) * 60
    _arm_cooldown(root, retry_key, delay)
    log(f"{iid}: all inbox spec(s) failed -> retry in {delay // 60}m")
    return False


def _load_issues(root):
    p = _pm(root) / "issues.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _parse_resolve_text(verb, text):
    """Split a CONTROL `approve`/`reject` body into (handle, extra_cli_args). `text`
    is the remainder after the verb: `<APR-NNNN|issue-id> [option-letter | note…]`.
    A lone letter after `approve` is the decided option; anything else is a note."""
    parts = (text or "").split()
    if not parts:
        return None, []
    handle, rest = parts[0], parts[1:]
    if verb == "approve" and len(rest) == 1 and re.fullmatch(r"[A-Za-z]", rest[0]):
        return handle, ["--option", rest[0].upper()]
    if rest:
        return handle, ["--note", " ".join(rest)]
    return handle, []


def resolve_gate(root, verb, text, log):
    """Run the deterministic resolver (no model) for an approve/reject CONTROL verb.
    Returns (ok, message) — message is the comment to post."""
    handle, extra = _parse_resolve_text(verb, text)
    if handle is None:
        return False, (f"⚠️ `{verb}` needs an `APR-NNNN` id (see `approvals`). "
                       f"e.g. `{verb} APR-0001`" + (" <note>" if verb == "reject" else ""))
    pm = _pm(root)
    script = pm.parent / "scripts" / "core" / "approvals_resolve.py"
    argv = [sys.executable, str(script), handle, verb, "--pm-dir", str(pm), *extra]
    try:
        out = subprocess.run(argv, cwd=str(rc.find_root(root)),
                             capture_output=True, text=True)
    except Exception as e:
        return False, f"⚠️ {handle}: resolver error ({e})"
    if out.returncode == 0:
        try:
            info = json.loads(out.stdout.strip().splitlines()[-1])
            apr = info.get("apr", handle)
            warnings = info.get("warnings") or []
            suffix = f" Warnings: {'; '.join(warnings)}" if warnings else ""
            if info.get("flipped"):
                return True, f"✅ Resolved {apr}: {info['issue']} → {info['to_status']}.{suffix}"
            sib = info.get("open_siblings") or []
            return True, (f"✅ Resolved {apr}; {info['issue']} stays parked "
                          f"({len(sib)} gate(s) still open: {', '.join(sib)}).{suffix}")
        except Exception:
            return True, f"✅ Resolved {handle}."
    reason = (out.stderr.strip().splitlines() or ["failed"])[-1].removeprefix("ERROR: ")
    log(f"resolve {verb} {handle} failed: {reason}")
    return False, f"⚠️ {handle}: {reason}"


def _advance_action_cursor(cfg, action):
    ts = action.get("created_at") or ""
    cid = str(action.get("id") or "")
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


def execute_actions(root, cfg, remote, actions, log):
    # adapt/plan-next are meta ROUTES that genuinely need a model — run them on the
    # implement/hard agent chain. approve/reject are DETERMINISTIC: resolve_gate runs
    # a script (no model), addressing the gate by its APR-NNNN id.
    pcfg = cfgmod.load_config(root) or {}
    ctl_chain = cfgmod.agent_chain(pcfg, "implement", "hard")
    ctl_retry_key = _chain_retry_key("control", "hard", ctl_chain)
    # Advance cli_cursor only across a contiguous prefix of SUCCESSFUL actions (oldest
    # first): a failed/cooling command leaves the cursor behind it so the next pass
    # re-reads + retries it (fixes the old advance-before-execute loss).
    advance_ok = True
    for a in sorted(actions, key=lambda x: (x.get("created_at") or "", str(x.get("id") or ""))):
        verb, n = a["verb"], a["reply_to"]
        ok = True
        if verb == "sync":
            remote.comment(n, "🔄 Synced.")          # reconcile already ran this loop
        elif verb == "claim-next":
            if work_step(root, cfg, remote, log):
                remote_sync.sync_push(root, cfg, remote, log=log)
                remote_sync.push_dashboards(root, cfg, remote, log=log)
            remote.comment(n, "▶️ Ran the next ready issue.")
        elif verb in ("approve", "reject", "hold"):
            # Deterministic resolve (no agent). Re-push the mirror after a good flip.
            ok, msg = resolve_gate(root, verb, a.get("text"), log)
            remote.comment(n, msg)
            if ok:
                remote_sync.sync_push(root, cfg, remote, log=log)
                remote_sync.push_dashboards(root, cfg, remote, log=log)
        elif verb in ("adapt", "plan-next"):
            if verb == "adapt" and remote_control.read_ctl(root) == "run":
                remote_control.write_ctl(root, "pause")
                remote.comment(n, "⏸️ Paused claiming before `adapt`; resume when reviewed.")
            prompt = control_prompt(verb, a.get("text"))
            res = run_agent_chain(root, cfg, ctl_chain, prompt, log,
                                  retry_key=ctl_retry_key)
            if res == 0:
                remote.comment(n, f"✅ Ran `{verb}`.")
            elif res == "cooldown":
                ok = False
                remote.comment(n, "⏳ In retry cooldown (a prior run hit a limit). "
                                  "Retry later, or `resume` after it clears.")
            else:
                ok = False
                remote.comment(n, f"⚠️ `{verb}` failed; will retry automatically.")
        # cursor: extend the high-water mark only while every action so far has stuck.
        if advance_ok and ok and a.get("created_at"):
            _advance_action_cursor(cfg, a)
        if not ok:
            advance_ok = False


# --------------------------------------------------------------------------- #
# CR respec mode (spec-change requests).
#
# A second, mutually-exclusive runner mode. When an urgent CR is active the runner
# STOPS claiming work, isolates the respec on a `cr/<CR-ID>` branch off the
# integration branch, relays the conversation through a RESUMABLE coding-agent
# session (one session id per CR, stored on the CR), and on a terminal CR state
# merges (approved) or deletes (rejected) the branch — then resumes claiming. One
# process, one writer: the loop is synchronous, so any in-flight work_step has fully
# returned before cr_step runs (the "finish in-flight at the loop boundary" is free).
#
# Pure decision helpers (select_active_cr / cr_next_action / build_relay_cmd /
# relay_prompt / parse_session_id) are unit-tested; the `claude`/`codex` shell-out and
# the `git` calls are NOT (same policy as the existing untested agent shell-out).
# --------------------------------------------------------------------------- #

# Phase-5 will define these config keys; until then the CR step is OFF by default
# (safe) so an un-migrated config never enters respec mode. Coordinate names with
# Phase 5: config.cr.enabled (bool), config.cr.branch_prefix (str),
# config.runner.agents.respec (a function with easy/hard buckets; both = one chain).
DEFAULT_RESPEC_CHAIN = [{"provider": "claude", "config_dir": None,
                         "model": "opus", "effort": "high"}]

RELAY_PROMPT = (
    "Use the specseed skill to handle spec-change request {cr_id}. The CR record is at "
    ".specseed/change_requests/{cr_id}/cr.md. {body}")

# bootstrap-kind (cold-start) relay: same async conductor conventions, but the skill
# runs bootstrap mode (no prior spec) instead of adapt. No branch — write onto the tree.
BOOTSTRAP_RELAY_PROMPT = (
    "Use the specseed skill in bootstrap mode, driven async as a headless relay (the "
    "change-request conductor conventions in routes/change-request.md). This is a "
    "cold-start spec request: the repo has NO spec yet and the user wants the first one "
    "built. The request record is at .specseed/change_requests/{cr_id}/cr.md. Converse "
    "one comment-turn at a time (clarify, then propose a plan/depth), wait for explicit "
    "approval, then run bootstrap's stages to produce the full .specseed/ tree on the "
    "working branch (do NOT create or switch branches). When the spec + first sprint are "
    "in place, set the request status to respec_complete. {body}")


def cr_enabled(pcfg):
    """Whether the CR/respec mode is turned on (config.cr.enabled; default OFF)."""
    return bool(((pcfg or {}).get("cr") or {}).get("enabled", False))


def cr_branch_prefix(pcfg):
    return ((pcfg or {}).get("cr") or {}).get("branch_prefix") or "cr/"


def cr_branch_name(cr_id, prefix="cr/"):
    return f"{prefix}{cr_id}"


def cr_integration_branch(pcfg):
    return ((pcfg or {}).get("git") or {}).get("integration_branch") or "dev"


def respec_chain(pcfg):
    """Ordered agent chain for the CR conductor (`respec` function). Phase 5 adds
    `respec` to the config matrix; until then (or if absent) fall back to a built-in
    opus/high default so the relay still runs. Uses the 'hard' bucket shape — a CR has
    no easy/hard split."""
    try:
        chain = cfgmod.agent_chain(pcfg, "respec", "hard")
        if chain:
            return chain
    except (KeyError, TypeError):
        pass
    return [dict(s) for s in DEFAULT_RESPEC_CHAIN]


def cr_next_action(cr):
    """Pure state→action map for one spec request. Branch-isolated for `kind:change`
    (the default); branchless for `kind:bootstrap` (a fresh repo has no settled spec to
    protect, so the cold-start conductor writes straight onto the working tree). Returns:
      branch   — change, open, no branch yet → enter respec mode (create cr/<id>)
      relay    — open + turn==agent → run a conversation turn
      wait     — open + turn human/null → waiting on the user, do nothing
      merge    — change, respec_complete → merge the branch into the integration branch
      finalize — bootstrap, respec_complete → land the spec (flip done + remote initialized)
      drop     — change, rejected + branch still present → delete the branch (clean abort)
      none     — done, or rejected with nothing left, or unknown → nothing to do
    """
    status = cr.get("status")
    branch = cr.get("branch")
    if cr.get("kind") == "bootstrap":
        if status == "open":
            return "relay" if cr.get("turn") == "agent" else "wait"
        if status == "respec_complete":
            return "finalize"
        return "none"                         # done / rejected → nothing (no branch)
    if status == "open":
        if not branch:
            return "branch"
        return "relay" if cr.get("turn") == "agent" else "wait"
    if status == "respec_complete":
        return "merge"
    if status == "rejected":
        return "drop" if branch else "none"
    return "none"


def select_active_cr(crs):
    """The one CR to handle this pass: the FIRST (FIFO — list_crs is already sorted by
    created_at then id) whose state needs ANY action. Strictly serial: a later CR is
    never touched while an earlier one is still live (decision #7 / phase-1 FIFO)."""
    for cr in crs:
        if cr_next_action(cr) != "none":
            return cr
    return None


def build_relay_cmd(spec, runner, session_id=None):
    """(argv, env) for one relay turn, dispatched on provider, with session
    capture/resume flags appended to the base agent command.
      claude — append `--output-format json` (capture/confirm the session id) and, when
               a session id is known, `--resume <id>`. (Never `--bare` — the conductor
               needs skill/CLAUDE.md auto-discovery.)
      codex  — `codex exec resume <thread_id> …` when known, else `codex exec …`, always
               with `--json` so thread.started can be read. Prompt is piped on stdin (the
               trailing '-')."""
    if spec.get("provider") == "codex":
        argv = ["codex", "exec"]
        if session_id:
            argv += ["resume", str(session_id)]
        argv += ["--model", str(spec["model"]),
                 "-c", f'model_reasoning_effort="{spec["effort"]}"',
                 "--sandbox", "workspace-write",
                 "--ask-for-approval", "never",
                 "--json", "-"]
        env = {"CODEX_HOME": str(spec["config_dir"])} if spec.get("config_dir") else {}
        return argv, env
    argv, env = build_claude_cmd(spec, runner)
    argv = list(argv) + ["--output-format", "json"]
    if session_id:
        argv += ["--resume", str(session_id)]
    return argv, env


def relay_prompt(cr_id, comment=None, kind="change"):
    """Natural-language prompt that auto-triggers the specseed skill's conductor for one
    relay turn. NOT a `/specseed …` slash command — in `claude -p` mode user-invoked slash
    commands are unavailable, so the relay must describe the task instead. `kind` picks the
    conductor: change → the CR/adapt conductor; bootstrap → the cold-start bootstrap one."""
    if comment:
        body = f"New message from the user:\n{comment}"
    else:
        body = ("Read the request and respond: ask any clarifying questions or draft a "
                "plan. Do NOT regenerate the spec until the user has explicitly approved.")
    template = BOOTSTRAP_RELAY_PROMPT if kind == "bootstrap" else RELAY_PROMPT
    return template.format(cr_id=cr_id, body=body)


def parse_session_id(output, provider="claude"):
    """Extract the session/thread id from an agent's JSON(L) output.
      claude — a JSON result object with `.session_id` (scan from the last JSON line).
      codex  — JSONL; the first `{"type":"thread.started","thread_id":…}` event.
    Returns the id string, or None if not found."""
    if not output:
        return None
    if provider == "codex":
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if isinstance(ev, dict) and ev.get("type") == "thread.started" \
                    and ev.get("thread_id"):
                return str(ev["thread_id"])
        return None
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("session_id"):
            return str(obj["session_id"])
    try:
        obj = json.loads(output)
        if isinstance(obj, dict) and obj.get("session_id"):
            return str(obj["session_id"])
    except Exception:
        pass
    return None


def parse_reply(output, provider="claude"):
    """Extract the agent's final assistant message from its JSON(L) output, to relay back
    as the CR-issue comment.
      claude — the `.result` field of the result object (scan from the last JSON line).
      codex  — the last `agent_message`/`assistant` text event in the JSONL stream.
    Returns the text, or None if nothing usable was emitted."""
    if not output:
        return None
    if provider == "codex":
        text = None
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            item = ev.get("item") if isinstance(ev.get("item"), dict) else ev
            if item.get("type") in ("agent_message", "assistant") and item.get("text"):
                text = str(item["text"])
            elif ev.get("type") in ("agent_message", "assistant") and ev.get("text"):
                text = str(ev["text"])
        return text
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("result"):
            return str(obj["result"])
    try:
        obj = json.loads(output)
        if isinstance(obj, dict) and obj.get("result"):
            return str(obj["result"])
    except Exception:
        pass
    return None


# --- git plumbing for respec isolation (UNTESTED shell-out, like the agent calls). ---
# NOTE (drift vs the Phase-3 plan): the runner has NO pre-existing git helpers — today
# branch-per-issue git is delegated to the impl agent via CLAUDE.md. Decision #7 / Phase-2
# §"branch ownership" require the RUNNER to own the respec branch/merge/abort, so these
# thin git wrappers live here. They assume git automation is on (respec needs isolation).
def _git(root, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(rc.find_root(root)),
                          capture_output=True, text=True, check=check)


def git_create_cr_branch(root, branch, integ, log):
    try:
        _git(root, "checkout", integ)
        _git(root, "checkout", "-B", branch)
        return True
    except Exception as e:
        log(f"git: create {branch} off {integ} failed: {e}")
        return False


def git_merge_cr(root, branch, integ, push, log):
    """Merge cr/<id> into the integration branch. Returns True on a clean merge, False
    on conflict (left for a human — never auto-resolved). Pushes if push=='auto'."""
    try:
        _git(root, "checkout", integ)
        _git(root, "merge", "--no-ff", branch)
    except Exception as e:
        log(f"git: merge {branch} -> {integ} conflict/failed: {e}")
        _git(root, "merge", "--abort", check=False)
        return False
    _git(root, "branch", "-D", branch, check=False)
    if push == "auto":
        _git(root, "push", "origin", integ, check=False)
    return True


def git_drop_cr(root, branch, integ, log):
    try:
        _git(root, "checkout", integ)
        _git(root, "branch", "-D", branch)
    except Exception as e:
        log(f"git: drop {branch} failed: {e}")


def run_relay_agent(root, prompt, log, argv, env=None):
    """Like run_agent but CAPTURES stdout (the agent's JSON result) so the caller can
    parse the session/thread id. Honors the kill flag. Returns (returncode, stdout);
    returncode is None if killed. UNTESTED (shells out to the coding agent)."""
    kf = _kill_flag(root)
    if kf.exists():
        kf.unlink()
    log(f"relay agent: {prompt[:60]}…")
    run_env = None
    if env:
        run_env = dict(os.environ)
        run_env.update(env)
    proc = subprocess.Popen(argv, cwd=str(rc.find_root(root)),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
            log("relay agent terminated (kill/stop)")
            break
        time.sleep(1)
    out = ""
    try:
        out = proc.stdout.read() or ""
    except Exception:
        pass
    with open(rc.find_root(root) / ".specseed" / "memory" / "runner.log",
              "a", encoding="utf-8") as f:
        f.write(out)
    return (None, out) if killed else (proc.returncode, out)


def cr_enter_branch(root, pcfg, cr_id, log):
    """Enter respec mode: create+checkout cr/<id> off the integration branch, record it."""
    branch = cr_branch_name(cr_id, cr_branch_prefix(pcfg))
    integ = cr_integration_branch(pcfg)
    git_create_cr_branch(root, branch, integ, log)
    change_requests.set_branch(root, cr_id, branch)
    change_requests.append_log(root, cr_id,
                               f"respec mode: branch {branch} off {integ}")
    log(f"CR {cr_id}: entered respec mode on {branch}")


def cr_relay(root, cfg, pcfg, cr, remote, log):
    """Run ONE conversation turn through a resumable agent session. Feeds the latest
    human comment (stashed by the remote comment-ingest), captures the session id on the
    first turn and resumes it after, then posts the conductor's reply back onto the CR's
    issue. The conductor (inside the agent) owns status/turn transitions; the runner only
    captures the session id and, as a safety net, flips turn→human if the conductor left
    it on `agent`. `remote` is None in local-only mode (no comment fetch / reply post)."""
    cr_id = cr["id"]
    session_id = cr.get("session_id")
    chain = respec_chain(pcfg)
    retry_key = _chain_retry_key("respec", "hard", chain)
    rem = cooldown_remaining(root, retry_key)
    if rem > 0:
        log(f"CR {cr_id}: relay in cooldown (~{int(rem // 60)}m); will retry")
        return
    pending = change_requests.read_pending_comment(root, cr_id)   # peek; clear on success
    prompt = relay_prompt(cr_id, comment=pending, kind=cr.get("kind", "change"))
    for i, spec in enumerate(chain):
        argv, env = build_relay_cmd(spec, RUNNER, session_id)
        if i:
            log(f"CR {cr_id}: relay fallback #{i} -> {spec.get('provider')}/{spec.get('model')}")
        code, out = run_relay_agent(root, prompt, log, argv, env)
        if code is None:
            return                                    # killed — not a failure
        if code == 0:
            _clear_cooldown(root, retry_key)
            if pending:
                change_requests.clear_pending_comment(root, cr_id)
            sid = parse_session_id(out, spec.get("provider"))
            if sid and sid != session_id:
                change_requests.set_session(root, cr_id, sid)
            if remote is not None:
                reply = parse_reply(out, spec.get("provider"))
                if reply:
                    remote_sync.post_cr_reply(root, cfg, remote, cr_id, reply, log=log)
            fresh = change_requests.load_cr(root, cr_id)   # conductor may have terminalized
            if fresh.get("status") == "open" and fresh.get("turn") == "agent":
                change_requests.set_turn(root, cr_id, "human")
            change_requests.append_log(root, cr_id, "relay turn complete")
            return
        log(f"CR {cr_id}: relay exit {code}")
    delay = int(cfg.get("retry_delay_minutes", 30)) * 60
    _arm_cooldown(root, retry_key, delay)
    log(f"CR {cr_id}: all relay spec(s) failed -> retry in {delay // 60}m")


def cr_merge(root, cfg, pcfg, cr, log):
    """Approved respec: merge cr/<id> into the integration branch → status done (exit
    respec mode). A merge conflict is NOT auto-resolved — flip turn→human and stop."""
    cr_id = cr["id"]
    branch = cr.get("branch") or cr_branch_name(cr_id, cr_branch_prefix(pcfg))
    integ = cr_integration_branch(pcfg)
    push = (pcfg.get("git") or {}).get("push", "user")
    if not git_merge_cr(root, branch, integ, push, log):
        change_requests.set_turn(root, cr_id, "human")
        change_requests.append_log(
            root, cr_id, f"MERGE CONFLICT: {branch} -> {integ} needs a human; respec stuck")
        log(f"CR {cr_id}: merge conflict — left for a human")
        return
    change_requests.set_status(root, cr_id, "done", turn=None)
    change_requests.append_log(root, cr_id, f"merged {branch} -> {integ}; respec live")
    log(f"CR {cr_id}: merged -> done; claiming resumes")


def cr_drop(root, pcfg, cr, log):
    """Rejected respec: delete cr/<id>, clear the branch field (status stays rejected,
    now terminal — select_active_cr will skip it). The integration branch is untouched."""
    cr_id = cr["id"]
    branch = cr.get("branch") or cr_branch_name(cr_id, cr_branch_prefix(pcfg))
    integ = cr_integration_branch(pcfg)
    git_drop_cr(root, branch, integ, log)
    change_requests.set_branch(root, cr_id, None)
    change_requests.append_log(root, cr_id, f"rejected; deleted {branch}; {integ} untouched")
    log(f"CR {cr_id}: rejected — branch deleted, claiming resumes")


def cr_finalize(root, cfg, cr, remote, log):
    """Bootstrap-kind terminal: the conductor already wrote the spec onto the working
    tree (no branch), so there is nothing to merge. Flip the request `done` and, on a
    mirror, flip `initialized` so the scaffolded-but-empty remote counts as live (the
    normal reconcile already mirrors the freshly produced work + dashboards)."""
    cr_id = cr["id"]
    change_requests.set_status(root, cr_id, "done", turn=None)
    if remote is not None and cfg.get("permanent"):
        cfg["initialized"] = True
    change_requests.append_log(root, cr_id, "spec landed (bootstrap); request done")
    log(f"CR {cr_id}: bootstrap finalized -> done")


def handle_cr(root, cfg, pcfg, cr, remote, log):
    """Perform the single action cr_next_action(cr) dictates for the active CR."""
    action = cr_next_action(cr)
    log(f"CR {cr['id']}: {cr.get('kind', 'change')} {cr.get('status')}/{cr.get('turn')} -> {action}")
    if action == "branch":
        cr_enter_branch(root, pcfg, cr["id"], log)
    elif action == "relay":
        cr_relay(root, cfg, pcfg, cr, remote, log)
    elif action == "merge":
        cr_merge(root, cfg, pcfg, cr, log)
    elif action == "finalize":
        cr_finalize(root, cfg, cr, remote, log)
    elif action == "drop":
        cr_drop(root, pcfg, cr, log)
    # wait / none: nothing this pass


def cr_step(root, cfg, remote, log):
    """If CR/respec mode is enabled AND a CR is active, handle it and return True
    (claiming is FROZEN this pass — respec is mutually exclusive with claiming). Returns
    False when CRs are off or none is active (normal claiming proceeds)."""
    pcfg = cfgmod.load_config(root) or {}
    if not cr_enabled(pcfg):
        return False
    try:
        active = select_active_cr(change_requests.list_crs(root))
    except Exception as e:
        log(f"CR list error: {e}")
        return False
    if active is None:
        return False
    try:
        handle_cr(root, cfg, pcfg, active, remote, log)
    except Exception as e:
        log(f"CR {active.get('id')} error: {e}")
    return True


def one_pass(root, cfg, remote, log):
    """One loop iteration. `remote is None` → local-only: the mirror reconcile +
    CONTROL channel are skipped; only the work step (and file-based control) run."""
    state = remote_control.read_ctl(root)
    if state == "stop":
        return cfg, True
    if remote is not None:
        # 1. reconcile mirror (pull new work + CR comments, drift/heal, push, dashboards).
        #    CR comment-ingest runs BEFORE cr_step so a fresh comment flips turn:agent in
        #    time for this pass's relay turn.
        try:
            remote_sync.sync_pull(root, cfg, remote, log=log)
            remote_sync.reconcile_crs(root, cfg, remote, log=log)
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
    #    Order within a running pass: CR respec (freezes claiming) → inbox (answer human
    #    asks first) → work → review. If a spec-change request is active, handle it in
    #    respec mode and FREEZE the rest this pass (respec is mutually exclusive with
    #    claiming — decision #7). The inbox runs before work so responding to humans takes
    #    precedence; review handles at most one in_review issue per loop.
    if remote_control.read_ctl(root) == "run":
        respec = False
        try:
            respec = cr_step(root, cfg, remote, log)
        except Exception as e:
            log(f"CR error: {e}")
        if respec:
            if remote is not None:
                rc.save_state(cfg, root)
            return cfg, False
        # inbox BEFORE work: answering a human ask/question takes precedence over
        # grinding new implementation work (acts on issues in any state).
        try:
            if inbox_step(root, cfg, remote, log) and remote is not None:
                remote_sync.sync_push(root, cfg, remote, log=log)
                remote_sync.push_dashboards(root, cfg, remote, log=log)
        except Exception as e:
            log(f"inbox error: {e}")
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
    # 4. surface HITL gates (runs even when paused — a parked gate still needs a human).
    try:
        notify_gates(root, cfg, remote, log)
    except Exception as e:
        log(f"gate notify error: {e}")
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
    pidp = _pid_path(root)
    pidp.parent.mkdir(parents=True, exist_ok=True)
    pidp.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    try:
        while not _STOP:
            cfg, stop = one_pass(root, cfg, remote, log)
            if stop or args.once:
                break
            for _ in range(interval):                # interruptible sleep
                if _STOP or remote_control.read_ctl(root) == "stop":
                    break
                time.sleep(1)
    finally:
        try:
            if pidp.exists() and pidp.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pidp.unlink()
        except Exception:
            pass
        log("runner down")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
