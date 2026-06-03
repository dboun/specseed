"""agents_runner.py — pure helpers only; never invokes claude."""

import json
import time

import agents_runner
import config


def _specseed_root(tmp_path):
    root = tmp_path
    (root / ".specseed" / "memory").mkdir(parents=True)
    (root / ".specseed" / "project_management").mkdir(parents=True)
    return root


def test_build_claude_cmd_returns_argv_and_env():
    runner = {"allowed_tools": ["Read", "Edit"], "max_turns": 17}
    spec = {"provider": "claude", "config_dir": "/tmp/cc", "model": "sonnet", "effort": "medium"}

    argv, env = agents_runner.build_claude_cmd(spec, runner)

    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "medium"
    assert argv[argv.index("--allowedTools") + 1] == "Read,Edit"
    assert argv[argv.index("--max-turns") + 1] == "17"
    assert env == {"CLAUDE_CONFIG_DIR": "/tmp/cc"}


def test_build_codex_cmd_argv_and_env():
    runner = {"allowed_tools": ["Read"], "max_turns": 9}
    spec = {"provider": "codex", "config_dir": None, "model": "gpt-5.5", "effort": "high"}

    argv, env = agents_runner.build_codex_cmd(spec, runner)

    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--model") + 1] == "gpt-5.5"
    assert 'model_reasoning_effort="high"' in argv
    assert 'approval_policy="never"' in argv        # non-interactive: set via -c, not --ask-for-approval
    assert "--ask-for-approval" not in argv         # not a valid `codex exec` flag
    assert argv[-1] == "-"                         # prompt piped on stdin
    assert "--max-turns" not in argv               # Claude-only knob ignored
    assert env == {}                               # no config_dir → no override


def test_build_agent_cmd_dispatches_on_provider():
    runner = {"allowed_tools": ["Read"], "max_turns": 9}
    cl_argv, _ = agents_runner.build_agent_cmd(
        {"provider": "claude", "config_dir": None, "model": "opus", "effort": "high"}, runner)
    cx_argv, _ = agents_runner.build_agent_cmd(
        {"provider": "codex", "config_dir": None, "model": "gpt-5.5", "effort": "high"}, runner)
    assert cl_argv[0] == "claude"
    assert cx_argv[:2] == ["codex", "exec"]


def test_run_agent_chain_walks_fallbacks(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)
    monkeypatch.setattr(agents_runner, "RUNNER", config.default_config()["runner"])
    monkeypatch.setattr(agents_runner, "cooldown_remaining", lambda r, retry_key=None: 0)

    seen = []

    def fake_run_agent(r, prompt, log, argv, env=None):
        model = argv[argv.index("--model") + 1]
        seen.append(model)
        return 0 if model == "opus" else 1         # first (codex) fails, opus succeeds

    monkeypatch.setattr(agents_runner, "run_agent", fake_run_agent)
    chain = [{"provider": "codex", "config_dir": None, "model": "gpt-5.5", "effort": "high"},
             {"provider": "claude", "config_dir": None, "model": "opus", "effort": "high"}]

    res = agents_runner.run_agent_chain(root, {"retry_delay_minutes": 30}, chain,
                                        "do it", lambda m: None)
    assert res == 0
    assert seen == ["gpt-5.5", "opus"]
    assert agents_runner.cooldown_remaining(root) == 0  # success → no cooldown armed


def test_run_agent_chain_arms_cooldown_when_all_fail(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)
    monkeypatch.setattr(agents_runner, "RUNNER", config.default_config()["runner"])
    monkeypatch.setattr(agents_runner, "run_agent", lambda *a, **k: 1)
    chain = config.default_config()["runner"]["agents"]["implement"]["hard"]

    res = agents_runner.run_agent_chain(root, {"retry_delay_minutes": 30}, chain,
                                        "do it", lambda m: None)
    assert res == 1
    assert agents_runner._retry_path(root).exists()     # cooldown armed


