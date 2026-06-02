"""Integration coverage for change-request intake and relay boundaries.

No live remotes, no agents. Remote and relay calls are faked at process edges.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "skills" / "specseed" / "scripts"
CORE = SCRIPTS / "core"
REMOTE = SCRIPTS / "remote"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REMOTE))
sys.path.insert(0, str(CORE))

import agents_runner  # noqa: E402
import change_requests as crmod  # noqa: E402
import config as cfgmod  # noqa: E402
import remote_sync as rs  # noqa: E402


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def bare_root(tmp_path):
    (tmp_path / ".specseed").mkdir()
    return tmp_path


def run_script(root, script, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )


class FakeRemote:
    def __init__(self, issues=None, comments=None, user=None):
        self.issues = {i["number"]: dict(i) for i in (issues or [])}
        self.comments = list(comments or [])
        self.user = user or {"login": "owner"}
        self.posted = []
        self.labels = []
        self.closed = []
        self.reopened = []

    def list_open_issues(self):
        return [i for i in self.issues.values() if i.get("state", "open") == "open"]

    def get_issue(self, number):
        return self.issues.get(number)

    def comments_since(self, since):
        return list(self.comments)

    def comment(self, number, body):
        self.posted.append((number, body))

    def set_labels(self, number, labels):
        self.labels.append((number, list(labels)))
        self.issues[number]["labels"] = list(labels)

    def close_issue(self, number, planned=True):
        self.closed.append((number, planned))
        self.issues[number]["state"] = "closed"

    def reopen_issue(self, number):
        self.reopened.append(number)
        self.issues[number]["state"] = "open"

    def whoami(self):
        return self.user


def comment(issue_number, body, author="owner", ts="2026-06-02T10:00:00Z", cid=1):
    return {
        "issue_number": issue_number,
        "body": body,
        "author": author,
        "created_at": ts,
        "id": cid,
    }


def cr_enabled_config():
    cfg = cfgmod.default_config()
    cfg["cr"]["enabled"] = True
    cfg["backend"]["enabled"] = True
    cfg["backend"]["provider"] = "github"
    return cfg


def seed_runner_root(tmp_path):
    root = bare_root(tmp_path)
    write(root / ".specseed" / "memory" / "config.json", json.dumps(cr_enabled_config()))
    return root


def test_local_cr_cli_creates_fifo_resumable_entities(tmp_path):
    root = bare_root(tmp_path)
    body = "## Goal\nSwitch auth.\n\n- keep SSO\n- remove passwords"

    first = run_script(root, "add_change_request.py", "--title", "Switch auth", "--desc", body)
    second = run_script(root, "add_change_request.py", "--title", "Add billing", "--desc", "stripe")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "CR-0001" in first.stdout
    assert "CR-0002" in second.stdout

    one = crmod.load_cr(root, "CR-0001")
    two = crmod.load_cr(root, "CR-0002")
    assert one["status"] == "open"
    assert one["turn"] == "agent"
    assert one["session_id"] is None
    assert one["request"] == body
    assert two["status"] == "open"
    assert two["turn"] == "agent"

    one["created_at"] = "2026-06-02T12:00:00Z"
    two["created_at"] = "2026-06-02T08:00:00Z"
    crmod.save_cr(root, one)
    crmod.save_cr(root, two)

    assert [cr["id"] for cr in crmod.list_crs(root)] == ["CR-0002", "CR-0001"]


def test_remote_change_request_issue_becomes_cr_not_work(tmp_path):
    root = bare_root(tmp_path)
    cfg = {"map": {}, "permanent": {}, "pull_cursor": None}
    remote = FakeRemote(issues=[{
        "number": 77,
        "title": "[feature] Change import scope",
        "body": "Need CSV and TSV import.\n\n## Why\nCustomers asked.",
        "state": "open",
        "labels": ["change-request"],
        "raw": {"created_at": "2026-06-02T09:00:00Z"},
    }])

    ingested = rs.sync_pull(root, cfg, remote, log=lambda _msg: None)

    assert ingested == [(77, "CR-0001")]
    cr_file = root / ".specseed" / "change_requests" / "CR-0001" / "cr.md"
    assert cr_file.exists()
    loaded = crmod.load_cr(root, "CR-0001")
    assert loaded["remote_issue"] == 77
    assert loaded["status"] == "open"
    assert loaded["turn"] == "agent"
    assert "CSV and TSV import" in loaded["request"]
    assert cfg["map"]["CR-0001"]["n"] == 77
    assert rs.is_bot_comment(remote.posted[0][1])

    pm = root / ".specseed" / "project_management"
    assert not (pm / "tickets").exists()
    assert not (pm / "issues").exists()
    assert "PROJ-0001" not in cfg["map"]


def test_relay_captures_session_waits_for_human_then_resumes(tmp_path, monkeypatch):
    root = seed_runner_root(tmp_path)
    cr_id = crmod.create_cr(root, "Change import scope", "Need CSV.", remote_issue=42)
    crmod.set_branch(root, cr_id, "cr/CR-0001")
    remote = FakeRemote(issues=[{
        "number": 42,
        "title": "Change import scope",
        "body": "Need CSV.",
        "state": "open",
        "labels": ["change-request", "cr:open"],
    }])
    cfg = {
        "map": {"CR-0001": {"n": 42, "sig": None}},
        "permanent": {},
        "allowlist": ["owner"],
        "retry_delay_minutes": 30,
    }
    pcfg = cfgmod.load_config(root)
    monkeypatch.setattr(agents_runner, "RUNNER", pcfg["runner"])

    calls = []

    def fake_run_relay_agent(root_arg, prompt, log, argv, env=None):
        calls.append({"prompt": prompt, "argv": list(argv), "env": dict(env or {})})
        if len(calls) == 1:
            return 0, json.dumps({
                "type": "result",
                "session_id": "sess-1",
                "result": "What exact import formats should change?",
            })
        return 0, json.dumps({
            "type": "result",
            "session_id": "sess-1",
            "result": "Got it. I will draft the approved plan.",
        })

    monkeypatch.setattr(agents_runner, "run_relay_agent", fake_run_relay_agent)
    monkeypatch.setattr(agents_runner, "cooldown_remaining", lambda root_arg, retry_key=None: 0)

    assert agents_runner.cr_step(root, cfg, remote, lambda _msg: None) is True
    loaded = crmod.load_cr(root, cr_id)
    assert loaded["session_id"] == "sess-1"
    assert loaded["turn"] == "human"
    assert "--resume" not in calls[0]["argv"]
    assert remote.posted[-1][0] == 42
    assert rs.is_bot_comment(remote.posted[-1][1])
    assert "What exact import formats" in remote.posted[-1][1]

    assert agents_runner.cr_step(root, cfg, remote, lambda _msg: None) is True
    assert len(calls) == 1

    remote.comments.append(comment(
        42,
        "CSV and TSV, but no Excel yet.",
        ts="2026-06-02T11:00:00Z",
        cid=2,
    ))
    touched = rs.sync_cr_comments(root, cfg, remote, log=lambda _msg: None)
    assert touched == [cr_id]
    assert crmod.load_cr(root, cr_id)["turn"] == "agent"

    assert agents_runner.cr_step(root, cfg, remote, lambda _msg: None) is True
    assert len(calls) == 2
    assert calls[1]["argv"][calls[1]["argv"].index("--resume") + 1] == "sess-1"
    assert "CSV and TSV, but no Excel yet." in calls[1]["prompt"]
    assert crmod.read_pending_comment(root, cr_id) is None
    assert crmod.load_cr(root, cr_id)["turn"] == "human"
    assert "Got it. I will draft" in remote.posted[-1][1]
