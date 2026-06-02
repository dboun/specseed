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
    monkeypatch.setattr(agents_runner, "cooldown_remaining", lambda r: 0)

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
        return _CP('{"peek": true, "issue_id": "I-7", "type": "qa", "difficulty": "hard"}\n')

    monkeypatch.setattr(agents_runner.subprocess, "run", fake_run)
    assert agents_runner.peek_next(root) == ("I-7", "qa", "hard")

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