def test_peek_next_parses_claim_issue_output(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)

    class _CP:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(argv, **kw):
        assert "--peek" in argv
        assert "--skip" not in argv
        return _CP('{"peek": true, "issue_id": "I-7", "type": "qa", "difficulty": "hard"}\n')

    monkeypatch.setattr(agents_runner.subprocess, "run", fake_run)
    assert agents_runner.peek_next(root) == ("I-7", "qa", "hard")

    def fake_run_with_skip(argv, **kw):
        assert "--skip" in argv
        assert argv[argv.index("--skip") + 1] == "I-7,I-8"
        return _CP('{"peek": true, "issue_id": "I-9", "type": "feature", "difficulty": "easy"}\n')

    monkeypatch.setattr(agents_runner.subprocess, "run", fake_run_with_skip)
    assert agents_runner.peek_next(root, skip=["I-7", "I-8"]) == ("I-9", "feature", "easy")

    # nothing ready → None
    monkeypatch.setattr(agents_runner.subprocess, "run",
                        lambda argv, **kw: _CP('{"peek": true, "issue_id": null}\n'))
    assert agents_runner.peek_next(root) is None


def test_bucket_maps_difficulty():
    assert agents_runner._bucket("easy") == "easy"
    assert agents_runner._bucket("hard") == "hard"
    assert agents_runner._bucket(None) == "hard"          # missing → conservative


def test_cooldown_remaining_reads_retry_timestamp(tmp_path):
    root = _specseed_root(tmp_path)

    assert agents_runner.cooldown_remaining(root) == 0

    retry_path = agents_runner._retry_path(root)
    retry_path.write_text(str(time.time() + 120), encoding="utf-8")
    assert agents_runner.cooldown_remaining(root) > 0

    retry_path.write_text("not-a-float", encoding="utf-8")
    assert agents_runner.cooldown_remaining(root) == 0


def test_scoped_cooldown_state_is_per_retry_key(tmp_path):
    root = _specseed_root(tmp_path)

    agents_runner._arm_cooldown(root, "implement:hard:aaa", 120)

    assert agents_runner.cooldown_remaining(root, "implement:hard:aaa") > 0
    assert agents_runner.cooldown_remaining(root, "implement:easy:bbb") == 0
    assert agents_runner.cooldown_remaining(root) > 0

    agents_runner._clear_cooldown(root, "implement:hard:aaa")
    assert agents_runner.cooldown_remaining(root, "implement:hard:aaa") == 0
    assert not agents_runner._retry_path(root).exists()


def test_run_agent_chain_clears_only_matching_cooldown(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)
    monkeypatch.setattr(agents_runner, "RUNNER", config.default_config()["runner"])
    monkeypatch.setattr(agents_runner, "run_agent", lambda *a, **k: 0)
    chain = config.default_config()["runner"]["agents"]["implement"]["easy"]
    other_key = "implement:hard:other"
    this_key = agents_runner._chain_retry_key("implement", "easy", chain)
    agents_runner._arm_cooldown(root, other_key, 120)

    res = agents_runner.run_agent_chain(root, {"retry_delay_minutes": 30}, chain,
                                        "do it", lambda m: None, retry_key=this_key)

    assert res == 0
    assert agents_runner.cooldown_remaining(root, this_key) == 0
    assert agents_runner.cooldown_remaining(root, other_key) > 0


