"""tickets_assemble.py — ticket folders plus issue roll-ups to tickets.json."""

import json

from conftest import fm_issue, write


def _ticket_md(tid, **over):
    fields = {
        "id": tid,
        "title": tid,
        "epic": "null",
        "type": "feature",
        "priority": "medium",
        "status": "todo",
        "depends_on": "[]",
        "satisfies_reqs": '["SRS-001"]',
        "issues": "[]",
        "sprint": "null",
    }
    fields.update(over)
    lines = ["---"]
    lines.extend("%s: %s" % (k, v) for k, v in fields.items())
    lines.extend(["---", "## Story", "x", ""])
    return "\n".join(lines)


def test_tickets_assemble_rolls_up_effort_and_issue_counts_from_issues(repo):
    write(repo.pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
          fm_issue("FEAT-0001", "PROJ-0001", effort_hours=1.25, status="done"))
    write(repo.pm / "issues" / "BUG-0001" / "BUG-0001.md",
          fm_issue("BUG-0001", "PROJ-0001", type="bug", effort_hours=2, status="todo"))
    assert repo.core("issues_assemble.py").returncode == 0

    res = repo.core("tickets_assemble.py")
    assert res.returncode == 0, res.stderr

    ticket = json.loads((repo.pm / "tickets.json").read_text())["PROJ-0001"]
    assert ticket["effort_hours"] == 3.25
    assert ticket["issues_total"] == 2
    assert ticket["issues_done"] == 1
    assert ticket["status"] == "todo"


def test_tickets_assemble_preserves_status_by_default_and_no_preserve_resets(repo):
    assert repo.core("issues_assemble.py").returncode == 0
    write(
        repo.pm / "tickets.json",
        json.dumps({"PROJ-0001": {"id": "PROJ-0001", "status": "in_progress"}}),
    )

    res = repo.core("tickets_assemble.py")
    assert res.returncode == 0, res.stderr
    assert json.loads((repo.pm / "tickets.json").read_text())["PROJ-0001"]["status"] == "in_progress"

    res = repo.core("tickets_assemble.py", "--no-preserve")
    assert res.returncode == 0, res.stderr
    assert json.loads((repo.pm / "tickets.json").read_text())["PROJ-0001"]["status"] == "todo"


def test_tickets_assemble_honors_issues_path_override(repo):
    custom_issues = repo.root / "custom-issues.json"
    write(
        custom_issues,
        json.dumps(
            {
                "FEAT-9000": {
                    "id": "FEAT-9000",
                    "ticket": "PROJ-0001",
                    "status": "done",
                    "effort_hours": 4,
                }
            }
        ),
    )

    res = repo.core("tickets_assemble.py", "--issues-path", custom_issues)
    assert res.returncode == 0, res.stderr
    ticket = json.loads((repo.pm / "tickets.json").read_text())["PROJ-0001"]
    assert ticket["effort_hours"] == 4
    assert ticket["issues_total"] == 1
    assert ticket["issues_done"] == 1
    assert ticket["status"] == "done"


def test_tickets_assemble_out_writes_custom_path_only(repo):
    assert repo.core("issues_assemble.py").returncode == 0
    default_path = repo.pm / "tickets.json"
    custom_path = repo.root / "tmp" / "custom-tickets.json"
    custom_path.parent.mkdir()

    res = repo.core("tickets_assemble.py", "--out", custom_path)
    assert res.returncode == 0, res.stderr
    assert custom_path.exists()
    assert not default_path.exists()
    assert "PROJ-0001" in json.loads(custom_path.read_text())


def test_tickets_assemble_ignores_ticket_issues_list_and_uses_child_issue_ticket_field(repo):
    write(
        repo.pm / "tickets" / "PROJ-0001" / "PROJ-0001.md",
        _ticket_md("PROJ-0001", issues='["MISSING-0001", "FEAT-0001"]'),
    )
    write(repo.pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
          fm_issue("FEAT-0001", "PROJ-9999", effort_hours=5, status="done"))
    assert repo.core("issues_assemble.py").returncode == 0

    res = repo.core("tickets_assemble.py")
    assert res.returncode == 0, res.stderr
    ticket = json.loads((repo.pm / "tickets.json").read_text())["PROJ-0001"]
    assert ticket["issues"] == ["MISSING-0001", "FEAT-0001"]
    assert ticket["effort_hours"] == 0
    assert ticket["issues_total"] == 0
    assert ticket["issues_done"] == 0


def test_tickets_assemble_reports_structural_error(repo):
    write(repo.pm / "tickets" / "PROJ-0002" / "PROJ-0002.md", _ticket_md("WRONG-0002"))

    res = repo.core("tickets_assemble.py")
    assert res.returncode == 1
    assert "ERROR:" in res.stderr
    assert "!= folder name" in res.stderr
