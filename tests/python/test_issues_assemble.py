"""issues_assemble.py — folder source of truth to issues.json."""

import json

from conftest import fm_issue, write


def test_issues_assemble_parses_frontmatter_and_coerces_values(repo):
    write(
        repo.pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
        "---\n"
        "id: FEAT-0001\n"
        "title: Typed issue\n"
        "ticket: PROJ-0001\n"
        "type: feature\n"
        "component: api\n"
        "effort_hours: 2.5\n"
        "depends_on: [\"BUG-0001\"]\n"
        "status: todo\n"
        "claimed_at: null\n"
        "claimed_by: null\n"
        "review_required: true\n"
        "approval_required: false\n"
        "rank: 7\n"
        "ratio: 1.25\n"
        "nullable: null\n"
        "metadata: {\"risk\": \"low\", \"count\": 2}\n"
        "artifacts: {\"touches\": [\"src/api/\"], \"tests\": [\"tests/test_api.py\"], \"migrations\": []}\n"
        "---\n"
        "## Acceptance criteria\n"
        "- x\n",
    )

    res = repo.core("issues_assemble.py")
    assert res.returncode == 0, res.stderr

    issue = repo.issues()["FEAT-0001"]
    assert issue["effort_hours"] == 2.5
    assert issue["depends_on"] == ["BUG-0001"]
    assert issue["claimed_at"] is None
    assert issue["claimed_by"] is None
    assert issue["review_required"] is True
    assert issue["approval_required"] is False
    assert issue["rank"] == 7
    assert issue["ratio"] == 1.25
    assert issue["nullable"] is None
    assert issue["metadata"] == {"risk": "low", "count": 2}
    assert issue["artifacts"]["touches"] == ["src/api/"]


def test_issues_assemble_prefers_named_main_file(repo):
    write(
        repo.pm / "issues" / "FEAT-0001" / "notes.md",
        "---\nid: WRONG-0001\n---\nwrong file\n",
    )
    write(
        repo.pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
        fm_issue("FEAT-0001", "PROJ-0001", title="Named main", effort_hours=1),
    )

    res = repo.core("issues_assemble.py")
    assert res.returncode == 0, res.stderr
    assert repo.issues()["FEAT-0001"]["title"] == "Named main"


def test_issues_assemble_preserves_runtime_state_by_default_and_no_preserve_resets(repo):
    write(
        repo.pm / "issues.json",
        json.dumps(
            {
                "FEAT-0001": {
                    "id": "FEAT-0001",
                    "status": "in_progress",
                    "claimed_at": "2026-06-01T10:00:00Z",
                    "claimed_by": "agent-a",
                }
            }
        ),
    )

    res = repo.core("issues_assemble.py")
    assert res.returncode == 0, res.stderr
    preserved = repo.issues()["FEAT-0001"]
    assert preserved["status"] == "in_progress"
    assert preserved["claimed_at"] == "2026-06-01T10:00:00Z"
    assert preserved["claimed_by"] == "agent-a"

    res = repo.core("issues_assemble.py", "--no-preserve")
    assert res.returncode == 0, res.stderr
    fresh = repo.issues()["FEAT-0001"]
    assert fresh["status"] == "todo"
    assert fresh["claimed_at"] is None
    assert fresh["claimed_by"] is None


def test_issues_assemble_out_writes_custom_path_only(repo):
    default_path = repo.pm / "issues.json"
    custom_path = repo.root / "tmp" / "custom-issues.json"
    custom_path.parent.mkdir()

    res = repo.core("issues_assemble.py", "--out", custom_path)
    assert res.returncode == 0, res.stderr
    assert custom_path.exists()
    assert not default_path.exists()
    assert "FEAT-0001" in json.loads(custom_path.read_text())


def test_issues_assemble_reports_structural_error(repo):
    write(
        repo.pm / "issues" / "FEAT-0002" / "FEAT-0002.md",
        fm_issue("WRONG-0002", "PROJ-0001"),
    )

    res = repo.core("issues_assemble.py")
    assert res.returncode == 1
    assert "ERROR:" in res.stderr
    assert "!= folder name" in res.stderr
