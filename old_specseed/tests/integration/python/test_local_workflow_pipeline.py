import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE = REPO_ROOT / "skills" / "specseed" / "scripts" / "core"


def repo_paths(root):
    spec = root / ".specseed" / "spec"
    pm = root / ".specseed" / "project_management"
    return spec, pm


def run_core(root, script, *args):
    return subprocess.run(
        [sys.executable, str(CORE / script), *map(str, args)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )


def must_run(root, script, *args):
    result = run_core(root, script, *args)
    assert result.returncode == 0, (
        f"{script} failed with {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return result


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def seed_srs(root, rows):
    spec, _ = repo_paths(root)
    body = "\n".join(
        f"| {rid} | {req} | functional | must | {depends_on or '-'} |"
        for rid, req, depends_on in rows
    )
    write(
        spec / "product_srs.md",
        f"""
        # SRS

        | ID | Requirement | Type | Priority | Depends on |
        |----|-------------|------|----------|------------|
        {body}
        """,
    )
    must_run(root, "requirements_generate_json.py")


def ticket_md(tid, title, reqs, issues, sprint, depends_on=None, status="todo"):
    return f"""
    ---
    id: {tid}
    title: {title}
    epic: null
    type: feature
    priority: medium
    status: {status}
    depends_on: {json.dumps(depends_on or [])}
    satisfies_reqs: {json.dumps(reqs)}
    issues: {json.dumps(issues)}
    sprint: {json.dumps(sprint)}
    ---
    ## Story
    Ship {title}.
    """


def issue_md(iid, title, ticket, tests=None, depends_on=None, status="todo"):
    artifacts = {
        "touches": ["src/example.py"],
        "tests": tests or [],
        "migrations": [],
    }
    return f"""
    ---
    id: {iid}
    title: {title}
    ticket: {ticket}
    type: feature
    component: core
    effort_hours: 1
    depends_on: {json.dumps(depends_on or [])}
    status: {status}
    claimed_at: null
    claimed_by: null
    artifacts: {json.dumps(artifacts)}
    ---
    ## Acceptance criteria
    - Works.
    """


def sprint_md(sid, title, tickets, status="in_progress", starts="2026-06-01"):
    return f"""
    ---
    id: {sid}
    title: {title}
    status: {status}
    starts: {starts}
    ends: 2026-06-07
    tickets: {json.dumps(tickets)}
    ---
    ## Goal
    Finish {title}.
    """


def seed_ticket(root, tid, content):
    _, pm = repo_paths(root)
    write(pm / "tickets" / tid / f"{tid}.md", content)


def seed_issue(root, iid, content):
    _, pm = repo_paths(root)
    write(pm / "issues" / iid / f"{iid}.md", content)


def seed_sprint(root, sid, content):
    _, pm = repo_paths(root)
    write(pm / "sprints" / sid / f"{sid}.md", content)


def seed_roadmap(root, tickets):
    _, pm = repo_paths(root)
    lines = ["# Roadmap", ""]
    lines.extend(f"- {tid} {title}" for tid, title in tickets)
    write(pm / "ROADMAP.md", "\n".join(lines) + "\n")


def assemble_all(root):
    for script in ("issues_assemble.py", "tickets_assemble.py", "sprints_assemble.py"):
        must_run(root, script)


def validate_all(root):
    for script in ("issues_validate.py", "tickets_validate.py", "sprints_validate.py"):
        must_run(root, script)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_happy_path_from_srs_to_ready_issue(tmp_path):
    root = tmp_path
    sid = "SPRINT_2026_W23_A"
    seed_srs(root, [("SRS-CORE-001", "Expose ready local workflow", "")])
    write(root / "tests" / "test_ready.py", "def test_ready():\n    assert True\n")
    seed_ticket(
        root,
        "PROJ-0001",
        ticket_md("PROJ-0001", "Ready workflow", ["SRS-CORE-001"], ["FEAT-0001"], sid),
    )
    seed_issue(
        root,
        "FEAT-0001",
        issue_md("FEAT-0001", "Implement ready workflow", "PROJ-0001",
                 tests=["tests/test_ready.py"]),
    )
    seed_sprint(root, sid, sprint_md(sid, "Foundation", ["PROJ-0001"]))
    seed_roadmap(root, [("PROJ-0001", "Ready workflow")])

    assemble_all(root)
    validate_all(root)
    must_run(root, "roadmap_render.py")
    must_run(root, "timeline_render.py")
    must_run(root, "verification_map.py", "--check")

    _, pm = repo_paths(root)
    assert "(0/1 complete)" in (pm / "ROADMAP.md").read_text(encoding="utf-8")
    assert "PROJ-0001 Ready workflow" in (pm / "TIMELINE.md").read_text(encoding="utf-8")
    peek = must_run(root, "claim_issue.py", "--peek")
    assert json.loads(peek.stdout) == {
        "peek": True,
        "issue_id": "FEAT-0001",
        "type": "feature",
        "difficulty": None,
    }


def test_ticket_dependency_ordering_survives_full_chain(tmp_path):
    root = tmp_path
    sid = "SPRINT_2026_W23_A"
    seed_srs(
        root,
        [
            ("SRS-CORE-001", "Build foundation first", ""),
            ("SRS-CORE-002", "Build dependent feature second", "SRS-CORE-001"),
        ],
    )
    write(root / "tests" / "test_foundation.py", "def test_foundation():\n    assert True\n")
    write(root / "tests" / "test_dependent.py", "def test_dependent():\n    assert True\n")
    seed_ticket(
        root,
        "PROJ-0001",
        ticket_md("PROJ-0001", "Foundation", ["SRS-CORE-001"], ["FEAT-0001"], sid),
    )
    seed_ticket(
        root,
        "PROJ-0002",
        ticket_md(
            "PROJ-0002",
            "Dependent feature",
            ["SRS-CORE-002"],
            ["FEAT-0002"],
            sid,
            depends_on=["PROJ-0001"],
        ),
    )
    seed_issue(
        root,
        "FEAT-0001",
        issue_md("FEAT-0001", "Implement foundation", "PROJ-0001",
                 tests=["tests/test_foundation.py"]),
    )
    seed_issue(
        root,
        "FEAT-0002",
        issue_md("FEAT-0002", "Implement dependent feature", "PROJ-0002",
                 tests=["tests/test_dependent.py"]),
    )
    seed_sprint(root, sid, sprint_md(sid, "Foundation", ["PROJ-0001", "PROJ-0002"]))
    seed_roadmap(root, [("PROJ-0001", "Foundation"), ("PROJ-0002", "Dependent feature")])

    assemble_all(root)
    validate_all(root)
    first_peek = must_run(root, "claim_issue.py", "--peek")
    assert json.loads(first_peek.stdout)["issue_id"] == "FEAT-0001"

    _, pm = repo_paths(root)
    issues = read_json(pm / "issues.json")
    issues["FEAT-0001"]["status"] = "done"
    (pm / "issues.json").write_text(json.dumps(issues, indent=2) + "\n", encoding="utf-8")
    assemble_all(root)
    validate_all(root)

    tickets = read_json(pm / "tickets.json")
    assert tickets["PROJ-0001"]["status"] == "done"
    second_peek = must_run(root, "claim_issue.py", "--peek")
    assert json.loads(second_peek.stdout)["issue_id"] == "FEAT-0002"


def test_verification_map_check_reports_uncovered_requirement(tmp_path):
    root = tmp_path
    sid = "SPRINT_2026_W23_A"
    seed_srs(
        root,
        [
            ("SRS-CORE-001", "Covered workflow", ""),
            ("SRS-CORE-002", "Uncovered workflow", ""),
        ],
    )
    write(root / "tests" / "test_covered.py", "def test_covered():\n    assert True\n")
    seed_ticket(
        root,
        "PROJ-0001",
        ticket_md("PROJ-0001", "Covered", ["SRS-CORE-001"], ["FEAT-0001"], sid),
    )
    seed_issue(
        root,
        "FEAT-0001",
        issue_md("FEAT-0001", "Implement covered", "PROJ-0001",
                 tests=["tests/test_covered.py"]),
    )
    seed_sprint(root, sid, sprint_md(sid, "Foundation", ["PROJ-0001"]))

    assemble_all(root)
    result = run_core(root, "verification_map.py", "--check")

    assert result.returncode == 1
    assert "SRS-CORE-002" in result.stderr
    assert "no satisfying ticket" in result.stderr