def test_work_step_skips_cooled_chain_and_runs_next_candidate(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)
    issues_path = root / ".specseed" / "project_management" / "issues.json"
    issues_path.write_text(json.dumps({
        "I-hard": {"status": "todo"},
        "I-easy": {"status": "todo"},
    }), encoding="utf-8")

    pcfg = config.default_config()
    hard_chain = config.agent_chain(pcfg, "implement", "hard")
    easy_chain = config.agent_chain(pcfg, "implement", "easy")
    hard_key = agents_runner._chain_retry_key("implement", "hard", hard_chain)
    easy_key = agents_runner._chain_retry_key("implement", "easy", easy_chain)

    seen_skips = []

    def fake_peek(r, skip=None):
        seen_skips.append(list(skip or []))
        if not skip:
            return "I-hard", "feature", "hard"
        if skip == ["I-hard"]:
            return "I-easy", "feature", "easy"
        return None

    ran = []

    def fake_run_chain(r, cfg, chain, prompt, log, retry_key=None):
        ran.append((prompt, retry_key))
        return 0

    monkeypatch.setattr(agents_runner.cfgmod, "load_config", lambda r: pcfg)
    monkeypatch.setattr(agents_runner, "peek_next", fake_peek)
    monkeypatch.setattr(
        agents_runner, "cooldown_remaining",
        lambda r, retry_key=None: 60 if retry_key == hard_key else 0,
    )
    monkeypatch.setattr(agents_runner, "run_agent_chain", fake_run_chain)
    monkeypatch.setattr(agents_runner, "notify_changes", lambda *a, **k: True)

    assert agents_runner.work_step(root, {"retry_delay_minutes": 30}, None,
                                   lambda m: None) is True
    assert seen_skips == [[], ["I-hard"]]
    assert ran == [(agents_runner.WORK_PROMPT.format(iid="I-easy"), easy_key)]


def test_work_step_scans_next_candidate_after_chain_failure(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)
    issues_path = root / ".specseed" / "project_management" / "issues.json"
    issues_path.write_text(json.dumps({
        "I-hard": {"status": "todo"},
        "I-easy": {"status": "todo"},
    }), encoding="utf-8")

    pcfg = config.default_config()
    peeks = [
        ("I-hard", "feature", "hard"),
        ("I-easy", "feature", "easy"),
        None,
    ]
    ran = []

    def fake_peek(r, skip=None):
        return peeks.pop(0)

    def fake_run_chain(r, cfg, chain, prompt, log, retry_key=None):
        ran.append(prompt)
        return 1 if "I-hard" in prompt else 0

    monkeypatch.setattr(agents_runner.cfgmod, "load_config", lambda r: pcfg)
    monkeypatch.setattr(agents_runner, "peek_next", fake_peek)
    monkeypatch.setattr(agents_runner, "cooldown_remaining", lambda r, retry_key=None: 0)
    monkeypatch.setattr(agents_runner, "run_agent_chain", fake_run_chain)
    monkeypatch.setattr(agents_runner, "notify_changes", lambda *a, **k: True)

    assert agents_runner.work_step(root, {"retry_delay_minutes": 30}, None,
                                   lambda m: None) is True
    assert ran == [
        agents_runner.WORK_PROMPT.format(iid="I-hard"),
        agents_runner.WORK_PROMPT.format(iid="I-easy"),
    ]


def test_path_helpers_and_status_snapshot(tmp_path):
    root = _specseed_root(tmp_path)
    issues_path = root / ".specseed" / "project_management" / "issues.json"
    issues_path.write_text(
        json.dumps(
            {
                "I-1": {"status": "todo"},
                "I-2": {"status": "in_review"},
            }
        ),
        encoding="utf-8",
    )

    assert agents_runner._kill_flag(root) == root / ".specseed" / "memory" / "runner.kill"
    assert agents_runner._pid_path(root) == root / ".specseed" / "memory" / "runner.pid"
    assert agents_runner._pm(root) == root / ".specseed" / "project_management"
    assert agents_runner._retry_path(root) == root / ".specseed" / "memory" / "runner.retry"
    assert agents_runner._statuses(root) == {"I-1": "todo", "I-2": "in_review"}


# --------------------------------------------------------------------------- #
# CR respec mode — pure decision helpers (no git, no claude shell-out).
# --------------------------------------------------------------------------- #
def _cr(**over):
    base = {"id": "CR-0001", "kind": "change", "status": "open", "turn": "agent",
            "branch": None, "session_id": None, "created_at": "2026-06-02T10:00:00Z"}
    base.update(over)
    return base


