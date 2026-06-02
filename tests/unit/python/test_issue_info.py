import json

import pytest

import issue_info


def test_issue_info_prints_issue_ticket_and_reqs(repo):
    repo.assemble()
    res = repo.core("issue_info.py", "FEAT-0001")
    assert res.returncode == 0, res.stderr

    data = json.loads(res.stdout)
    assert data["issue_id"] == "FEAT-0001"
    assert data["issue"]["title"] == "FEAT-0001"
    assert data["ticket_id"] == "PROJ-0001"
    assert data["ticket"]["title"] == "Base"
    assert data["reqs"] == {"SRS-001": {"text": "do a thing"}}


def test_unknown_issue_exits_one(repo):
    repo.assemble()
    res = repo.core("issue_info.py", "NOPE-0001")
    assert res.returncode == 1
    assert "issue NOPE-0001 not found" in res.stderr


def test_load_required_missing_file_exits_two(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        issue_info.load(tmp_path / "missing.json", required=True)

    assert exc.value.code == 2
    assert "missing.json not found" in capsys.readouterr().err


def test_load_optional_missing_file_returns_none(tmp_path):
    assert issue_info.load(tmp_path / "missing.json", required=False) is None
