"""Integration coverage for the optional remote mirror, using fake remotes only."""

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

sys.path.insert(0, str(REMOTE))
sys.path.insert(0, str(CORE))

import remote_config as rc  # noqa: E402
import remote_control as ctl  # noqa: E402
import remote_sync as rs  # noqa: E402


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_core(root, script):
    return subprocess.run(
        [sys.executable, str(CORE / script)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )


def assert_core_ok(root, script):
    result = run_core(root, script)
    assert result.returncode == 0, (
        f"{script} failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return result


def issue_frontmatter(iid, ticket, status="todo"):
    return (
        "---\n"
        f"id: {iid}\n"
        f"title: {iid} title\n"
        f"ticket: {ticket}\n"
        "type: feature\n"
        "component: api\n"
        "effort_hours: 1\n"
        "depends_on: []\n"
        f"status: {status}\n"
        "claimed_at: null\n"
        "claimed_by: null\n"
        'artifacts: {"touches": [], "tests": [], "migrations": []}\n'
        "---\n"
        "## Acceptance criteria\n"
        "- Works\n"
    )


def seed_work_tree(root):
    pm = root / ".specseed" / "project_management"
    write(root / ".specseed" / "spec" / "reqs.json", json.dumps({
        "SRS-001": {"text": "ship the base flow"},
    }))
    write(pm / "epics" / "EPIC-0001" / "EPIC-0001.md", (
        "---\n"
        "id: EPIC-0001\n"
        "title: Launch\n"
        "status: todo\n"
        'tickets: ["PROJ-0001"]\n'
        "---\n"
        "Epic body.\n"
    ))
    write(pm / "tickets" / "PROJ-0001" / "PROJ-0001.md", (
        "---\n"
        "id: PROJ-0001\n"
        "title: Base ticket\n"
        "epic: EPIC-0001\n"
        "type: feature\n"
        "priority: high\n"
        "status: todo\n"
        "approval_required: false\n"
        "depends_on: []\n"
        'satisfies_reqs: ["SRS-001"]\n'
        'issues: ["FEAT-0001"]\n'
        "sprint: SPRINT_2026_W01_A\n"
        "---\n"
        "Ticket body.\n"
    ))
    write(pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
          issue_frontmatter("FEAT-0001", "PROJ-0001"))
    write(pm / "sprints" / "SPRINT_2026_W01_A" / "SPRINT_2026_W01_A.md", (
        "---\n"
        "id: SPRINT_2026_W01_A\n"
        "title: Foundations\n"
        "status: in_progress\n"
        "starts: 2026-06-01\n"
        "ends: 2026-06-07\n"
        'tickets: ["PROJ-0001"]\n'
        "---\n"
        "Sprint body.\n"
    ))
    assert_core_ok(root, "issues_assemble.py")
    assert_core_ok(root, "tickets_assemble.py")
    assert_core_ok(root, "sprints_assemble.py")
    return root


def seed_remote_config(root, provider="github", allowlist=None):
    write(root / ".specseed" / "memory" / "config.json", json.dumps({
        "backend": {"enabled": True, "provider": provider},
    }))
    cfg = rc.default_state("owner/repo", allowlist or ["owner"])
    rc.save_state(cfg, root)
    loaded, enabled, _ = rc.load_runtime(root)
    assert enabled is True
    assert loaded["provider"] == provider
    return loaded


class FakeRemote:
    def __init__(self, issues=None, comments=None, user=None):
        self.issues = {}
        self.comments = list(comments or [])
        self.user = user or {"login": "owner"}
        self.created = []
        self.updated = []
        self.labels = []
        self.closed = []
        self.reopened = []
        self.posted = []
        self.pinned = []
        self.next_number = 100
        for issue in issues or []:
            self.issues[issue["number"]] = {
                "number": issue["number"],
                "title": issue.get("title", ""),
                "body": issue.get("body", ""),
                "state": issue.get("state", "open"),
                "labels": list(issue.get("labels") or []),
                "assignees": list(issue.get("assignees") or []),
                "raw": dict(issue.get("raw") or {}),
            }
            self.next_number = max(self.next_number, issue["number"] + 1)

    def create_issue(self, title, body=None, labels=None, assignees=None):
        number = self.next_number
        self.next_number += 1
        issue = {
            "number": number,
            "title": title,
            "body": body or "",
            "state": "open",
            "labels": list(labels or []),
            "assignees": list(assignees or []),
            "raw": {},
        }
        self.issues[number] = issue
        self.created.append((number, title, body or "", list(labels or [])))
        return issue

    def get_issue(self, number):
        return self.issues.get(number)

    def update_issue(self, number, title=None, body=None, labels=None, assignees=None):
        issue = self.issues[number]
        if title is not None:
            issue["title"] = title
        if body is not None:
            issue["body"] = body
        if labels is not None:
            issue["labels"] = list(labels)
        if assignees is not None:
            issue["assignees"] = list(assignees)
        self.updated.append((number, title, body, labels, assignees))
        return issue

    def set_labels(self, number, labels):
        self.issues[number]["labels"] = list(labels)
        self.labels.append((number, list(labels)))

    def close_issue(self, number, planned=True):
        self.issues[number]["state"] = "closed"
        self.closed.append((number, planned))

    def reopen_issue(self, number):
        self.issues[number]["state"] = "open"
        self.reopened.append(number)

    def comment(self, number, body):
        self.posted.append((number, body))

    def pin(self, number):
        self.pinned.append(number)

    def list_open_issues(self):
        return [i for i in self.issues.values() if i["state"] == "open"]

    def comments_since(self, since):
        return list(self.comments)

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


def test_local_work_sync_creates_updates_and_closes_fake_remote(tmp_path):
    root = seed_work_tree(tmp_path)
    cfg = seed_remote_config(root)
    remote = FakeRemote()

    cfg = rs.sync_push(root, cfg, remote, log=lambda _msg: None)

    assert set(cfg["map"]) == {"EPIC-0001", "PROJ-0001", "FEAT-0001"}
    assert len(remote.created) == 3
    for mapped in cfg["map"].values():
        assert isinstance(mapped["n"], int)
        assert isinstance(mapped["sig"], str)

    rc.save_state(cfg, root)
    saved = rc.load_state(root)
    assert saved["map"] == cfg["map"]
    assert "provider" not in saved

    issue_no = cfg["map"]["FEAT-0001"]["n"]
    issues_path = root / ".specseed" / "project_management" / "issues.json"
    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    issues["FEAT-0001"]["status"] = "done"
    issues_path.write_text(json.dumps(issues, indent=2) + "\n", encoding="utf-8")

    cfg = rs.sync_push(root, cfg, remote, log=lambda _msg: None)

    assert (issue_no, True) in remote.closed
    assert remote.issues[issue_no]["state"] == "closed"
    assert "status:done" in remote.issues[issue_no]["labels"]
    assert cfg["map"]["FEAT-0001"]["sig"] is not None


def test_remote_new_bug_intake_becomes_local_work_and_validates(tmp_path):
    root = tmp_path
    write(root / ".specseed" / "spec" / "reqs.json", "{}\n")
    cfg = seed_remote_config(root)
    remote = FakeRemote(issues=[{
        "number": 77,
        "title": "Crash when importing CSV",
        "body": "Traceback from the import view.",
        "state": "open",
        "labels": ["bug"],
        "raw": {"created_at": "2026-06-02T09:00:00Z"},
    }])

    ingested = rs.sync_pull(root, cfg, remote, log=lambda _msg: None)

    assert ingested == [(77, "PROJ-0001")]
    assert cfg["map"]["PROJ-0001"]["n"] == 77
    assert (root / ".specseed/project_management/tickets/PROJ-0001/PROJ-0001.md").exists()
    assert (root / ".specseed/project_management/issues/BUG-0001/BUG-0001.md").exists()
    assert "Ingested as **PROJ-0001**" in remote.posted[0][1]

    assert_core_ok(root, "issues_assemble.py")
    assert_core_ok(root, "tickets_assemble.py")
    assert_core_ok(root, "issues_validate.py")
    assert_core_ok(root, "tickets_validate.py")


def test_control_comments_route_only_authorized_commands(tmp_path):
    root = seed_work_tree(tmp_path)
    cfg = seed_remote_config(root, allowlist=["owner", "alice"])
    cfg["permanent"]["control"] = 500
    cfg["map"] = {"FEAT-0001": {"n": 501, "sig": "old"}}
    write(root / ".specseed" / "project_management" / "approvals.json", json.dumps([
        {
            "issue": "FEAT-0001",
            "n": 1,
            "apr": "APR-0001",
            "summary": "Need approval",
            "kind": "network",
        },
    ]))
    remote = FakeRemote(comments=[
        comment(500, "status", author="owner", ts="2026-06-02T10:00:00Z", cid=1),
        comment(500, "pause", author="mallory", ts="2026-06-02T10:01:00Z", cid=2),
        comment(500, "approvals", author="alice", ts="2026-06-02T10:02:00Z", cid=3),
        comment(500, "sync", author="alice", ts="2026-06-02T10:03:00Z", cid=4),
        comment(501, "approve APR-0001", author="alice", ts="2026-06-02T10:04:00Z", cid=5),
        comment(501, "reject APR-0001 nope", author="mallory", ts="2026-06-02T10:05:00Z", cid=6),
    ])

    actions, cfg = ctl.process(root, cfg, remote, log=lambda _msg: None)

    posted_bodies = [body for _, body in remote.posted]
    assert any(body.startswith("**status**") for body in posted_bodies)
    assert any(body.startswith("**approvals**") for body in posted_bodies)
    assert sum("not authorized" in body for body in posted_bodies) == 2
    assert actions == [
        {
            "verb": "sync",
            "text": "",
            "reply_to": 500,
            "created_at": "2026-06-02T10:03:00Z",
            "id": "4",
        },
        {
            "verb": "approve",
            "text": "APR-0001",
            "reply_to": 501,
            "created_at": "2026-06-02T10:04:00Z",
            "id": "5",
        },
    ]
    assert ctl.read_ctl(root) == "run"
    assert cfg["cli_cursor"] == "2026-06-02T10:02:00Z"
    assert cfg["cli_cursor_ids"] == ["3"]