def test_cr_next_action_state_table():
    f = agents_runner.cr_next_action
    # open + no branch yet → enter respec mode (create branch)
    assert f(_cr(status="open", branch=None)) == "branch"
    # open + branch + a queued human comment / first turn → relay
    assert f(_cr(status="open", branch="cr/CR-0001", turn="agent")) == "relay"
    # open + branch + waiting on the user (human or null) → wait
    assert f(_cr(status="open", branch="cr/CR-0001", turn="human")) == "wait"
    assert f(_cr(status="open", branch="cr/CR-0001", turn=None)) == "wait"
    # conductor finished regenerating → merge
    assert f(_cr(status="respec_complete", branch="cr/CR-0001")) == "merge"
    # rejected with a branch still present → drop it; without → nothing left
    assert f(_cr(status="rejected", branch="cr/CR-0001")) == "drop"
    assert f(_cr(status="rejected", branch=None)) == "none"
    # terminal / unknown → none
    assert f(_cr(status="done", branch=None)) == "none"


def test_cr_finalize_lands_spec_and_flips_initialized(tmp_path):
    import change_requests as crmod
    root = _specseed_root(tmp_path)
    crmod.create_cr(root, "Build it", "a CLI", kind="bootstrap")
    cr = crmod.load_cr(root, "CR-0001")
    cfg = {"permanent": {"control": 3}}        # a real (scaffolded) mirror
    agents_runner.cr_finalize(root, cfg, cr, remote=object(), log=lambda *a: None)
    assert cfg["initialized"] is True
    landed = crmod.load_cr(root, "CR-0001")
    assert landed["status"] == "done"
    assert landed["turn"] is None


def test_cr_finalize_local_only_does_not_touch_initialized(tmp_path):
    import change_requests as crmod
    root = _specseed_root(tmp_path)
    crmod.create_cr(root, "Build it", "a CLI", kind="bootstrap")
    cr = crmod.load_cr(root, "CR-0001")
    cfg = {}
    agents_runner.cr_finalize(root, cfg, cr, remote=None, log=lambda *a: None)
    assert "initialized" not in cfg          # local-only: no mirror bookkeeping
    assert crmod.load_cr(root, "CR-0001")["status"] == "done"


def test_cr_next_action_bootstrap_kind_is_branchless():
    f = agents_runner.cr_next_action
    # bootstrap NEVER enters a branch (no settled spec to isolate) — straight to relay/wait
    assert f(_cr(kind="bootstrap", status="open", branch=None, turn="agent")) == "relay"
    assert f(_cr(kind="bootstrap", status="open", branch=None, turn="human")) == "wait"
    assert f(_cr(kind="bootstrap", status="open", branch=None, turn=None)) == "wait"
    # conductor wrote the tree → finalize (land it), not merge
    assert f(_cr(kind="bootstrap", status="respec_complete", branch=None)) == "finalize"
    # rejected/done → nothing left (no branch to drop)
    assert f(_cr(kind="bootstrap", status="rejected", branch=None)) == "none"
    assert f(_cr(kind="bootstrap", status="done", branch=None)) == "none"


def test_select_active_cr_is_fifo_and_serial():
    # list_crs yields FIFO order; pick the FIRST needing action.
    crs = [
        _cr(id="CR-0001", status="done", branch=None),            # terminal → skip
        _cr(id="CR-0002", status="open", branch=None),            # first live one
        _cr(id="CR-0003", status="open", branch=None),            # later → not touched yet
    ]
    assert agents_runner.select_active_cr(crs)["id"] == "CR-0002"

    # a rejected CR whose branch still needs deleting IS active.
    crs2 = [_cr(id="CR-0001", status="rejected", branch="cr/CR-0001")]
    assert agents_runner.select_active_cr(crs2)["id"] == "CR-0001"

    # nothing live → None
    assert agents_runner.select_active_cr(
        [_cr(status="done", branch=None), _cr(status="rejected", branch=None)]) is None
    assert agents_runner.select_active_cr([]) is None


def test_relay_prompt_embeds_cr_id_and_comment():
    p0 = agents_runner.relay_prompt("CR-0007")
    assert "CR-0007" in p0
    assert ".specseed/change_requests/CR-0007/cr.md" in p0
    assert "/specseed" not in p0                # natural language, not a slash command
    p1 = agents_runner.relay_prompt("CR-0007", comment="please also bump the timeout")
    assert "CR-0007" in p1
    assert "please also bump the timeout" in p1
    assert "New message from the user" in p1


