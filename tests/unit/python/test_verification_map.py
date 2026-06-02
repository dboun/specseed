import json

from conftest import run_core, write


def _seed(root):
    pm = root / ".specseed" / "project_management"
    spec = root / ".specseed" / "spec"
    write(spec / "reqs.json", json.dumps({
        "SRS-001": {"text": "covered"},
        "SRS-002": {"text": "deprecated work"},
        "SRS-003": {"text": "uncovered"},
    }))
    write(pm / "tickets.json", json.dumps({
        "PROJ-0001": {
            "status": "todo",
            "satisfies_reqs": ["SRS-001"],
            "issues": ["FEAT-0001"],
        },
        "PROJ-0002": {
            "status": "deprecated",
            "satisfies_reqs": ["SRS-002"],
            "issues": ["FEAT-0002"],
        },
    }))
    write(pm / "issues.json", json.dumps({
        "FEAT-0001": {
            "status": "todo",
            "ticket": "PROJ-0001",
            "artifacts": {"tests": ["tests/test_api.py"]},
        },
        "FEAT-0002": {
            "status": "deprecated",
            "ticket": "PROJ-0002",
            "artifacts": {"tests": ["tests/test_old.py"]},
        },
    }))
    return pm, spec


def test_json_maps_req_to_tickets_issues_and_tests(tmp_path):
    _seed(tmp_path)
    res = run_core("verification_map.py", "--format", "json", cwd=tmp_path)
    assert res.returncode == 0, res.stderr

    data = json.loads(res.stdout)
    assert data["SRS-001"] == {
        "tickets": ["PROJ-0001"],
        "issues": ["FEAT-0001"],
        "tests": ["tests/test_api.py"],
    }
    assert data["SRS-003"] == {"tickets": [], "issues": [], "tests": []}


def test_markdown_and_req_filter(tmp_path):
    _seed(tmp_path)
    res = run_core("verification_map.py", "--format", "markdown", "--req", "SRS-001",
                   cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "| SRS-001 | PROJ-0001 | FEAT-0001 | tests/test_api.py |" in res.stdout
    assert "SRS-003" not in res.stdout


def test_deprecated_entries_excluded_by_default_and_included_by_flag(tmp_path):
    _seed(tmp_path)
    default = json.loads(run_core("verification_map.py", cwd=tmp_path).stdout)
    included = json.loads(run_core("verification_map.py", "--include-deprecated",
                                   cwd=tmp_path).stdout)

    assert default["SRS-002"] == {"tickets": [], "issues": [], "tests": []}
    assert included["SRS-002"] == {
        "tickets": ["PROJ-0002"],
        "issues": ["FEAT-0002"],
        "tests": ["tests/test_old.py"],
    }


def test_check_reports_uncovered_reqs(tmp_path):
    _seed(tmp_path)
    res = run_core("verification_map.py", "--check", cwd=tmp_path)
    assert res.returncode == 1
    assert "SRS-003" in res.stderr
    assert "no satisfying ticket" in res.stderr


def test_check_passes_when_all_reqs_have_tests(tmp_path):
    _seed(tmp_path)
    reqs_path = tmp_path / ".specseed" / "spec" / "reqs.json"
    reqs_path.write_text(json.dumps({"SRS-001": {"text": "covered"}}))
    res = run_core("verification_map.py", "--check", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "all 1 req" in res.stdout
