"""add_work.py — manual item scaffolding + sprint placement (no token usage)."""

import json

from conftest import run_scripts


def _read_fm(md_path):
    fm = {}
    for line in md_path.read_text().splitlines():
        if line.strip() == "---":
            continue
        if line.startswith("## "):
            break
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def test_high_priority_lands_in_active_sprint(repo_with_sprint):
    r = repo_with_sprint
    res = run_scripts("add_work.py", "--title", "Urgent bug", "--type", "bug",
                      "--priority", "high", "--component", "api", "--effort", "0.5",
                      "--req", "SRS-001", "--desc", "boom", "--no-render", cwd=r.root)
    assert res.returncode == 0, res.stderr
    assert "sprint SPRINT_2026_W01_A" in res.stdout

    # ticket + issue folders created
    tdir = r.pm / "tickets" / "PROJ-0002"
    idir = r.pm / "issues" / "BUG-0001"
    assert (tdir / "PROJ-0002.md").exists()
    assert (idir / "BUG-0001.md").exists()

    tfm = _read_fm(tdir / "PROJ-0002.md")
    assert tfm["priority"] == "high"
    assert tfm["sprint"] == '"SPRINT_2026_W01_A"'
    assert tfm["created_at"]                       # stamped

    # sprint folder tickets[] kept in sync (back-consistency)
    sprint_md = (r.pm / "sprints" / "SPRINT_2026_W01_A" / "SPRINT_2026_W01_A.md").read_text()
    assert "PROJ-0002" in sprint_md

    # whole tree re-assembles + validates clean
    r.assemble()
    for name, vr in r.validate().items():
        assert vr.returncode == 0, f"{name}: {vr.stdout}\n{vr.stderr}"

    # the new high-priority issue is the next claim
    claim = json.loads(r.core("claim_issue.py").stdout)
    assert claim["claimed"] and claim["issue_id"] == "BUG-0001"


def test_missing_title_non_interactive_exits_clean(repo_with_sprint):
    r = repo_with_sprint
    # no --title, non-tty stdin (subprocess pipe) → clean exit 2, no traceback
    res = run_scripts("add_work.py", "--no-render", cwd=r.root)
    assert res.returncode == 2
    assert "--title required (non-interactive)" in res.stderr
    assert "Traceback" not in res.stderr


def test_omitted_desc_defaults_to_title_non_interactive(repo_with_sprint):
    r = repo_with_sprint
    # --desc omitted → prompt() returns its default (the title) non-interactively
    res = run_scripts("add_work.py", "--title", "Quick fix", "--type", "bug",
                      "--priority", "high", "--component", "api", "--effort", "0.5",
                      "--no-render", cwd=r.root)
    assert res.returncode == 0, res.stderr
    body = (r.pm / "issues" / "BUG-0001" / "BUG-0001.md").read_text()
    assert "Quick fix" in body


def test_normal_priority_goes_to_backlog(repo_with_sprint):
    r = repo_with_sprint
    res = run_scripts("add_work.py", "--title", "Later chore", "--type", "chore",
                      "--priority", "low", "--component", "api", "--effort", "1.0",
                      "--desc", "cleanup", "--no-render", cwd=r.root)
    assert res.returncode == 0, res.stderr
    assert "backlog" in res.stdout
    tfm = _read_fm(r.pm / "tickets" / "PROJ-0002" / "PROJ-0002.md")
    assert tfm["sprint"] == "null"                 # backlog, not the active sprint
    r.assemble()
    for name, vr in r.validate().items():
        assert vr.returncode == 0, f"{name}: {vr.stdout}\n{vr.stderr}"