def test_relay_prompt_bootstrap_kind_runs_bootstrap_mode():
    p = agents_runner.relay_prompt("CR-0003", kind="bootstrap")
    assert "CR-0003" in p
    assert "bootstrap" in p.lower()
    assert "/specseed" not in p                 # natural language, not a slash command
    # the comment still threads through for bootstrap kind
    p2 = agents_runner.relay_prompt("CR-0003", comment="make it a Rust CLI", kind="bootstrap")
    assert "make it a Rust CLI" in p2


def test_control_prompt_is_natural_language_not_slash():
    # CONTROL work-verbs that need a model (adapt/plan-next) must NOT use a
    # `/specseed …` slash form (unavailable in `claude -p` headless mode); they
    # describe the task so the skill auto-triggers. approve/reject no longer come
    # here — they resolve deterministically via approvals_resolve.py.
    for verb in ("adapt", "plan-next"):
        p = agents_runner.control_prompt(verb)
        assert "/specseed" not in p
        assert "specseed skill" in p
    # mode routing + argument carry-through
    assert "adapt mode" in agents_runner.control_prompt("adapt", "add OAuth")
    assert "add OAuth" in agents_runner.control_prompt("adapt", "add OAuth")
    assert "claiming has been paused" in agents_runner.control_prompt("adapt", "add OAuth")
    assert "do not refuse" in agents_runner.control_prompt("adapt", "add OAuth")
    assert "plan-next mode" in agents_runner.control_prompt("plan-next")


def test_parse_resolve_text():
    # bare id → no extra args
    assert agents_runner._parse_resolve_text("approve", "APR-0001") == ("APR-0001", [])
    # approve + lone option letter → --option (uppercased)
    assert agents_runner._parse_resolve_text("approve", "APR-0001 b") == \
        ("APR-0001", ["--option", "B"])
    # reject + note → --note (joined)
    assert agents_runner._parse_resolve_text("reject", "APR-0002 too risky now") == \
        ("APR-0002", ["--note", "too risky now"])
    # approve + multi-word remainder is a note, not an option
    assert agents_runner._parse_resolve_text("approve", "APR-3 looks fine") == \
        ("APR-3", ["--note", "looks fine"])
    # empty → no handle
    assert agents_runner._parse_resolve_text("approve", "") == (None, [])
    assert agents_runner._parse_resolve_text("reject", None) == (None, [])


def test_execute_actions_pauses_before_remote_adapt(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)
    agents_runner.remote_control.write_ctl(root, "run")
    pcfg = config.default_config()
    chain = [{"provider": "claude", "config_dir": None, "model": "opus", "effort": "high"}]
    seen = []

    class Remote:
        def __init__(self):
            self.comments = []

        def comment(self, issue_no, body):
            self.comments.append((issue_no, body))

    remote = Remote()

    def fake_run_agent_chain(root_arg, cfg, chain_arg, prompt, log, retry_key=None):
        seen.append((chain_arg, prompt, retry_key))
        return 0

    monkeypatch.setattr(agents_runner.cfgmod, "load_config", lambda r: pcfg)
    monkeypatch.setattr(agents_runner.cfgmod, "agent_chain", lambda cfg, fn, diff: chain)
    monkeypatch.setattr(agents_runner, "run_agent_chain", fake_run_agent_chain)

    agents_runner.execute_actions(
        root,
        {"retry_delay_minutes": 30},
        remote,
        [{"verb": "adapt", "text": "add OAuth", "reply_to": 7}],
        lambda m: None,
    )

    assert agents_runner.remote_control.read_ctl(root) == "pause"
    assert remote.comments[0] == (7, "⏸️ Paused claiming before `adapt`; resume when reviewed.")
    assert remote.comments[-1] == (7, "✅ Ran `adapt`.")
    assert "add OAuth" in seen[0][1]
    assert "do not refuse" in seen[0][1]


