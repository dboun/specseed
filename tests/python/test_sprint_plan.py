import json

import sprint_plan as SP
from conftest import run_core


def _ticket(effort, depends_on=None, epic="core", priority="medium", status="todo"):
    return {
        "title": "Ticket",
        "effort_hours": effort,
        "depends_on": depends_on or [],
        "epic": epic,
        "priority": priority,
        "status": status,
    }


def test_serial_chain_len_handles_linear_diamond_and_isolated_nodes():
    deps = {
        "A": [],
        "B": ["A"],
        "C": ["A"],
        "D": ["B", "C"],
        "E": [],
    }

    assert SP.serial_chain_len(["A", "B", "C", "D"], deps) == 3
    assert SP.serial_chain_len(["E"], deps) == 1
    assert SP.serial_chain_len([], deps) == 0


def test_critical_path_set_for_empty_single_and_diamond_graphs():
    assert SP.critical_path_set({}) == set()
    assert SP.critical_path_set({"A": _ticket(2)}) == {"A"}
    assert SP.critical_path_set({
        "A": _ticket(2),
        "B": _ticket(5, ["A"]),
        "C": _ticket(1, ["A"]),
        "D": _ticket(3, ["B", "C"]),
    }) == {"A", "B", "D"}


def test_plan_packs_sprints_by_budget_dependency_order_and_critical_path_priority():
    tickets = {
        "A": _ticket(4, priority="low"),
        "B": _ticket(4, ["A"], priority="low"),
        "C": _ticket(3, ["A"], priority="high"),
        "D": _ticket(4, ["B", "C"]),
    }

    sprints, cp = SP.plan(tickets, budget=7)

    assert cp == {"A", "B", "D"}
    assert [(ids, effort) for ids, effort, _ in sprints] == [
        (["A"], 4.0),
        (["B", "C"], 7.0),
        (["D"], 4.0),
    ]
    sprint_by_ticket = {
        tid: index
        for index, (ids, _, _) in enumerate(sprints)
        for tid in ids
    }
    for tid, ticket in tickets.items():
        assert all(sprint_by_ticket[dep] <= sprint_by_ticket[tid] for dep in ticket["depends_on"])


def test_plan_places_single_oversized_ticket_alone():
    sprints, cp = SP.plan({"BIG": _ticket(10)}, budget=5)

    assert cp == {"BIG"}
    assert [(ids, effort) for ids, effort, _ in sprints] == [(["BIG"], 10.0)]


def test_plan_skips_abandoned_tickets_and_detects_cycles():
    sprints, cp = SP.plan({
        "A": _ticket(1),
        "B": _ticket(1, ["A"], status="wont_do"),
    }, budget=5)

    assert [ids for ids, _, _ in sprints] == [["A"]]
    assert cp == {"A", "B"}

    try:
        SP.plan({"A": _ticket(1, ["B"]), "B": _ticket(1, ["A"])}, budget=5)
    except ValueError as exc:
        assert "cycle detected" in str(exc)
    else:
        raise AssertionError("expected cycle detection")


def test_cli_emits_proposal_and_honors_budget(tmp_path):
    pm = tmp_path / ".specseed" / "project_management"
    pm.mkdir(parents=True)
    (pm / "tickets.json").write_text(
        json.dumps({
            "A": _ticket(4),
            "B": _ticket(4, ["A"]),
            "C": _ticket(3, ["A"]),
        }),
        encoding="utf-8",
    )

    result = run_core("sprint_plan.py", "--budget", "7", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    proposal = json.loads(result.stdout)
    assert proposal["budget_hours"] == 7.0
    assert proposal["diagnostics"]["n_sprints"] == 2
    assert proposal["proposed_sprints"][0]["tickets"][0]["id"] == "A"

