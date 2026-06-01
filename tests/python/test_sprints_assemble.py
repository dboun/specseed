"""sprints_assemble.py — sprint folders plus ticket roll-ups to sprints.json."""

import json

from conftest import fm_issue, write


def _ticket_md(tid, sprint="SPRINT_2026_W01_A", **over):
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
        "sprint": sprint if sprint == "null" else json.dumps(sprint),
    }
    fields.update(over)
    lines = ["---"]
    lines.extend("%s: %s" % (k, v) for k, v in fields.items())
    lines.extend(["---", "## Story", "x", ""])
    return "\n".join(lines)


def _sprint_md(sid, **over):
    fields = {
        "id": sid,
        "title": sid,
        "status": "planned",
        "tickets": "[]",
        "starts": "null",
        "ends": "null",
    }
    fields.update(over)
    lines = ["---"]
    lines.extend("%s: %s" % (k, v) for k, v in fields.items())
    lines.extend(["---", "## Goal", "x", ""])
    return "\n".join(lines)


def test_sprints_assemble_rolls_up_effort_and_ticket_counts(repo):
    write(repo.pm / "tickets" / "PROJ-0001" / "PROJ-0001.md",
          _ticket_md("PROJ-0001", issues='["FEAT-0001"]'))
    write(repo.pm / "tickets" / "PROJ-0002" / "PROJ-0002.md",
          _ticket_md("PROJ-0002", issues='["BUG-0001"]'))
    write(repo.pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
          fm_issue("FEAT-0001", "PROJ-0001", effort_hours=1.5, status="done"))
    write(repo.pm / "issues" / "BUG-0001" / "BUG-0001.md",
          fm_issue("BUG-0001", "PROJ-0002", type="bug", effort_hours=2.25, status="todo"))
    write(
        repo.pm / "sprints" / "SPRINT_2026_W01_A" / "SPRINT_2026_W01_A.md",
        _sprint_md("SPRINT_2026_W01_A", tickets='["PROJ-0001", "PROJ-0002"]'),
    )
    repo.assemble()

    sprint = json.loads((repo.pm / "sprints.json").read_text())["SPRINT_2026_W01_A"]
    assert sprint["effort_hours"] == 3.75
    assert sprint["tickets_total"] == 2
    assert sprint["tickets_done"] == 1
    assert sprint["status"] == "planned"


def test_sprints_assemble_orders_by_start_date_then_id(repo):
    write(repo.pm / "sprints" / "SPRINT_2026_W02_A" / "SPRINT_2026_W02_A.md",
          _sprint_md("SPRINT_2026_W02_A", starts="2026-01-08"))
    write(repo.pm / "sprints" / "SPRINT_2026_W01_A" / "SPRINT_2026_W01_A.md",
          _sprint_md("SPRINT_2026_W01_A", starts="2026-01-01"))
    write(repo.pm / "sprints" / "SPRINT_BACKLOG" / "SPRINT_BACKLOG.md",
          _sprint_md("SPRINT_BACKLOG"))

    res = repo.core("sprints_assemble.py")
    assert res.returncode == 0, res.stderr
    sprints = json.loads((repo.pm / "sprints.json").read_text())
    assert list(sprints.keys()) == ["SPRINT_2026_W01_A", "SPRINT_2026_W02_A", "SPRINT_BACKLOG"]
    assert sprints["SPRINT_2026_W01_A"]["order"] == 0
    assert sprints["SPRINT_2026_W02_A"]["order"] == 1
    assert sprints["SPRINT_BACKLOG"]["order"] == 2


def test_sprints_assemble_preserves_status_by_default_and_no_preserve_resets(repo):
    write(repo.pm / "sprints" / "SPRINT_2026_W01_A" / "SPRINT_2026_W01_A.md",
          _sprint_md("SPRINT_2026_W01_A", tickets='["PROJ-0001"]'))
    write(
        repo.pm / "tickets.json",
        json.dumps({"PROJ-0001": {"id": "PROJ-0001", "status": "todo", "effort_hours": 1}}),
    )
    write(
        repo.pm / "sprints.json",
        json.dumps({"SPRINT_2026_W01_A": {"id": "SPRINT_2026_W01_A", "status": "in_progress"}}),
    )

    res = repo.core("sprints_assemble.py")
    assert res.returncode == 0, res.stderr
    assert json.loads((repo.pm / "sprints.json").read_text())["SPRINT_2026_W01_A"]["status"] == "in_progress"

    res = repo.core("sprints_assemble.py", "--no-preserve")
    assert res.returncode == 0, res.stderr
    assert json.loads((repo.pm / "sprints.json").read_text())["SPRINT_2026_W01_A"]["status"] == "planned"


def test_sprints_assemble_honors_tickets_path_and_out_overrides(repo):
    write(
        repo.pm / "sprints" / "SPRINT_2026_W01_A" / "SPRINT_2026_W01_A.md",
        _sprint_md("SPRINT_2026_W01_A", tickets='["PROJ-9000"]'),
    )
    custom_tickets = repo.root / "custom-tickets.json"
    custom_out = repo.root / "tmp" / "custom-sprints.json"
    custom_out.parent.mkdir()
    write(
        custom_tickets,
        json.dumps({"PROJ-9000": {"id": "PROJ-9000", "status": "done", "effort_hours": 6}}),
    )

    res = repo.core("sprints_assemble.py", "--tickets-path", custom_tickets, "--out", custom_out)
    assert res.returncode == 0, res.stderr
    assert custom_out.exists()
    assert not (repo.pm / "sprints.json").exists()
    sprint = json.loads(custom_out.read_text())["SPRINT_2026_W01_A"]
    assert sprint["effort_hours"] == 6
    assert sprint["tickets_total"] == 1
    assert sprint["tickets_done"] == 1
    assert sprint["status"] == "done"


def test_sprints_assemble_missing_sprints_dir_exits_2(repo):
    res = repo.core("sprints_assemble.py")
    assert res.returncode == 2
    assert "not found" in res.stderr


def test_assemble_chain_keeps_issue_ticket_sprint_effort_consistent(repo):
    write(repo.pm / "tickets" / "PROJ-0001" / "PROJ-0001.md",
          _ticket_md("PROJ-0001", issues='["FEAT-0001", "BUG-0001"]'))
    write(repo.pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
          fm_issue("FEAT-0001", "PROJ-0001", effort_hours=1.5, status="done"))
    write(repo.pm / "issues" / "BUG-0001" / "BUG-0001.md",
          fm_issue("BUG-0001", "PROJ-0001", type="bug", effort_hours=2.0, status="todo"))
    write(
        repo.pm / "sprints" / "SPRINT_2026_W01_A" / "SPRINT_2026_W01_A.md",
        _sprint_md("SPRINT_2026_W01_A", tickets='["PROJ-0001"]'),
    )

    repo.assemble()
    issues = json.loads((repo.pm / "issues.json").read_text())
    tickets = json.loads((repo.pm / "tickets.json").read_text())
    sprints = json.loads((repo.pm / "sprints.json").read_text())

    issue_effort = sum(i["effort_hours"] for i in issues.values())
    assert tickets["PROJ-0001"]["effort_hours"] == issue_effort
    assert sprints["SPRINT_2026_W01_A"]["effort_hours"] == issue_effort
    assert sprints["SPRINT_2026_W01_A"]["tickets_total"] == 1
    assert tickets["PROJ-0001"]["issues_total"] == 2
