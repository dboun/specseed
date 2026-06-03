"""Integration: runner policy gates, inbox processing, and retry fallback.

These tests exercise real script chains and runner-adjacent helpers against tiny
local `.specseed/` repos. Agent execution is mocked; no network, no tokens.
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
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(REMOTE))

import agents_runner  # noqa: E402
import config  # noqa: E402
import inbox  # noqa: E402


def _run_core(repo, script, *args):
    return subprocess.run(
        [sys.executable, str(CORE / script), *map(str, args)],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _issue_md(
    issue_id,
    ticket_id,
    *,
    status="todo",
    difficulty="easy",
    claimed_by="null",
    claimed_at="null",
):
    return (
        "---\n"
        f"id: {issue_id}\n"
        f"title: {issue_id}\n"
        f"ticket: {ticket_id}\n"
        "type: feature\n"
        "component: api\n"
        "effort_hours: 0.5\n"
        f"difficulty: {difficulty}\n"
        "depends_on: []\n"
        f"status: {status}\n"
        "review_required: false\n"
        "approval_required: false\n"
        f"claimed_at: {claimed_at}\n"
        f"claimed_by: {claimed_by}\n"
        'artifacts: {"touches": [], "tests": [], "migrations": []}\n'
        "---\n"
        "## Acceptance criteria\n"
        "- Pass.\n"
    )


def _ticket_md(ticket_id, issue_ids):
    return (
        "---\n"
        f"id: {ticket_id}\n"
        f"title: {ticket_id}\n"
        "epic: null\n"
        "type: feature\n"
        "priority: medium\n"
        "status: todo\n"
        "approval_required: false\n"
        "depends_on: []\n"
        'satisfies_reqs: ["SRS-001"]\n'
        f"issues: {json.dumps(issue_ids)}\n"
        "sprint: null\n"
        "---\n"
        "## Story\n"
        "Base story.\n"
    )


def _seed_review_repo(tmp_path):
    repo = tmp_path
    pm = repo / ".specseed" / "project_management"
    spec = repo / ".specseed" / "spec"
    memory = repo / ".specseed" / "memory"

    _write(spec / "reqs.json", json.dumps({"SRS-001": {"text": "Base req"}}) + "\n")
    _write(memory / "config.json", json.dumps(config.default_config(), indent=2) + "\n")
    _write(pm / "ROADMAP.md", "# Roadmap\n\n- PROJ-0001 Review gate\n")
    _write(pm / "tickets" / "PROJ-0001" / "PROJ-0001.md", _ticket_md("PROJ-0001", ["FEAT-0001"]))
    _write(
        pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
        _issue_md(
            "FEAT-0001",
            "PROJ-0001",
            status="in_review",
            difficulty="hard",
            claimed_by="review-target",
            claimed_at="2026-06-01T00:00:00Z",
        ),
    )
    for script in ("issues_assemble.py", "tickets_assemble.py"):
        result = _run_core(repo, script)
        assert result.returncode == 0, f"{script}: {result.stdout}\n{result.stderr}"
    return repo


def _seed_runner_repo(tmp_path):
    repo = tmp_path
    pm = repo / ".specseed" / "project_management"
    memory = repo / ".specseed" / "memory"
    (pm / "issues" / "I-hard").mkdir(parents=True)
    (pm / "issues" / "I-easy").mkdir(parents=True)
    memory.mkdir(parents=True)
    _write(
        pm / "issues.json",
        json.dumps(
            {
                "I-hard": {"status": "todo", "type": "feature", "difficulty": "hard"},
                "I-easy": {"status": "todo", "type": "feature", "difficulty": "easy"},
            },
            indent=2,
        )
        + "\n",
    )
    return repo


def _snapshot(root):
    data = {}
    for path in sorted((root / ".specseed").rglob("*")):
        if path.is_file():
            data[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return data


def test_review_gate_surfaces_human_approval_then_resolves_to_done(tmp_path):
    repo = _seed_review_repo(tmp_path)
    pm = repo / ".specseed" / "project_management"
    issue_dir = pm / "issues" / "FEAT-0001"
    _write(
        issue_dir / "review.json",
        json.dumps({"verdict": "pass", "confidence": 99, "findings": []}) + "\n",
    )

    gate = _run_core(repo, "review_gate.py", "FEAT-0001", "--apply", "--pm-dir", pm)

    assert gate.returncode == 0, gate.stderr
    gate_out = json.loads(gate.stdout)
    assert gate_out["decision"] == "needs_human"
    assert gate_out["to_status"] == "awaiting_approval"
    assert _read_json(pm / "issues.json")["FEAT-0001"]["status"] == "awaiting_approval"

    rendered = _run_core(repo, "approvals_render.py", "--pm-dir", pm)
    assert rendered.returncode == 0, rendered.stderr
    approvals_md = (pm / "APPROVALS.md").read_text(encoding="utf-8")
    approvals_json = _read_json(pm / "approvals.json")
    assert "FEAT-0001" in approvals_md
    assert approvals_json[0]["issue"] == "FEAT-0001"
    assert approvals_json[0]["apr"] == "APR-0001"
    assert approvals_json[0]["kind"] == "entity-approval"

    resolved = _run_core(repo, "approvals_resolve.py", "APR-0001", "approve", "--pm-dir", pm)

    assert resolved.returncode == 0, resolved.stderr
    resolved_out = json.loads(resolved.stdout)
    assert resolved_out["to_status"] == "done"
    issue = _read_json(pm / "issues.json")["FEAT-0001"]
    assert issue["status"] == "done"
    assert issue["claimed_by"] is None
    assert _read_json(pm / "approvals.json") == []


def test_inbox_step_records_reply_and_leaves_settled_docs_unchanged(monkeypatch, tmp_path):
    repo = _seed_runner_repo(tmp_path)
    pm = repo / ".specseed" / "project_management"
    spec_doc = repo / ".specseed" / "spec" / "SRS.md"
    work_doc = pm / "tickets" / "PROJ-0001" / "PROJ-0001.md"
    _write(spec_doc, "# SRS\n\nSettled requirement.\n")
    _write(work_doc, "# Ticket\n\nSettled story.\n")
    inbox.append_entry(pm, "I-hard", "alice", "Why did the implementation choose this path?")
    before = _snapshot(repo)

    pcfg = config.default_config()
    monkeypatch.setattr(agents_runner.cfgmod, "load_config", lambda root: pcfg)
    monkeypatch.setattr(agents_runner, "RUNNER", pcfg["runner"])
    monkeypatch.setattr(agents_runner, "peek_next", lambda root: None)
    monkeypatch.setattr(agents_runner, "cooldown_remaining", lambda root, retry_key=None: 0)
    monkeypatch.setattr(agents_runner, "build_relay_cmd", lambda spec, runner, session_id=None: (["mock-agent"], {}))
    monkeypatch.setattr(
        agents_runner,
        "run_relay_agent",
        lambda root, prompt, log, argv, env=None: (
            0,
            json.dumps({"result": "Answered the question; no scope or spec change needed."}),
        ),
    )

    changed = agents_runner.inbox_step(repo, pcfg["runner"], None, lambda msg: None)

    assert changed is False
    assert inbox.read_cursor(pm, "I-hard") == 1
    entries = inbox.read_entries(pm, "I-hard")
    assert [entry["seq"] for entry in entries] == [1, 2]
    assert entries[1]["is_agent"] is True
    assert entries[1]["re"] == [1]
    assert "Answered the question" in entries[1]["body"]

    after = _snapshot(repo)
    changed_paths = {path for path, text in after.items() if before.get(path) != text}
    changed_paths |= {path for path in before if path not in after}
    changed_paths |= {path for path in after if path not in before}
    assert changed_paths == {
        ".specseed/project_management/issues/I-hard/inbox.md",
        ".specseed/project_management/issues/I-hard/inbox.state",
    }
    assert spec_doc.read_text(encoding="utf-8") == before[".specseed/spec/SRS.md"]
    assert work_doc.read_text(encoding="utf-8") == before[
        ".specseed/project_management/tickets/PROJ-0001/PROJ-0001.md"
    ]


def test_work_step_arms_failed_chain_cooldown_and_processes_next_candidate(monkeypatch, tmp_path):
    repo = _seed_runner_repo(tmp_path)
    pm = repo / ".specseed" / "project_management"
    pcfg = config.default_config()
    pcfg["runner"]["agents"]["implement"]["hard"] = [
        {"provider": "claude", "config_dir": None, "model": "hard-primary", "effort": "high"},
        {"provider": "claude", "config_dir": None, "model": "hard-fallback", "effort": "high"},
    ]
    pcfg["runner"]["agents"]["implement"]["easy"] = [
        {"provider": "claude", "config_dir": None, "model": "easy-success", "effort": "medium"},
    ]
    hard_chain = config.agent_chain(pcfg, "implement", "hard")
    easy_chain = config.agent_chain(pcfg, "implement", "easy")
    hard_key = agents_runner._chain_retry_key("implement", "hard", hard_chain)
    easy_key = agents_runner._chain_retry_key("implement", "easy", easy_chain)
    peeks = [
        ("I-hard", "feature", "hard"),
        ("I-easy", "feature", "easy"),
    ]
    seen_models = []
    seen_skips = []

    def fake_peek(root, skip=None):
        seen_skips.append(list(skip or []))
        return peeks.pop(0) if peeks else None

    def fake_run_agent(root, prompt, log, argv, env=None):
        model = argv[argv.index("--model") + 1]
        seen_models.append(model)
        if model == "easy-success":
            issues = _read_json(pm / "issues.json")
            issues["I-easy"]["status"] = "in_review"
            (pm / "issues.json").write_text(json.dumps(issues, indent=2) + "\n", encoding="utf-8")
            return 0
        return 1

    monkeypatch.setattr(agents_runner.cfgmod, "load_config", lambda root: pcfg)
    monkeypatch.setattr(agents_runner, "RUNNER", pcfg["runner"])
    monkeypatch.setattr(agents_runner, "peek_next", fake_peek)
    monkeypatch.setattr(agents_runner, "run_agent", fake_run_agent)

    changed = agents_runner.work_step(repo, pcfg["runner"], None, lambda msg: None)

    assert changed is True
    assert seen_skips == [[], ["I-hard"]]
    assert seen_models == ["hard-primary", "hard-fallback", "easy-success"]
    assert agents_runner.cooldown_remaining(repo, hard_key) > 0
    assert agents_runner.cooldown_remaining(repo, easy_key) == 0
    assert _read_json(pm / "issues.json")["I-easy"]["status"] == "in_review"
