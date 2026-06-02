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
    assert agents_runner._pm(root) == root / ".specseed" / "project_management"
    assert agents_runner._retry_path(root) == root / ".specseed" / "memory" / "runner.retry"
    assert agents_runner._statuses(root) == {"I-1": "todo", "I-2": "in_review"}


# --------------------------------------------------------------------------- #
# CR respec mode — pure decision helpers (no git, no claude shell-out).
# --------------------------------------------------------------------------- #
def _cr(**over):
    base = {"id": "CR-0001", "status": "open", "turn": "agent", "branch": None,
            "session_id": None, "created_at": "2026-06-02T10:00:00Z"}
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


def test_control_prompt_is_natural_language_not_slash():
    # CONTROL work-verbs must NOT use a `/specseed …` slash form (unavailable in
    # `claude -p` headless mode); they describe the task so the skill auto-triggers.
    for verb in ("adapt", "plan-next", "approve", "reject"):
        p = agents_runner.control_prompt(verb)
        assert "/specseed" not in p
        assert "specseed skill" in p
    # mode routing + argument carry-through
    assert "adapt mode" in agents_runner.control_prompt("adapt", "add OAuth")
    assert "add OAuth" in agents_runner.control_prompt("adapt", "add OAuth")
    assert "plan-next mode" in agents_runner.control_prompt("plan-next")
    # approve/reject both route to approve mode and carry the ID
    ap = agents_runner.control_prompt("approve", "I-12")
    assert "approve mode" in ap and "I-12" in ap and "Approve" in ap
    rj = agents_runner.control_prompt("reject", "I-12 not safe")
    assert "approve mode" in rj and "I-12 not safe" in rj and "Reject" in rj


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
