import subprocess
import sys
from graphlib import CycleError

import pytest

from conftest import CORE

import tickets_analyze as TA


def _run_stdin(text):
    return subprocess.run(
        [sys.executable, str(CORE / "tickets_analyze.py")],
        input=text, capture_output=True, text=True,
    )


def _ticket(effort, depends_on=None, status="todo"):
    return {
        "effort_hours": effort,
        "depends_on": depends_on or [],
        "status": status,
    }


def test_empty_ticket_graph_returns_empty_analysis():
    assert TA.analyze({}) == {
        "critical_path": [],
        "critical_path_effort_hours": 0,
        "build_order": [],
        "next_todo": None,
    }


def test_linear_chain_critical_path_and_next_todo():
    result = TA.analyze({
        "A": _ticket(1, status="done"),
        "B": _ticket(2, ["A"]),
        "C": _ticket(3, ["B"]),
    })

    assert result["critical_path"] == ["A", "B", "C"]
    assert result["critical_path_effort_hours"] == 6
    assert result["build_order"] == ["A", "B", "C"]
    assert result["next_todo"] == "B"


def test_diamond_chooses_longer_weighted_path():
    result = TA.analyze({
        "A": _ticket(2, status="done"),
        "B": _ticket(5, ["A"]),
        "C": _ticket(1, ["A"]),
        "D": _ticket(3, ["B", "C"]),
    })

    assert result["critical_path"] == ["A", "B", "D"]
    assert result["critical_path_effort_hours"] == 10
    assert result["build_order"] == ["A", "B", "C", "D"]
    assert result["next_todo"] == "B"


def test_next_todo_skips_done_and_in_progress_tickets():
    result = TA.analyze({
        "A": _ticket(1, status="done"),
        "B": _ticket(1, ["A"], status="in_progress"),
        "C": _ticket(1, ["A"], status="blocked"),
        "D": _ticket(1, ["C"], status="todo"),
    })

    assert result["next_todo"] == "C"


def test_missing_effort_and_unknown_deps_raise_validation_errors():
    with pytest.raises(ValueError, match="missing required field 'effort_hours'"):
        TA.analyze({"A": {"depends_on": []}})

    with pytest.raises(ValueError, match="depends on unknown Z"):
        TA.analyze({"A": _ticket(1, ["Z"])})


def test_cycle_raises_cycle_error():
    with pytest.raises(CycleError):
        TA.analyze({"A": _ticket(1, ["B"]), "B": _ticket(1, ["A"])})


def test_empty_stdin_prints_usage_no_traceback():
    res = _run_stdin("")
    assert res.returncode == 2
    assert "Usage:" in res.stderr
    assert "Traceback" not in res.stderr


def test_blank_stdin_prints_usage_no_traceback():
    res = _run_stdin("   \n  \t\n")
    assert res.returncode == 2
    assert "Usage:" in res.stderr
    assert "Traceback" not in res.stderr


def test_real_json_via_stdin_still_works():
    import json
    res = _run_stdin(json.dumps({"A": _ticket(1, status="done")}))
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["build_order"] == ["A"]

