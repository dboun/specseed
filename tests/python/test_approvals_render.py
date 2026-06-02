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

    md = (repo.pm / "APPROVALS.md").read_text()
    assert "FEAT-0001" in md
    assert "APR-0001" in md                       # the stamped global handle is shown
    records = json.loads((repo.pm / "approvals.json").read_text())
    assert records == [{
        "issue": "FEAT-0001",
        "apr": "APR-0001",
        "n": 1,
        "summary": "Run migration",
        "kind": "gate:data_destructive",
        "opened": "2026-06-01T10:00:00Z",
        "why": "touches production data",
        "options": "approve or reject",
        "handoff": "",
        "surfaced": False,
    }]
    # the CLI stamped the id into the source approval.md (mutating contract)
    assert "- **Id:** APR-0001" in \
        (repo.pm / "issues" / "FEAT-0001" / "approval.md").read_text()


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


TWO_OPEN = """## A1 - First gate
- **Opened:** 2026-06-01T10:00:00Z
- **Kind:** gate:network
- **Status:** open

## A2 - Second gate
- **Opened:** 2026-06-01T10:05:00Z
- **Kind:** gate:deps
- **Status:** open
"""


def test_assign_ids_sequential_across_issues(repo):
    write(repo.pm / "issues" / "FEAT-0001" / "approval.md", TWO_OPEN)
    write(repo.pm / "issues" / "FEAT-0002" / "approval.md",
          "## A1 - Lone gate\n- **Kind:** gate:container\n- **Status:** open\n")

    n = A.assign_ids(repo.pm)
    assert n == 3
    # top-down within a file, sorted issue dirs across files
    f1 = (repo.pm / "issues" / "FEAT-0001" / "approval.md").read_text()
    assert "## A1 - First gate\n- **Id:** APR-0001" in f1
    assert "## A2 - Second gate\n- **Id:** APR-0002" in f1
    f2 = (repo.pm / "issues" / "FEAT-0002" / "approval.md").read_text()
    assert "## A1 - Lone gate\n- **Id:** APR-0003" in f2

    recs = {(r["issue"], r["n"]): r["apr"] for r in A.collect(repo.pm)}
    assert recs == {("FEAT-0001", 1): "APR-0001",
                    ("FEAT-0001", 2): "APR-0002",
                    ("FEAT-0002", 1): "APR-0003"}


def test_assign_ids_idempotent_and_no_gap_fill(repo):
    af = repo.pm / "issues" / "FEAT-0001" / "approval.md"
    write(af, TWO_OPEN)
    assert A.assign_ids(repo.pm) == 2
    before = af.read_text()
    # second run renumbers nothing
    assert A.assign_ids(repo.pm) == 0
    assert af.read_text() == before

    # a freshly-appended entry gets max+1 (APR-0003), not a gap-fill of a lower number
    af.write_text(before + "\n## A3 - Late gate\n- **Status:** open\n", encoding="utf-8")
    assert A.assign_ids(repo.pm) == 1
    txt = af.read_text()
    assert "## A3 - Late gate\n- **Id:** APR-0003" in txt
    assert txt.count("APR-0001") == 1 and txt.count("APR-0002") == 1


def test_assign_ids_replaces_blank_placeholder(repo):
    # the impl agent left the template placeholder — replace it, don't duplicate
    af = repo.pm / "issues" / "FEAT-0001" / "approval.md"
    write(af, "## A1 - Gate\n- **Id:** <leave blank — the runner stamps it>\n"
              "- **Status:** open\n")
    assert A.assign_ids(repo.pm) == 1
    txt = af.read_text()
    assert "- **Id:** APR-0001" in txt
    assert txt.count("**Id:**") == 1             # replaced, not a second line


def test_resolved_entry_keeps_stamped_id(repo):
    af = repo.pm / "issues" / "FEAT-0001" / "approval.md"
    write(af, "## A1 - Gate\n- **Id:** APR-0007\n- **Status:** open\n"
              "\n## Resolved A1 (2026-06-02T00:00:00Z): approved - ok\n")
    # already has a real id AND is resolved → untouched, no renumber
    assert A.assign_ids(repo.pm) == 0
    assert "- **Id:** APR-0007" in af.read_text()
    # and a new open gate counts from the resolved max (APR-0008)
    af.write_text(af.read_text() + "\n## A2 - New\n- **Status:** open\n", encoding="utf-8")
    assert A.assign_ids(repo.pm) == 1
    assert "## A2 - New\n- **Id:** APR-0008" in af.read_text()


def test_surfaced_stamp_and_per_gate_selection(repo):
    # the missed-2nd-gate regression: two open gates on ONE issue, keyed per-gate
    af = repo.pm / "issues" / "FEAT-0001" / "approval.md"
    write(af, TWO_OPEN)
    A.assign_ids(repo.pm)

    recs = A.collect(repo.pm)
    assert all(r["surfaced"] is False for r in recs)
    unsurfaced = [r["n"] for r in recs if not r["surfaced"]]
    assert unsurfaced == [1, 2]                   # both selected for announce

    # announce gate A1 → stamp it; A2 stays selectable
    assert A.stamp_field(repo.pm, "FEAT-0001", 1, "Surfaced", "2026-06-02T00:00:00Z")
    by_n = {r["n"]: r for r in A.collect(repo.pm)}
    assert by_n[1]["surfaced"] is True
    assert by_n[2]["surfaced"] is False
    # idempotent: stamping again is a no-op
    assert A.stamp_field(repo.pm, "FEAT-0001", 1, "Surfaced", "2026-06-02T11:11:11Z") is False
    assert af.read_text().count("**Surfaced:**") == 1


def test_check_does_not_mutate_sources(repo):
    af = repo.pm / "issues" / "FEAT-0001" / "approval.md"
    write(af, TWO_OPEN)
    before = af.read_text()
    repo.core("approvals_render.py", "--check")   # exit 1 (stale) — but read-only
    assert af.read_text() == before               # no ids stamped in --check


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