def test_execute_actions_cursor_stops_at_first_failed_resolve(monkeypatch, tmp_path):
    """The advance-before-execute regression: cursor advances across a contiguous
    prefix of successful resolves and STOPS at the first failure so it is retried."""
    root = _specseed_root(tmp_path)
    pcfg = config.default_config()
    chain = [{"provider": "claude", "config_dir": None, "model": "opus", "effort": "high"}]
    monkeypatch.setattr(agents_runner.cfgmod, "load_config", lambda r: pcfg)
    monkeypatch.setattr(agents_runner.cfgmod, "agent_chain", lambda cfg, fn, diff: chain)
    monkeypatch.setattr(agents_runner.remote_sync, "sync_push", lambda *a, **k: None)
    monkeypatch.setattr(agents_runner.remote_sync, "push_dashboards", lambda *a, **k: None)

    # APR-0001 resolves; APR-0002 fails -> cursor must stop at APR-0001's timestamp
    def fake_resolve(root_arg, verb, text, log):
        return ("APR-0001" in (text or ""), "msg")
    monkeypatch.setattr(agents_runner, "resolve_gate", fake_resolve)

    class Remote:
        def __init__(self):
            self.comments = []

        def comment(self, n, body):
            self.comments.append((n, body))

    cfg = {"cli_cursor": "t0"}
    actions = [
        {"verb": "approve", "text": "APR-0002", "reply_to": 8, "created_at": "t3"},
        {"verb": "approve", "text": "APR-0001", "reply_to": 7, "created_at": "t2"},
    ]
    agents_runner.execute_actions(root, cfg, Remote(), actions, lambda m: None)
    # oldest-first: t2 (APR-0001) succeeds -> cursor advances to t2; t3 fails -> stop
    assert cfg["cli_cursor"] == "t2"

    # if the EARLIEST action fails, the cursor never leaves the incoming value
    cfg2 = {"cli_cursor": "t0"}
    agents_runner.execute_actions(
        root, cfg2, Remote(),
        [{"verb": "approve", "text": "APR-0002", "reply_to": 8, "created_at": "t2"}],
        lambda m: None)
    assert cfg2["cli_cursor"] == "t0"


def test_execute_actions_cursor_tracks_same_second_comment_ids(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)
    pcfg = config.default_config()
    chain = [{"provider": "claude", "config_dir": None, "model": "opus", "effort": "high"}]
    monkeypatch.setattr(agents_runner.cfgmod, "load_config", lambda r: pcfg)
    monkeypatch.setattr(agents_runner.cfgmod, "agent_chain", lambda cfg, fn, diff: chain)
    monkeypatch.setattr(agents_runner.remote_sync, "sync_push", lambda *a, **k: None)
    monkeypatch.setattr(agents_runner.remote_sync, "push_dashboards", lambda *a, **k: None)

    def fake_resolve(root_arg, verb, text, log):
        return (text == "APR-0001", "msg")
    monkeypatch.setattr(agents_runner, "resolve_gate", fake_resolve)

    class Remote:
        def comment(self, n, body):
            pass

    cfg = {"cli_cursor": None, "cli_cursor_ids": []}
    ts = "2026-01-01T00:00:00Z"
    agents_runner.execute_actions(root, cfg, Remote(), [
        {"verb": "approve", "text": "APR-0001", "reply_to": 7, "created_at": ts, "id": "10"},
        {"verb": "approve", "text": "APR-0002", "reply_to": 8, "created_at": ts, "id": "11"},
    ], lambda m: None)

    assert cfg["cli_cursor"] == ts
    assert cfg["cli_cursor_ids"] == ["10"]


def test_resolve_gate_surfaces_side_effect_warnings(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)

    class _CP:
        returncode = 0
        stdout = json.dumps({
            "apr": "APR-0001", "issue": "FEAT-0001", "flipped": True,
            "to_status": "done", "warnings": ["tickets_assemble.py failed: boom"],
        }) + "\n"
        stderr = ""

    monkeypatch.setattr(agents_runner.subprocess, "run", lambda *a, **k: _CP())
    ok, msg = agents_runner.resolve_gate(root, "approve", "APR-0001", lambda m: None)

    assert ok is True
    assert "Warnings: tickets_assemble.py failed: boom" in msg


