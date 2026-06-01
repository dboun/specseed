"""Deep coverage for tickets_validate.py."""

import json

import tickets_validate as TV
from conftest import run_core


def _ticket(**over):
    t = {
        "title": "Ship feature",
        "type": "feature",
        "priority": "medium",
        "status": "todo",
        "depends_on": [],
        "satisfies_reqs": ["SRS-001"],
        "issues": [],
        "epic": None,
    }
    t.update(over)
    return t


def _validate(tid="PROJ-0001", t=None, all_ids=None, reqs=None, epics_dir=None, issues=None):
    return TV.validate_one(
        tid,
        _ticket() if t is None else t,
        all_ids or {tid},
        {"SRS-001": {}} if reqs is None else reqs,
        epics_dir,
        issues,
    )


def _fields(errors):
    return {e.get("field") for e in errors}


def test_clean_ticket_validates_without_errors():
    errors, warnings = _validate()

    assert errors == []
    assert warnings == []


def test_missing_required_field_short_circuits():
    t = _ticket()
    del t["priority"]

    errors, warnings = _validate(t=t)

    assert {"id": "PROJ-0001", "kind": "missing_field", "field": "priority"} in errors
    assert warnings == []


def test_non_object_entry_is_schema_error():
    errors, warnings = _validate(t="bad")

    assert any(e["kind"] == "schema" and "field" not in e for e in errors)
    assert warnings == []


def test_type_priority_and_status_enums_are_checked():
    errors, _ = _validate(t=_ticket(type="qa", priority="urgent", status="in_review"))

    assert {"type", "priority", "status"} <= _fields(errors)


def test_optional_approval_required_must_be_boolean():
    errors, _ = _validate(t=_ticket(approval_required="true"))

    assert any(e.get("field") == "approval_required" for e in errors)


def test_satisfies_reqs_must_be_list_and_reference_known_req():
    errors, warnings = _validate(t=_ticket(satisfies_reqs="SRS-001"))
    assert any(e.get("field") == "satisfies_reqs" and e["kind"] == "schema" for e in errors)
    assert warnings == []

    errors, warnings = _validate(t=_ticket(satisfies_reqs=["SRS-404"]), reqs={"SRS-001": {}})
    assert any(e.get("field") == "satisfies_reqs" and e["kind"] == "dangling_ref"
               for e in errors)
    assert warnings == []


def test_empty_or_missing_satisfies_reqs_is_warning_not_error():
    empty_errors, empty_warnings = _validate(t=_ticket(satisfies_reqs=[]))
    missing = _ticket()
    del missing["satisfies_reqs"]
    missing_errors, missing_warnings = _validate(t=missing)

    assert empty_errors == []
    assert any(w["kind"] == "empty_satisfies_reqs" for w in empty_warnings)
    assert missing_errors == []
    assert any(w["kind"] == "empty_satisfies_reqs" for w in missing_warnings)


def test_issues_list_refs_and_back_refs_are_checked():
    missing_errors, _ = _validate(t=_ticket(issues=["FEAT-4040"]), issues={})
    wrong_parent_errors, _ = _validate(
        t=_ticket(issues=["FEAT-0001"]),
        issues={"FEAT-0001": {"ticket": "PROJ-9999"}},
    )

    assert any(e.get("field") == "issues" and e["kind"] == "dangling_ref"
               for e in missing_errors)
    assert any(e.get("field") == "issues" and e["kind"] == "back_ref"
               for e in wrong_parent_errors)


def test_epic_folder_must_exist_when_epics_dir_present(tmp_path):
    epics = tmp_path / "epics"
    (epics / "EPIC-0001").mkdir(parents=True)

    good, _ = _validate(t=_ticket(epic="EPIC-0001"), epics_dir=epics)
    bad, _ = _validate(t=_ticket(epic="EPIC-4040"), epics_dir=epics)

    assert good == []
    assert any(e.get("field") == "epic" and e["kind"] == "dangling_ref" for e in bad)


def test_depends_on_must_be_list_and_reference_known_ticket():
    schema_errors, _ = _validate(t=_ticket(depends_on="PROJ-0002"))
    dangling_errors, _ = _validate(
        t=_ticket(depends_on=["PROJ-9999"]),
        all_ids={"PROJ-0001", "PROJ-0002"},
    )

    assert any(e.get("field") == "depends_on" and e["kind"] == "schema"
               for e in schema_errors)
    assert any(e.get("field") == "depends_on" and e["kind"] == "dangling_ref"
               for e in dangling_errors)


def test_cli_reports_ticket_dependency_cycle(tmp_path):
    pm = tmp_path / ".specseed" / "project_management"
    spec = tmp_path / ".specseed" / "spec"
    pm.mkdir(parents=True)
    spec.mkdir(parents=True)
    (spec / "reqs.json").write_text(json.dumps({"SRS-001": {}}), encoding="utf-8")
    tickets = {
        "PROJ-0001": _ticket(depends_on=["PROJ-0002"]),
        "PROJ-0002": _ticket(depends_on=["PROJ-0001"]),
    }
    (pm / "tickets.json").write_text(json.dumps(tickets), encoding="utf-8")

    res = run_core("tickets_validate.py", "--pm-dir", pm, "--reqs-path", spec / "reqs.json",
                   cwd=tmp_path)

    assert res.returncode == 1
    payload = json.loads(res.stdout)
    assert any(e["kind"] == "cycle" for e in payload["errors"])
