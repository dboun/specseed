import json
import sys
from pathlib import Path

from conftest import CORE


import requirements_analyze as RA


def _req(depends_on=None):
    return {
        "text": "Do it",
        "type": "functional",
        "priority": "must",
        "depends_on": depends_on or [],
    }


def _analyzer():
    return RA


def test_import_is_available():
    assert RA is not None


def test_clean_linear_dag_reports_topo_stats_and_no_findings():
    ra = _analyzer()
    reqs = {
        "SRS-A": _req(),
        "SRS-B": _req(["SRS-A"]),
        "SRS-C": _req(["SRS-B"]),
    }
    tickets = {"PROJ-1": {"satisfies_reqs": ["SRS-A", "SRS-B", "SRS-C"]}}

    result = ra.analyze(reqs, tickets)

    assert result == {
        "ok": True,
        "errors": [],
        "warnings": [],
        "topo_order": ["SRS-A", "SRS-B", "SRS-C"],
        "stats": {"n_reqs": 3, "n_roots": 1, "n_leaves": 1},
    }


def test_cycle_is_reported_and_topo_order_is_empty():
    ra = _analyzer()
    result = ra.analyze({"SRS-A": _req(["SRS-B"]), "SRS-B": _req(["SRS-A"])})

    assert result["ok"] is False
    assert result["topo_order"] == []
    assert any("cycle detected" in e for e in result["errors"])


def test_dangling_deps_and_unknown_ticket_refs_are_errors():
    ra = _analyzer()
    result = ra.analyze(
        {"SRS-A": _req(["SRS-MISSING"])},
        {"PROJ-1": {"satisfies_reqs": ["SRS-A", "SRS-OTHER"]}},
    )

    assert result["ok"] is False
    assert "SRS-A depends on unknown SRS-MISSING" in result["errors"]
    assert "ticket PROJ-1 satisfies unknown req SRS-OTHER" in result["errors"]


def test_unsatisfied_requirement_is_warning_not_error():
    ra = _analyzer()
    result = ra.analyze(
        {"SRS-A": _req(), "SRS-B": _req()},
        {"PROJ-1": {"satisfies_reqs": ["SRS-A"]}},
    )

    assert result["ok"] is True
    assert result["warnings"] == ["SRS-B not satisfied by any ticket"]


def test_cli_outputs_analysis_json(tmp_path):
    reqs_path = tmp_path / "reqs.json"
    reqs_path.write_text(json.dumps({"SRS-A": _req()}), encoding="utf-8")

    import subprocess

    result = subprocess.run(
        [sys.executable, str(Path(CORE) / "requirements_analyze.py"), str(reqs_path)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True

