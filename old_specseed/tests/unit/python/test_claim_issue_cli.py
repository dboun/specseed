"""claim_issue.py — CLI claim layer, sprint scope, stale takeover, lock helpers."""

import fcntl
import json
import subprocess
import sys
import textwrap
import time

import claim_issue as C
from conftest import Repo


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _json_repo(tmp_path, issues, tickets=None, sprints=None):
    r = Repo(tmp_path)
    _write_json(r.pm / "issues.json", issues)
    if tickets is not None:
        _write_json(r.pm / "tickets.json", tickets)
    if sprints is not None:
        _write_json(r.pm / "sprints.json", sprints)
    return r


def _issue(ticket="T-1", status="todo", **over):
    data = {"ticket": ticket, "status": status, "depends_on": [], "claimed_by": None}
    data.update(over)
    return data


def _ticket(status="todo", **over):
    data = {"status": status, "depends_on": [], "priority": "medium"}
    data.update(over)
    return data


def test_auto_claim_updates_issues_json(repo):
    repo.assemble()

    res = repo.core("claim_issue.py", "--agent", "agent-a")

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["claimed"] is True
    assert payload["issue_id"] == "FEAT-0001"
    assert payload["claimed_by"] == "agent-a"

    issue = repo.issues()["FEAT-0001"]
    assert issue["status"] == "in_progress"
    assert issue["claimed_by"] == "agent-a"
    assert issue["claimed_at"]


def test_targeted_claim_and_unmet_deps_refusal(tmp_path):
    r = _json_repo(
        tmp_path,
        {
            "I-1": _issue(),
            "I-2": _issue(depends_on=["I-1"]),
        },
        {"T-1": _ticket()},
    )

    ok = r.core("claim_issue.py", "I-1", "--agent", "agent-a")
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["claimed"] is True

    blocked = r.core("claim_issue.py", "I-2", "--agent", "agent-a")
    assert blocked.returncode == 0, blocked.stderr
    payload = json.loads(blocked.stdout)
    assert payload["claimed"] is False
    assert payload["reason"] == "issue depends_on not all done"


def test_skip_auto_pick_claims_following_ready_issue(tmp_path):
    r = _json_repo(
        tmp_path,
        {
            "I-1": _issue(),
            "I-2": _issue(),
        },
        {"T-1": _ticket()},
    )

    res = r.core("claim_issue.py", "--skip", "I-1", "--agent", "agent-a")

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["claimed"] is True
    assert payload["issue_id"] == "I-2"
    assert _read_json(r.pm / "issues.json")["I-1"]["status"] == "todo"


def test_nothing_ready_is_normal_no_claim(tmp_path):
    r = _json_repo(
        tmp_path,
        {
            "I-1": _issue(status="done"),
            "I-2": _issue(status="awaiting_approval"),
        },
        {"T-1": _ticket()},
    )

    res = r.core("claim_issue.py", "--agent", "agent-a")

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload == {
        "claimed": False,
        "issue_id": None,
        "reason": "no ready issue to claim",
        "stale": False,
    }


def test_peek_is_readonly_and_reports_type_difficulty(tmp_path):
    r = _json_repo(
        tmp_path,
        {
            "I-1": _issue(type="feature", difficulty="easy"),
            "I-2": _issue(type="qa", difficulty="hard", depends_on=["I-1"]),
        },
        {"T-1": _ticket()},
    )
    before = (r.pm / "issues.json").read_text(encoding="utf-8")

    auto = r.core("claim_issue.py", "--peek")
    assert auto.returncode == 0, auto.stderr
    payload = json.loads(auto.stdout)
    assert payload == {"peek": True, "issue_id": "I-1",
                       "type": "feature", "difficulty": "easy"}

    # specific id peek surfaces that issue's type/difficulty
    targeted = r.core("claim_issue.py", "I-2", "--peek")
    assert json.loads(targeted.stdout) == {"peek": True, "issue_id": "I-2",
                                           "type": "qa", "difficulty": "hard"}

    # read-only: nothing claimed, file byte-identical
    assert (r.pm / "issues.json").read_text(encoding="utf-8") == before


def test_sprint_scope_current_and_spill_cli(tmp_path):
    issues = {
        "I-CURRENT": _issue(ticket="T-CURRENT"),
        "I-NEXT": _issue(ticket="T-NEXT"),
    }
    tickets = {
        "T-CURRENT": _ticket(sprint="SPRINT-A"),
        "T-NEXT": _ticket(sprint="SPRINT-B"),
    }
    sprints = {
        "SPRINT-A": {"status": "in_progress", "order": 1, "tickets": ["T-CURRENT"]},
        "SPRINT-B": {"status": "planned", "order": 2, "tickets": ["T-NEXT"]},
    }
    current = _json_repo(tmp_path / "current", issues, tickets, sprints)

    first = current.core("claim_issue.py", "--sprint-scope", "current", "--agent", "agent-a")
    assert json.loads(first.stdout)["issue_id"] == "I-CURRENT"

    second = current.core("claim_issue.py", "--sprint-scope", "current", "--agent", "agent-a")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["reason"] == "no ready issue in the in_progress sprint"

    spill = _json_repo(tmp_path / "spill", issues, tickets, sprints)
    first = spill.core("claim_issue.py", "--sprint-scope", "spill", "--agent", "agent-a")
    second = spill.core("claim_issue.py", "--sprint-scope", "spill", "--agent", "agent-a")
    assert json.loads(first.stdout)["issue_id"] == "I-CURRENT"
    assert json.loads(second.stdout)["issue_id"] == "I-NEXT"


