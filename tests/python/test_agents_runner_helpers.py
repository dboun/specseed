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


def test_build_claude_cmd_returns_arg_list_with_runner_flags():
    runner = dict(config.default_config()["runner"])
    runner.update(
        {
            "model": "opus",
            "effort": "medium",
            "allowed_tools": ["Read", "Edit"],
            "max_turns": 17,
        }
    )

    cmd = agents_runner.build_claude_cmd(runner, model="sonnet")

    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "medium"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Edit"
    assert cmd[cmd.index("--max-turns") + 1] == "17"


def test_cmd_for_role_uses_per_role_model_and_implement_command(monkeypatch):
    cfg = config.default_config()
    cfg["runner"]["model"] = "opus"
    cfg["runner"]["models"] = {"review": "sonnet"}
    implement_cmd = agents_runner.build_claude_cmd(cfg["runner"])

    monkeypatch.setattr(agents_runner, "RUNNER", cfg["runner"])
    monkeypatch.setattr(agents_runner, "CLAUDE_CMD", implement_cmd)

    review_cmd = agents_runner.cmd_for_role(cfg, "review")
    assert review_cmd[review_cmd.index("--model") + 1] == "sonnet"

    assert agents_runner.cmd_for_role(cfg, "implement") is implement_cmd
    assert implement_cmd[implement_cmd.index("--model") + 1] == "opus"


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
