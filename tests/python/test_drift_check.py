import json
from datetime import datetime
from pathlib import Path

import drift_check as D
from conftest import run_core, write


def test_parse_frontmatter_valid_missing_and_weird_lines():
    text = "---\nsettled: true\nbad line\nname: 'api'\n---\nBody\n"
    assert D.parse_frontmatter(text) == {"settled": "true", "name": "api"}
    assert D.parse_frontmatter("no frontmatter") is None
    assert D.parse_frontmatter("---\nsettled: true\nBody") is None


def test_parse_settled_at_and_infer_component():
    assert D.parse_settled_at("2026-06-01") == datetime(2026, 6, 1)
    assert D.parse_settled_at("bad") is None
    assert D.parse_settled_at(None) is None
    assert D.infer_component(Path("api-srs.md")) == "api"
    assert D.infer_component(Path("README.md")) is None


def test_check_missing_tests_warns_only_for_absent_files(tmp_path):
    write(tmp_path / "tests" / "test_present.py", "")
    issues = {
        "FEAT-0001": {"artifacts": {"tests": ["tests/test_present.py"]}},
        "FEAT-0002": {"artifacts": {"tests": ["tests/test_missing.py"]}},
    }
    warnings = []

    D.check_missing_tests(issues, tmp_path, warnings)

    assert warnings == [{
        "kind": "missing_test_file",
        "ref": "tests/test_missing.py",
        "referenced_by": "FEAT-0002 (artifacts.tests)",
    }]


def test_check_orphan_tests(tmp_path):
    write(tmp_path / "tests" / "test_extra.py", "")
    warnings = []
    D.check_orphan_tests(tmp_path, {}, warnings)
    assert warnings == [{
        "kind": "unreferenced_test_file",
        "ref": "tests/test_extra.py",
        "detail": "test file on disk not referenced by any issue",
    }]

    warnings = []
    D.check_orphan_tests(
        tmp_path,
        {"FEAT-0001": {"artifacts": {"tests": ["tests/test_extra.py"]}}},
        warnings,
    )
    assert warnings == []


def test_check_orphan_handlers(tmp_path):
    write(tmp_path / "app.py",
          "from flask import Flask\n"
          "app = Flask(__name__)\n"
          "@app.get('/known')\n"
          "def known(): pass\n"
          "@app.post('/missing')\n"
          "def missing(): pass\n")
    warnings = []

    D.check_orphan_handlers(tmp_path, {"SRS-001": {"text": "GET /known"}}, warnings)

    assert len(warnings) == 1
    assert warnings[0]["kind"] == "unreferenced_handler"
    assert "POST /missing" in warnings[0]["ref"]


def _seed_clean_tree(root, issue_tests=None):
    issue_tests = issue_tests or []
    write(root / ".specseed" / "spec" / "reqs.json",
          json.dumps({"SRS-001": {"text": "no routes"}}))
    write(root / ".specseed" / "project_management" / "issues.json",
          json.dumps({"FEAT-0001": {"artifacts": {"tests": issue_tests}}}))


def test_cli_clean_tree_quiet_exits_zero(tmp_path):
    _seed_clean_tree(tmp_path)
    res = run_core("drift_check.py", tmp_path, "--no-git", "--quiet",
                   "--no-handler-check", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "OK"


def test_cli_warning_surfaces(tmp_path):
    _seed_clean_tree(tmp_path, ["tests/test_missing.py"])
    res = run_core("drift_check.py", tmp_path, "--no-git", "--no-handler-check",
                   cwd=tmp_path)
    assert res.returncode == 0, res.stderr

    data = json.loads(res.stdout)
    assert data["ok"] is True
    assert data["warnings"][0]["kind"] == "missing_test_file"
    assert data["stats"]["n_warnings"] == 1
