"""Integration: manual work insertion and terminal QA flow.

These tests run real specseed Python CLIs against tiny on-disk repos. No agents,
no network, no shared conftest.
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


def _run(script_dir, script, *args, cwd):
    return subprocess.run(
        [sys.executable, str(script_dir / script), *map(str, args)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _core(repo, script, *args):
    return _run(CORE, script, *args, cwd=repo)


def _script(repo, script, *args):
    return _run(SCRIPTS, script, *args, cwd=repo)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_frontmatter(path):
    out = {}
    in_fm = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "---":
            if in_fm:
                break
            in_fm = True
            continue
        if in_fm and ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out


def _issue_md(
    issue_id,
    ticket_id,
    *,
    title=None,
    issue_type="feature",
    status="todo",
    difficulty="easy",
    depends_on=None,
    claimed_at="null",
    claimed_by="null",
):
    depends_on = depends_on or []
    return (
        "---\n"
        f"id: {issue_id}\n"
        f"title: {title or issue_id}\n"
        f"ticket: {ticket_id}\n"
        f"type: {issue_type}\n"
        "component: api\n"
        "effort_hours: 0.5\n"
        f"difficulty: {difficulty}\n"
        f"depends_on: {json.dumps(depends_on)}\n"
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


def _ticket_md(ticket_id, title, issues, *, priority="medium", sprint=None, reqs=None):
    return (
        "---\n"
        f"id: {ticket_id}\n"
        f"title: {title}\n"
        "epic: null\n"
        "type: feature\n"
        f"priority: {priority}\n"
        "status: todo\n"
        "approval_required: false\n"
        "depends_on: []\n"
        f"satisfies_reqs: {json.dumps(reqs or ['SRS-001'])}\n"
        f"issues: {json.dumps(issues)}\n"
        f"sprint: {json.dumps(sprint)}\n"
        "---\n"
        "## Story\n"
        "Base story.\n"
    )


def _sprint_md(sprint_id, tickets):
    return (
        "---\n"
        f"id: {sprint_id}\n"
        "title: Foundations\n"
        "status: in_progress\n"
        f"tickets: {json.dumps(tickets)}\n"
        "starts: 2026-06-01\n"
        "ends: 2026-06-07\n"
        "---\n"
        "## Goal\n"
        "Current sprint.\n"
    )


def _seed_repo(tmp_path, *, with_qa=False):
    repo = tmp_path
    pm = repo / ".specseed" / "project_management"
    spec = repo / ".specseed" / "spec"
    memory = repo / ".specseed" / "memory"
    sprint_id = "SPRINT_2026_W01_A"

    _write(spec / "reqs.json", json.dumps({"SRS-001": {"text": "Base req"}}) + "\n")
    _write(
        memory / "config.json",
        json.dumps({"review": {"enabled": True, "scope": "both"}}) + "\n",
    )
    _write(pm / "ROADMAP.md", "# Roadmap\n\n- PROJ-0001 Base\n")

    if with_qa:
        _write(
            pm / "tickets" / "PROJ-0001" / "PROJ-0001.md",
            _ticket_md("PROJ-0001", "Base", ["FEAT-0001", "QA-0001"], sprint=sprint_id),
        )
        _write(
            pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
            _issue_md(
                "FEAT-0001",
                "PROJ-0001",
                title="Implement base",
            ),
        )
        _write(
            pm / "issues" / "QA-0001" / "QA-0001.md",
            _issue_md("QA-0001", "PROJ-0001", title="QA base", issue_type="qa", depends_on=["FEAT-0001"]),
        )
    else:
        _write(
            pm / "tickets" / "PROJ-0001" / "PROJ-0001.md",
            _ticket_md("PROJ-0001", "Base", ["FEAT-0001"], sprint=sprint_id),
        )
        _write(
            pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
            _issue_md("FEAT-0001", "PROJ-0001", title="Base implementation"),
        )

    _write(pm / "sprints" / sprint_id / f"{sprint_id}.md", _sprint_md(sprint_id, ["PROJ-0001"]))
    _assemble(repo)
    if with_qa:
        issues = _read_json(pm / "issues.json")
        issues["FEAT-0001"]["status"] = "in_review"
        issues["FEAT-0001"]["claimed_at"] = "2026-06-01T00:00:00Z"
        issues["FEAT-0001"]["claimed_by"] = "impl-agent"
        (pm / "issues.json").write_text(json.dumps(issues, indent=2) + "\n", encoding="utf-8")
    return repo


def _assemble(repo):
    for script in ("issues_assemble.py", "tickets_assemble.py", "sprints_assemble.py"):
        result = _core(repo, script)
        assert result.returncode == 0, f"{script}: {result.stdout}\n{result.stderr}"


def _validate(repo):
    for script in ("issues_validate.py", "tickets_validate.py", "sprints_validate.py"):
        result = _core(repo, script)
        assert result.returncode == 0, f"{script}: {result.stdout}\n{result.stderr}"


def _render(repo):
    for script in ("roadmap_render.py", "timeline_render.py"):
        result = _core(repo, script)
        assert result.returncode == 0, f"{script}: {result.stdout}\n{result.stderr}"


def _peek(repo):
    result = _core(repo, "claim_issue.py", "--peek")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_high_priority_manual_bug_jumps_into_active_sprint_and_validates(tmp_path):
    repo = _seed_repo(tmp_path)

    result = _script(
        repo,
        "add_work.py",
        "--title",
        "Urgent bug",
        "--type",
        "bug",
        "--priority",
        "high",
        "--component",
        "api",
        "--effort",
        "0.5",
        "--req",
        "SRS-001",
        "--desc",
        "Fix urgent failure.",
        "--no-render",
    )

    assert result.returncode == 0, result.stderr
    assert "sprint SPRINT_2026_W01_A" in result.stdout

    _assemble(repo)
    _validate(repo)

    pm = repo / ".specseed" / "project_management"
    ticket_fm = _read_frontmatter(pm / "tickets" / "PROJ-0002" / "PROJ-0002.md")
    assert ticket_fm["sprint"] == '"SPRINT_2026_W01_A"'
    assert "PROJ-0002" in _read_json(pm / "sprints.json")["SPRINT_2026_W01_A"]["tickets"]
    assert _peek(repo)["issue_id"] == "BUG-0001"


def test_normal_priority_manual_work_stays_backlog_without_disturbing_current_sprint(tmp_path):
    repo = _seed_repo(tmp_path)
    pm = repo / ".specseed" / "project_management"
    before = _read_json(pm / "sprints.json")["SPRINT_2026_W01_A"]["tickets"]

    result = _script(
        repo,
        "add_work.py",
        "--title",
        "Later chore",
        "--type",
        "chore",
        "--priority",
        "low",
        "--component",
        "api",
        "--effort",
        "1.0",
        "--req",
        "SRS-001",
        "--desc",
        "Clean up later.",
        "--no-render",
    )

    assert result.returncode == 0, result.stderr
    assert "backlog" in result.stdout

    _assemble(repo)
    _validate(repo)

    ticket = _read_json(pm / "tickets.json")["PROJ-0002"]
    assert ticket["sprint"] is None
    assert _read_json(pm / "sprints.json")["SPRINT_2026_W01_A"]["tickets"] == before
    assert _peek(repo)["issue_id"] == "FEAT-0001"


def test_ticket_terminal_qa_issue_files_findings_as_visible_work(tmp_path):
    repo = _seed_repo(tmp_path, with_qa=True)
    pm = repo / ".specseed" / "project_management"

    _write(
        pm / "issues" / "FEAT-0001" / "review.json",
        json.dumps({"verdict": "pass", "confidence": 96}) + "\n",
    )
    review = _core(repo, "review_gate.py", "FEAT-0001", "--apply")
    assert review.returncode == 0, review.stderr
    assert json.loads(review.stdout)["to_status"] == "done"

    assert _peek(repo) == {"peek": True, "issue_id": "QA-0001", "type": "qa", "difficulty": "easy"}

    result = _script(
        repo,
        "add_work.py",
        "--title",
        "QA found regression",
        "--type",
        "bug",
        "--priority",
        "high",
        "--component",
        "api",
        "--effort",
        "0.5",
        "--req",
        "SRS-001",
        "--desc",
        "Regression found during terminal QA.",
    )
    assert result.returncode == 0, result.stderr

    _assemble(repo)
    _validate(repo)
    _render(repo)

    roadmap = (pm / "ROADMAP.md").read_text(encoding="utf-8")
    timeline = (pm / "TIMELINE.md").read_text(encoding="utf-8")
    assert "PROJ-0002 QA found regression" in timeline
    assert "- PROJ-0001 Base (1/2 complete)" in roadmap

    issues = _read_json(pm / "issues.json")
    assert "BUG-0001" in issues
    assert issues["BUG-0001"]["type"] == "bug"
    assert _peek(repo)["issue_id"] == "BUG-0001"