def test_sprint_rank_helpers():
    tickets = {
        "T-CURRENT": _ticket(sprint="SPRINT-A"),
        "T-NEXT": _ticket(),
    }
    sprints = {
        "SPRINT-A": {"status": "in_progress", "order": 3, "tickets": []},
        "SPRINT-B": {"status": "planned", "order": 4, "tickets": ["T-NEXT"]},
        "SPRINT-C": {"status": "done", "order": 1, "tickets": []},
    }

    ranks = C.sprint_rank_map(sprints)
    t_sprints = C.ticket_sprint_map(tickets, sprints)

    assert ranks["SPRINT-A"] == (C.CURRENT_TIER, 3)
    assert ranks["SPRINT-B"] == (C.PLANNED_TIER, 4)
    assert ranks["SPRINT-C"] == (C.BACKLOG_TIER, 1)
    assert t_sprints == {"T-NEXT": "SPRINT-B", "T-CURRENT": "SPRINT-A"}
    assert C.issue_sprint_rank(_issue(ticket="T-CURRENT"), tickets, t_sprints, ranks) == (C.CURRENT_TIER, 3)
    assert C.issue_sprint_rank(_issue(ticket="T-NEXT"), tickets, t_sprints, ranks) == (C.PLANNED_TIER, 4)
    assert C.issue_sprint_rank(_issue(ticket="T-BACKLOG"), tickets, t_sprints, ranks) == (C.BACKLOG_TIER, 0)


def test_stale_takeover_and_no_stale_takeup(tmp_path):
    r = _json_repo(
        tmp_path,
        {
            "I-1": _issue(
                status="in_progress",
                claimed_by="other-agent",
                claimed_at="2000-01-01T00:00:00Z",
            )
        },
        {"T-1": _ticket()},
    )

    refused = r.core(
        "claim_issue.py", "I-1", "--agent", "agent-a", "--stale-hours", "0.001",
        "--no-stale-takeover",
    )
    refused_payload = json.loads(refused.stdout)
    assert refused_payload["claimed"] is False
    assert refused_payload["reason"] == "already claimed"
    assert refused_payload["stale"] is True

    taken = r.core("claim_issue.py", "I-1", "--agent", "agent-a", "--stale-hours", "0.001")
    taken_payload = json.loads(taken.stdout)
    assert taken_payload["claimed"] is True
    assert taken_payload["previous_claim"]["by"] == "other-agent"
    assert _read_json(r.pm / "issues.json")["I-1"]["claimed_by"] == "agent-a"


def test_acquire_lock_succeeds_then_times_out_when_held(tmp_path):
    path = tmp_path / "issues.json"
    path.write_text("{}\n", encoding="utf-8")

    held = C.acquire_lock(path, 0)
    assert held is not None
    fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    held.close()

    ready = tmp_path / "locked.ready"
    locker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import fcntl, pathlib, sys, time
                p = pathlib.Path(sys.argv[1])
                ready = pathlib.Path(sys.argv[2])
                f = p.open("r+", encoding="utf-8")
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                ready.write_text("1", encoding="utf-8")
                time.sleep(10)
                """
            ),
            str(path),
            str(ready),
        ]
    )
    try:
        deadline = time.time() + 5
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        assert C.acquire_lock(path, 0) is None
    finally:
        locker.terminate()
        locker.wait(timeout=5)


def test_do_claim_and_reachability_helpers():
    issues = {
        "DEP": _issue(status="todo", ticket="T-DEP"),
        "TARGET": _issue(ticket="T-TARGET", depends_on=["DEP"]),
    }
    payload, claimed = C.do_claim("TARGET", issues, "agent-a", 3.0, False, None, {})
    assert claimed is False
    assert payload["reason"] == "issue depends_on not all done"
    assert C.issue_deps_met(issues["TARGET"], issues) is False

    tickets = {
        "T-DEP": _ticket(),
        "T-TARGET": _ticket(depends_on=["T-DEP"]),
    }
    tdone = C.ticket_done_map(tickets, issues)
    assert tdone == {"T-DEP": False, "T-TARGET": False}
    assert C.ticket_reachable("T-TARGET", tickets, tdone) is False

    issues["DEP"]["status"] = "done"
    tdone = C.ticket_done_map(tickets, issues)
    assert tdone["T-DEP"] is True
    assert C.ticket_reachable("T-TARGET", tickets, tdone) is True
