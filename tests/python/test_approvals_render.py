import json

import approvals_render as A
from conftest import write


SAMPLE = """## A1 - Run migration
- **Opened:** 2026-06-01T10:00:00Z
- **Kind:** gate:data_destructive
- **Status:** open
- **Why it's gated:** touches production data
- **Options:** approve or reject

## A2 - Old review
- **Opened:** 2026-06-01T11:00:00Z
- **Kind:** entity-approval
- **Status:** open

## Resolved A2 (2026-06-01T12:00:00Z): approved - ok
"""


def test_parse_approval_file_open_resolved_and_multi_entry():
    entries = A._parse_approval_file(SAMPLE)
    assert [e["n"] for e in entries] == [1, 2]
    assert entries[0]["summary"] == "Run migration"
    assert entries[0]["fields"]["kind"] == "gate:data_destructive"
    assert entries[0]["fields"]["status"] == "open"
    assert entries[0]["resolved"] is False
    assert entries[1]["resolved"] is True


def test_collect_only_open_unresolved_entries(repo):
    write(repo.pm / "issues" / "FEAT-0001" / "approval.md", SAMPLE)
    write(repo.pm / "issues" / "FEAT-0002" / "approval.md",
          "## A1 - Closed\n- **Status:** closed\n- **Kind:** gate:network\n")

    records = A.collect(repo.pm)
    assert len(records) == 1
    assert records[0]["issue"] == "FEAT-0001"
    assert records[0]["n"] == 1
    assert records[0]["why"] == "touches production data"


def test_render_md_lists_issue_ids_and_empty_state():
    md = A.render_md([{
        "issue": "FEAT-0001",
        "n": 1,
        "summary": "Need approval",
        "kind": "entity-approval",
        "opened": "2026-06-01T00:00:00Z",
        "why": "manual review",
        "options": "approve or reject",
    }])
    assert "FEAT-0001" in md
    assert "entity-approval" in md
    assert "approval.md" in md

    empty = A.render_md([])
    assert "None" in empty


def test_cli_writes_markdown_and_json(repo):
    write(repo.pm / "issues" / "FEAT-0001" / "approval.md", SAMPLE)
    res = repo.core("approvals_render.py")
    assert res.returncode == 0, res.stderr

    assert "FEAT-0001" in (repo.pm / "APPROVALS.md").read_text()
    records = json.loads((repo.pm / "approvals.json").read_text())
    assert records == [{
        "issue": "FEAT-0001",
        "n": 1,
        "summary": "Run migration",
        "kind": "gate:data_destructive",
        "opened": "2026-06-01T10:00:00Z",
        "why": "touches production data",
        "options": "approve or reject",
        "handoff": "",
    }]


HANDOFF = """## A1 - Download the base model
- **Opened:** 2026-06-02T09:00:00Z
- **Kind:** handoff
- **Status:** open
- **What I need / am about to do:** human must fetch the model weights
- **Handoff:** .specseed/project_management/issues/FEAT-0009/handoff/
- **Verify:** test -f models/base.gguf
"""


def test_collect_and_render_surface_handoff_pointer(repo):
    write(repo.pm / "issues" / "FEAT-0009" / "approval.md", HANDOFF)
    records = A.collect(repo.pm)
    assert len(records) == 1
    assert records[0]["kind"] == "handoff"
    assert records[0]["handoff"] == \
        ".specseed/project_management/issues/FEAT-0009/handoff/"

    md = A.render_md(records)
    assert "**Handoff:**" in md
    assert "issues/FEAT-0009/handoff/" in md


def test_empty_cli_and_check_exit_codes(repo):
    res = repo.core("approvals_render.py")
    assert res.returncode == 0, res.stderr
    assert "None" in (repo.pm / "APPROVALS.md").read_text()
    assert json.loads((repo.pm / "approvals.json").read_text()) == []

    clean = repo.core("approvals_render.py", "--check")
    assert clean.returncode == 0, clean.stderr

    (repo.pm / "APPROVALS.md").write_text("stale\n")
    stale = repo.core("approvals_render.py", "--check")
    assert stale.returncode == 1
    assert "DRIFT" in stale.stderr
    assert (repo.pm / "APPROVALS.md").read_text() == "stale\n"