def test_gate_comment_replies_here_not_control():
    msg = agents_runner._gate_comment({
        "apr": "APR-0001", "n": 1, "summary": "Need deploy OK",
        "why": "external publish", "kind": "gate:external_publish", "options": "A) ship",
    }, "FEAT-0001")

    assert "Reply here:" in msg
    assert "CONTROL" not in msg
    assert "hold APR-0001" in msg


def test_build_relay_cmd_claude_resume_and_capture():
    runner = {"allowed_tools": ["Read", "Edit"], "max_turns": 50}
    spec = {"provider": "claude", "config_dir": "/tmp/cc", "model": "opus", "effort": "high"}

    # first turn: no session id → capture only, no --resume
    argv, env = agents_runner.build_relay_cmd(spec, runner, session_id=None)
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--resume" not in argv
    assert "--bare" not in argv
    assert env == {"CLAUDE_CONFIG_DIR": "/tmp/cc"}

    # later turn: session id present → --resume <id>
    argv2, _ = agents_runner.build_relay_cmd(spec, runner, session_id="abc-123")
    assert argv2[argv2.index("--resume") + 1] == "abc-123"
    assert argv2[argv2.index("--output-format") + 1] == "json"


def test_build_relay_cmd_codex_resume_subcommand():
    runner = {"allowed_tools": ["Read"], "max_turns": 9}
    spec = {"provider": "codex", "config_dir": None, "model": "gpt-5.5", "effort": "high"}

    argv, env = agents_runner.build_relay_cmd(spec, runner, session_id=None)
    assert argv[:2] == ["codex", "exec"]
    assert "resume" not in argv
    assert 'approval_policy="never"' in argv
    assert "--ask-for-approval" not in argv
    assert "--json" in argv
    assert argv[-1] == "-"                       # prompt piped on stdin
    assert env == {}

    argv2, _ = agents_runner.build_relay_cmd(spec, runner, session_id="thr-9")
    assert argv2[:4] == ["codex", "exec", "resume", "thr-9"]
    assert "--json" in argv2
    assert argv2[-1] == "-"


def test_parse_session_id_claude_and_codex():
    # claude: a result JSON object with .session_id (scan from the last line)
    claude_out = '{"type":"result","session_id":"uuid-1","result":"ok"}'
    assert agents_runner.parse_session_id(claude_out, "claude") == "uuid-1"
    # extra log noise before the JSON line is tolerated
    noisy = "starting...\n" + claude_out
    assert agents_runner.parse_session_id(noisy, "claude") == "uuid-1"
    # codex: first thread.started event in the JSONL stream
    codex_out = ('{"type":"thread.started","thread_id":"thr-7"}\n'
                 '{"type":"item.completed"}\n')
    assert agents_runner.parse_session_id(codex_out, "codex") == "thr-7"
    # nothing parseable → None
    assert agents_runner.parse_session_id("no json here", "claude") is None
    assert agents_runner.parse_session_id("", "codex") is None


def test_parse_reply_claude_and_codex():
    # claude: the .result field of the result object (last JSON line)
    claude_out = '{"type":"result","session_id":"uuid-1","result":"Here is my answer."}'
    assert agents_runner.parse_reply(claude_out, "claude") == "Here is my answer."
    noisy = "warming up...\n" + claude_out
    assert agents_runner.parse_reply(noisy, "claude") == "Here is my answer."
    # codex: the LAST agent_message text event in the stream
    codex_out = ('{"type":"thread.started","thread_id":"thr-7"}\n'
                 '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}\n'
                 '{"type":"item.completed","item":{"type":"agent_message","text":"final"}}\n')
    assert agents_runner.parse_reply(codex_out, "codex") == "final"
    # nothing usable → None
    assert agents_runner.parse_reply("no json", "claude") is None
    assert agents_runner.parse_reply("", "codex") is None


