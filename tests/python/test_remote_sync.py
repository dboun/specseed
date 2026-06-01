"""remote_sync.py — pure local mirror helpers."""

import json
import sys

from conftest import REPO_ROOT, write, fm_issue

REMOTE = REPO_ROOT / "skills" / "specseed" / "scripts" / "remote"
sys.path.insert(0, str(REMOTE))

import remote_sync as rs


def _pm_root(tmp_path):
    (tmp_path / ".specseed" / "project_management").mkdir(parents=True)
    return tmp_path


def _tiny_work_tree(tmp_path):
    root = _pm_root(tmp_path)
    pm = root / ".specseed" / "project_management"
    write(pm / "epics" / "EPIC-0001" / "EPIC-0001.md",
          "---\nid: EPIC-0001\ntitle: Launch\nstatus: todo\n"
          'tickets: ["PROJ-0001"]\n---\nEpic body.\n')
    write(pm / "tickets" / "PROJ-0001" / "PROJ-0001.md",
          "---\nid: PROJ-0001\ntitle: Base ticket\nepic: EPIC-0001\n"
          "type: feature\npriority: high\nstatus: todo\n"
          'depends_on: []\nsatisfies_reqs: ["SRS-001"]\nissues: ["FEAT-0001"]\n'
          "sprint: SPRINT_2026_W01_A\n---\nTicket body.\n")
    write(pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
          fm_issue("FEAT-0001", "PROJ-0001"))
    write(pm / "issues" / "BUG-0001" / "BUG-0001.md",
          fm_issue("BUG-0001", "PROJ-0001", type="bug", title="Existing bug"))
    write(pm / "tickets.json", json.dumps({
        "PROJ-0001": {
            "id": "PROJ-0001", "title": "Base ticket", "status": "todo",
            "epic": "EPIC-0001", "issues": ["FEAT-0001"], "depends_on": [],
            "sprint": "SPRINT_2026_W01_A",
            "issues_done": 0, "issues_total": 1,
        },
    }))
    write(pm / "issues.json", json.dumps({
        "FEAT-0001": {
            "id": "FEAT-0001", "title": "Feature issue", "status": "in_progress",
            "ticket": "PROJ-0001", "claimed_by": "alice",
        },
        "BUG-0001": {
            "id": "BUG-0001", "title": "Existing bug", "status": "todo",
            "ticket": "PROJ-0001", "claimed_by": None,
        },
    }))
    write(pm / "sprints.json", json.dumps({
        "SPRINT_2026_W01_A": {
            "id": "SPRINT_2026_W01_A", "title": "Foundations",
            "status": "in_progress", "starts": "2026-06-01",
            "ends": "2026-06-07", "tickets": ["PROJ-0001"],
        },
    }))
    return root


def test_split_md_handles_frontmatter_body_and_plain_text():
    fm, body = rs.split_md("---\ntitle: Hi\ncount: 2\nitems: [\"a\"]\n---\nBody\n")
    assert fm == {"title": "Hi", "count": 2, "items": ["a"]}
    assert body == "Body\n"

    fm, body = rs.split_md("---\ntitle: Empty\n---\n")
    assert fm == {"title": "Empty"}
    assert body == ""

    fm, body = rs.split_md("No frontmatter\n")
    assert fm == {}
    assert body == "No frontmatter\n"


def test_render_labels_title_and_desired_state():
    ticket = {
        "id": "PROJ-0001", "tier": "ticket", "title": "Build it",
        "status": "in_progress", "sprint": "SPRINT_A",
    }
    assert rs.render_labels(ticket) == [
        "tier:ticket", "status:in_progress", "sprint:SPRINT_A",
    ]
    assert rs.render_title(ticket) == "[PROJ-0001] Build it"
    assert rs._desired_state(ticket) == "open"

    assert rs.render_labels({"id": "EPIC-1", "tier": "epic", "status": "todo"}) == [
        "tier:epic", "status:todo",
    ]
    assert rs.render_labels({"id": "FEAT-1", "tier": "issue", "status": "done"}) == [
        "tier:issue", "status:done",
    ]
    assert rs._desired_state({"status": "done"}) == "closed"
    assert rs._desired_state({"status": "wont_do"}) == "closed"


def test_signature_is_stable_and_changes_on_any_input():
    base = rs._sig("title", "open", ["b", "a"], "body")
    assert rs._sig("title", "open", ["a", "b"], "body") == base
    assert rs._sig("title!", "open", ["a", "b"], "body") != base
    assert rs._sig("title", "closed", ["a", "b"], "body") != base
    assert rs._sig("title", "open", ["a"], "body") != base
    assert rs._sig("title", "open", ["a", "b"], "body!") != base


def test_read_entities_and_render_ticket_body(tmp_path):
    root = _tiny_work_tree(tmp_path)
    ents = rs.read_entities(root)

    assert set(ents) == {"EPIC-0001", "PROJ-0001", "FEAT-0001", "BUG-0001"}
    assert ents["EPIC-0001"]["tier"] == "epic"
    assert ents["PROJ-0001"]["tier"] == "ticket"
    assert ents["FEAT-0001"]["tier"] == "issue"

    cfg = {"map": {
        "EPIC-0001": {"n": 1}, "PROJ-0001": {"n": 2}, "FEAT-0001": {"n": 3},
    }}
    body = rs.render_body(root, cfg, ents["PROJ-0001"], ents)
    assert "Ticket body." in body
    assert "- **Epic:** EPIC-0001 Launch (#1)" in body
    assert "- **Issues:** FEAT-0001 Feature issue (#3)" in body
    assert "- **Sprint:** SPRINT_2026_W01_A" in body
    assert "- specseed-id: PROJ-0001" in body


def test_next_id_uses_existing_prefix_folders(tmp_path):
    root = _tiny_work_tree(tmp_path)
    assert rs._next_id(root, "BUG") == "BUG-0002"
    assert rs._next_id(root, "PROJ") == "PROJ-0002"


def test_render_sprint_dashboard_mentions_active_sprint(tmp_path):
    root = _tiny_work_tree(tmp_path)
    out = rs.render_sprint_dashboard(root)
    assert "# Sprint SPRINT_2026_W01_A" in out
    assert "Foundations" in out
    assert "- PROJ-0001 Base ticket" in out
    assert "(0/1)" in out


def test_write_md_and_read_text_round_trip(tmp_path):
    p = tmp_path / "doc.md"
    rs._write_md(p, {"id": "BUG-0001", "items": ["a"], "ok": True}, "Body")

    text = rs._read_text(p)
    assert "id: BUG-0001" in text
    assert 'items: ["a"]' in text
    assert "ok: true" in text
    assert text.endswith("Body\n")
    assert rs._read_text(tmp_path / "missing.md") == "_not generated yet_"
