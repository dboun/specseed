"""Deep coverage for issues_validate.py."""

import json
from pathlib import Path

import issues_validate as IV
from conftest import run_core


def _issue(**over):
    e = {
        "title": "Build thing",
        "type": "feature",
        "component": "api",
        "effort_hours": 1.0,
        "depends_on": [],
        "status": "todo",
        "claimed_at": None,
        "claimed_by": None,
        "artifacts": {"touches": [], "tests": [], "migrations": []},
    }
    e.update(over)
    return e


def _validate(iid="FEAT-0001", e=None, all_ids=None, tickets=None, repo_root=None):
    return IV.validate_one(
        iid,
        _issue() if e is None else e,
        all_ids or {iid},
        tickets,
        repo_root or Path("."),
    )


def _fields(errors):
    return {e.get("field") for e in errors}


def test_clean_issue_validates_without_errors():
    errors, warnings = _validate()

    assert errors == []
    assert warnings == []


def test_missing_required_field_short_circuits():
    e = _issue()
    del e["effort_hours"]

    errors, warnings = _validate(e=e)

    assert {"id": "FEAT-0001", "kind": "missing_field", "field": "effort_hours"} in errors
    assert warnings == []


def test_non_object_entry_is_schema_error():
    errors, warnings = _validate(e=["not", "an", "object"])

    assert any(e["kind"] == "schema" and "field" not in e for e in errors)
    assert warnings == []


def test_type_and_status_enums_are_checked():
    errors, _ = _validate(e=_issue(type="task", status="active"))

    assert {"type", "status"} <= _fields(errors)


def test_optional_gate_fields_must_be_booleans():
    errors, _ = _validate(e=_issue(review_required="yes", approval_required=1))

    assert {"review_required", "approval_required"} <= _fields(errors)


def test_effort_hours_must_be_positive_number():
    for bad in ("large", 0, -1, True):
        errors, _ = _validate(e=_issue(effort_hours=bad))
        assert any(e["kind"] == "effort" for e in errors)


def test_depends_on_must_be_list_and_reference_known_issue():
    errors, _ = _validate(e=_issue(depends_on="FEAT-9999"))
    assert any(e.get("field") == "depends_on" and e["kind"] == "schema" for e in errors)

    errors, _ = _validate(e=_issue(depends_on=["FEAT-9999"]), all_ids={"FEAT-0001"})
    assert any(e.get("field") == "depends_on" and e["kind"] == "dangling_ref" for e in errors)


def test_ticket_cross_ref_is_checked_when_tickets_map_present():
    errors, _ = _validate(e=_issue(ticket="PROJ-4040"), tickets={"PROJ-0001": {}})

    assert any(e.get("field") == "ticket" and e["kind"] == "dangling_ref" for e in errors)


def test_artifacts_must_be_object_with_string_lists(tmp_path):
    errors, _ = _validate(e=_issue(artifacts=[]), repo_root=tmp_path)
    assert any(e.get("field") == "artifacts" for e in errors)

    errors, warnings = _validate(
        e=_issue(artifacts={"touches": [1], "tests": ["missing_test.py"], "migrations": [False]}),
        repo_root=tmp_path,
    )

    assert {"artifacts.touches", "artifacts.migrations"} <= _fields(errors)
    assert any(w.get("field") == "artifacts.tests" and w["kind"] == "missing_tests_file"
               for w in warnings)


def test_claimed_states_require_claim_fields():
    errors, _ = _validate(e=_issue(status="in_progress"))

    assert any(e["kind"] == "claim_invariant" and "claim fields null" in e["detail"]
               for e in errors)


def test_unclaimed_states_reject_claim_fields():
    errors, _ = _validate(
        e=_issue(status="todo", claimed_at="2026-06-01T10:00:00Z", claimed_by="agent-a")
    )

    assert any(e["kind"] == "claim_invariant" and "claim fields populated" in e["detail"]
               for e in errors)


def test_partial_or_malformed_claim_is_invalid():
    errors, _ = _validate(e=_issue(status="blocked", claimed_at="not-a-date", claimed_by=None))

    assert {"claimed_at", "claimed_at/claimed_by"} <= _fields(errors)


def test_blocked_accepts_claimed_or_unclaimed():
    unclaimed, _ = _validate(e=_issue(status="blocked"))
    claimed, _ = _validate(
        e=_issue(status="blocked", claimed_at="2026-06-01T10:00:00Z", claimed_by="agent-a")
    )

    assert unclaimed == []
    assert claimed == []


def test_cli_reports_dependency_cycle(tmp_path):
    pm = tmp_path / ".specseed" / "project_management"
    pm.mkdir(parents=True)
    issues = {
        "FEAT-0001": _issue(depends_on=["FEAT-0002"]),
        "FEAT-0002": _issue(depends_on=["FEAT-0001"]),
    }
    (pm / "issues.json").write_text(json.dumps(issues), encoding="utf-8")

    res = run_core("issues_validate.py", "--pm-dir", pm, cwd=tmp_path)

    assert res.returncode == 1
    payload = json.loads(res.stdout)
    assert any(e["kind"] == "cycle" for e in payload["errors"])