def test_respec_chain_falls_back_when_config_absent():
    # no config.cr / no runner.agents.respec → built-in opus/high default
    chain = agents_runner.respec_chain({})
    assert chain == agents_runner.DEFAULT_RESPEC_CHAIN
    assert chain[0]["provider"] == "claude"

    # explicit respec function in the matrix is honored
    pcfg = {"runner": {"agents": {"respec": {
        "easy": [{"provider": "codex", "config_dir": None, "model": "x", "effort": "low"}],
        "hard": [{"provider": "claude", "config_dir": None, "model": "opus", "effort": "high"}],
    }}}}
    assert agents_runner.respec_chain(pcfg)[0]["provider"] == "claude"


def test_cr_enabled_and_branch_helpers():
    assert agents_runner.cr_enabled({}) is False
    assert agents_runner.cr_enabled({"cr": {"enabled": False}}) is False
    assert agents_runner.cr_enabled({"cr": {"enabled": True}}) is True
    assert agents_runner.cr_branch_prefix({}) == "cr/"
    assert agents_runner.cr_branch_prefix({"cr": {"branch_prefix": "spec/"}}) == "spec/"
    assert agents_runner.cr_branch_name("CR-0003") == "cr/CR-0003"
    assert agents_runner.cr_branch_name("CR-0003", "spec/") == "spec/CR-0003"
    assert agents_runner.cr_integration_branch({}) == "dev"
    assert agents_runner.cr_integration_branch(
        {"git": {"integration_branch": "develop"}}) == "develop"


# --------------------------------------------------------------------------- #
# instruction inbox (Phase 4) — pure helpers only (no agent shell-out).
# --------------------------------------------------------------------------- #
def test_next_inbox_picks_first_pending_and_skips_target(tmp_path):
    import inbox
    root = _specseed_root(tmp_path)
    pm = root / ".specseed" / "project_management"
    for iid in ("FEAT-0001", "FEAT-0002"):
        (pm / "issues" / iid).mkdir(parents=True)

    # no inboxes yet → nothing to do
    assert agents_runner.next_inbox(root) is None

    inbox.append_entry(pm, "FEAT-0001", "alice", "tweak it")
    inbox.append_entry(pm, "FEAT-0002", "bob", "and this")

    iid, entries = agents_runner.next_inbox(root)
    assert iid == "FEAT-0001" and [e["seq"] for e in entries] == [1]

    # skipping the work_step target this pass moves to the next eligible issue
    iid2, _ = agents_runner.next_inbox(root, skip="FEAT-0001")
    assert iid2 == "FEAT-0002"

    # an inbox whose entries are all processed is not eligible
    inbox.write_cursor(pm, "FEAT-0001", 1)
    assert agents_runner.next_inbox(root)[0] == "FEAT-0002"


def test_inbox_prompt_is_fresh_context_with_boundaries():
    p = agents_runner.INBOX_PROMPT.format(iid="FEAT-0007", cursor=3)
    assert "FEAT-0007" in p
    assert "IN-3" in p                                  # process entries after the cursor
    assert "/specseed" not in p                         # natural language, not a slash
    assert "do not rely on any earlier session" in p    # fresh context, not resume
    # the hard boundaries are spelled out
    assert "add_change_request" in p or "change-request" in p
    assert "add_work" in p
    assert "NEVER edit settled spec docs" in p


def test_inbox_reply_comment_is_bot_prefixed():
    c = agents_runner._inbox_reply_comment("FEAT-0001", "Done — added logging.")
    assert c.startswith("📝")                            # skipped by comment-ingest
    assert "FEAT-0001" in c
    assert "Done — added logging." in c


def test_cr_step_off_by_default(monkeypatch, tmp_path):
    root = _specseed_root(tmp_path)
    # no config at all → CR step inert (returns False, claiming proceeds)
    monkeypatch.setattr(agents_runner.cfgmod, "load_config", lambda r: None)
    assert agents_runner.cr_step(root, {}, None, lambda m: None) is False

    # enabled but no CRs on disk → still False
    monkeypatch.setattr(agents_runner.cfgmod, "load_config",
                        lambda r: {"cr": {"enabled": True}})
    monkeypatch.setattr(agents_runner.change_requests, "list_crs", lambda r: [])
    assert agents_runner.cr_step(root, {}, None, lambda m: None) is False
