"""Coverage for sprints_validate.py."""

import json

import pytest

import sprints_validate as SV


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _tickets(*tickets):
    return {
        tid: {
            "title": tid,
            "type": "feature",
            "priority": "medium",
            "status": "todo",
            "depends_on": deps,
            "sprint": sprint,
            "effort_hours": effort,
        }
        for tid, sprint, effort, deps in tickets
    }


def _sprints(*sprints):
    return {
        sid: {
            "title": sid,
            "status": status,
            "tickets": tickets,
            "order": order,
            "effort_hours": effort,
        }
        for sid, status, tickets, order, effort in sprints
    }


def _seed(repo, tickets, sprints):
    _write_json(repo.pm / "tickets.json", tickets)
    _write_json(repo.pm / "sprints.json", sprints)


def _payload(res):
    return json.loads(res.stdout)


def test_happy_path_single_sprint_validates(repo_with_sprint):
    res = repo_with_sprint.core("sprints_validate.py")

    assert res.returncode == 0
    assert "OK: 1 sprints validated" in res.stdout


def test_ticket_in_earlier_sprint_cannot_depend_on_later_sprint_ticket(repo):
    sprint_a = "SPRINT_2026_W01_A"
    sprint_b = "SPRINT_2026_W02_A"
    _seed(
        repo,
        _tickets(
            ("PROJ-0001", sprint_a, 1, ["PROJ-0002"]),
            ("PROJ-0002", sprint_b, 1, []),
        ),
        _sprints(
            (sprint_a, "planned", ["PROJ-0001"], 0, 1),
            (sprint_b, "planned", ["PROJ-0002"], 1, 1),
        ),
    )

    res = repo.core("sprints_validate.py")

    assert res.returncode == 1
    assert any(e["kind"] == "backward_sprint_dep" for e in _payload(res)["errors"])


def test_budget_over_factor_is_warning_not_error(repo):
    sprint = "SPRINT_2026_W01_A"
    _seed(
        repo,
        _tickets(("PROJ-0001", sprint, 16, [])),
        _sprints((sprint, "planned", ["PROJ-0001"], 0, 16)),
    )

    res = repo.core("sprints_validate.py", "--budget", "10", "--over-factor", "1.5")

    assert res.returncode == 0
    assert "over_budget" in res.stdout


def test_budget_at_or_under_over_factor_has_no_warning(repo):
    sprint = "SPRINT_2026_W01_A"
    _seed(
        repo,
        _tickets(("PROJ-0001", sprint, 15, [])),
        _sprints((sprint, "planned", ["PROJ-0001"], 0, 15)),
    )

    res = repo.core("sprints_validate.py", "--budget", "10", "--over-factor", "1.5")

    assert res.returncode == 0
    assert "over_budget" not in res.stdout
    assert "OK: 1 sprints validated" in res.stdout


def test_sprint_ticket_membership_must_match_ticket_back_ref(repo):
    sprint = "SPRINT_2026_W01_A"
    other = "SPRINT_2026_W02_A"
    _seed(
        repo,
        _tickets(("PROJ-0001", other, 1, [])),
        _sprints((sprint, "planned", ["PROJ-0001"], 0, 1)),
    )

    res = repo.core("sprints_validate.py")

    assert res.returncode == 1
    assert any(e["kind"] == "back_ref" and e.get("field") == "tickets"
               for e in _payload(res)["errors"])


def test_sprint_ticket_must_exist_in_tickets_json(repo):
    sprint = "SPRINT_2026_W01_A"
    _seed(repo, {}, _sprints((sprint, "planned", ["PROJ-4040"], 0, 0)))

    res = repo.core("sprints_validate.py")

    assert res.returncode == 1
    assert any(e["kind"] == "dangling_ref" and e.get("field") == "tickets"
               for e in _payload(res)["errors"])


def test_ticket_cannot_appear_in_two_sprints(repo):
    sprint_a = "SPRINT_2026_W01_A"
    sprint_b = "SPRINT_2026_W02_A"
    _seed(
        repo,
        _tickets(("PROJ-0001", sprint_a, 1, [])),
        _sprints(
            (sprint_a, "planned", ["PROJ-0001"], 0, 1),
            (sprint_b, "planned", ["PROJ-0001"], 1, 1),
        ),
    )

    res = repo.core("sprints_validate.py")

    assert res.returncode == 1
    assert any(e["kind"] == "multi_sprint" for e in _payload(res)["errors"])


def test_status_and_ticket_schema_are_checked(repo):
    _seed(
        repo,
        {},
        {
            "SPRINT_2026_W01_A": {
                "title": "Bad",
                "status": "active",
                "tickets": "PROJ-0001",
                "order": 0,
                "effort_hours": 0,
            }
        },
    )

    res = repo.core("sprints_validate.py")

    assert res.returncode == 1
    fields = {e.get("field") for e in _payload(res)["errors"]}
    assert {"status", "tickets"} <= fields


def test_longest_chain_linear_and_diamond():
    assert SV.longest_chain(["A", "B", "C"], {"A": ["B"], "B": ["C"], "C": []}) == 3
    assert SV.longest_chain(
        ["A", "B", "C", "D"],
        {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []},
    ) == 3


def test_longest_chain_cycle_does_not_recurse_infinitely():
    # Cycle-tolerance is only a no-infinite-recursion guard; the returned
    # length inside a cycle is not meaningful (sprint graphs are acyclic).
    assert SV.longest_chain(["A", "B"], {"A": ["B"], "B": ["A"]}) == 3
