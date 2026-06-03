import json

import timeline_render as T


def test_fmt_hours():
    assert T.fmt_hours(1) == "1h"
    assert T.fmt_hours(1.0) == "1h"
    assert T.fmt_hours(1.5) == "1.5h"


def test_critical_path_set_marks_longest_dependency_chain():
    tickets = {
        "PROJ-0001": {"effort_hours": 1, "depends_on": [], "status": "done"},
        "PROJ-0002": {"effort_hours": 2, "depends_on": ["PROJ-0001"]},
        "PROJ-0003": {"effort_hours": 5, "depends_on": ["PROJ-0001"]},
    }
    assert T.critical_path_set(tickets) == {"PROJ-0001", "PROJ-0003"}


def test_render_writes_custom_timeline(repo_with_sprint):
    out = repo_with_sprint.root / "custom_TIMELINE.md"
    res = repo_with_sprint.core("timeline_render.py", "--out", out, "--budget", "10")
    assert res.returncode == 0, res.stderr

    text = out.read_text()
    assert "## SPRINT_2026_W01_A" in text
    assert "Foundations" in text
    assert "0.5h / ~10h" in text
    assert "- PROJ-0001 Base" in text


def test_check_current_and_stale_are_read_only(repo_with_sprint):
    out = repo_with_sprint.root / "TIMELINE.custom.md"
    assert repo_with_sprint.core("timeline_render.py", "--out", out).returncode == 0
    current = out.read_text()

    clean = repo_with_sprint.core("timeline_render.py", "--out", out, "--check")
    assert clean.returncode == 0, clean.stderr
    assert out.read_text() == current

    out.write_text("stale\n")
    stale = repo_with_sprint.core("timeline_render.py", "--out", out, "--check")
    assert stale.returncode == 1
    assert "DRIFT" in stale.stderr
    assert out.read_text() == "stale\n"


def test_render_includes_backlog_ticket(tmp_path):
    tickets = {
        "PROJ-0001": {"title": "Base", "effort_hours": 1, "issues_done": 0, "issues_total": 1},
    }
    text = T.render({}, tickets, 168)
    assert "## Unassigned (backlog)" in text
    assert "PROJ-0001 Base" in text
