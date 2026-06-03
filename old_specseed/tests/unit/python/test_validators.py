"""issues_validate.py / tickets_validate.py — new optional fields + full chain."""

from pathlib import Path

import issues_validate as IV
import tickets_validate as TV


def _issue(**over):
    e = {
        "title": "t", "type": "feature", "component": "api", "effort_hours": 0.5,
        "status": "todo", "claimed_at": None, "claimed_by": None, "artifacts": {},
    }
    e.update(over)
    return e


def errs_issue(iid, e, all_ids=None):
    errors, _ = IV.validate_one(iid, e, all_ids or {iid}, None, Path("."))
    return errors


def test_qa_type_accepted():
    assert errs_issue("QA-0001", _issue(type="qa")) == []


def test_difficulty_enum():
    assert errs_issue("FEAT-0001", _issue(difficulty="easy")) == []
    assert errs_issue("FEAT-0001", _issue(difficulty="hard")) == []
    bad = errs_issue("FEAT-0001", _issue(difficulty="medium"))
    assert any(x["field"] == "difficulty" for x in bad)


def test_issue_priority_enum_optional():
    assert errs_issue("FEAT-0001", _issue()) == []                   # absent is fine
    assert errs_issue("FEAT-0001", _issue(priority="high")) == []
    bad = errs_issue("FEAT-0001", _issue(priority="urgent"))
    assert any(x["field"] == "priority" for x in bad)


def test_created_at_iso_validation():
    assert errs_issue("FEAT-0001", _issue(created_at="2026-06-01T10:00:00Z")) == []
    bad = errs_issue("FEAT-0001", _issue(created_at="not-a-date"))
    assert any(x["field"] == "created_at" for x in bad)


def test_ticket_created_at_validation():
    t = {"title": "t", "type": "feature", "priority": "high", "status": "todo",
         "created_at": "nope"}
    errors, _ = TV.validate_one("PROJ-0001", t, {"PROJ-0001"}, {"SRS-001": {}}, None, None)
    assert any(x.get("field") == "created_at" for x in errors)


def test_full_chain_with_new_fields(repo):
    """A tree carrying difficulty/priority/created_at + a qa issue assembles and
    validates clean."""
    # add a qa terminal issue + a manual high-priority bug to the base repo
    (repo.pm / "issues" / "QA-0001").mkdir(parents=True)
    (repo.pm / "issues" / "QA-0001" / "QA-0001.md").write_text(
        "---\nid: QA-0001\ntitle: QA\nticket: PROJ-0001\ntype: qa\ncomponent: qa\n"
        'effort_hours: 0.25\ndifficulty: easy\ndepends_on: ["FEAT-0001"]\nstatus: todo\n'
        "claimed_at: null\nclaimed_by: null\n"
        'artifacts: {"touches": [], "tests": [], "migrations": []}\n---\n## AC\n- smoke\n')
    # FEAT-0001 gains difficulty; PROJ ticket lists the qa issue too
    (repo.pm / "issues" / "FEAT-0001" / "FEAT-0001.md").write_text(
        "---\nid: FEAT-0001\ntitle: f\nticket: PROJ-0001\ntype: feature\ncomponent: api\n"
        "effort_hours: 0.5\ndifficulty: hard\npriority: high\ndepends_on: []\nstatus: todo\n"
        "claimed_at: null\nclaimed_by: null\ncreated_at: 2026-06-01T10:00:00Z\n"
        'artifacts: {"touches": [], "tests": [], "migrations": []}\n---\n## AC\n- x\n')
    (repo.pm / "tickets" / "PROJ-0001" / "PROJ-0001.md").write_text(
        "---\nid: PROJ-0001\ntitle: Base\nepic: null\ntype: feature\npriority: high\n"
        "status: todo\ndepends_on: []\n"
        'satisfies_reqs: ["SRS-001"]\nissues: ["FEAT-0001", "QA-0001"]\nsprint: null\n'
        "---\n## Story\nx\n")
    repo.assemble()
    results = repo.validate()
    for name, res in results.items():
        assert res.returncode == 0, f"{name} failed: {res.stdout}\n{res.stderr}"
