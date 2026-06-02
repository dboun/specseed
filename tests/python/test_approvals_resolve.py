import json
import sys

import pytest

import approvals_resolve as R
from conftest import write


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O)
# --------------------------------------------------------------------------- #

def test_decide_status_table():
    # completion gate (entity-approval)
    assert R.decide_status("entity-approval", "approve", False) == ("done", "clear")
    assert R.decide_status("entity-approval", "reject", False) == ("in_progress", "keep")
    assert R.decide_status("entity-approval", "hold", False) == ("blocked", "keep")
    # action gates
    assert R.decide_status("gate:network", "approve", False) == ("todo", "clear")
    assert R.decide_status("gate:network", "reject", False) == ("blocked", "clear")
    assert R.decide_status("gate:network", "reject", True) == ("wont_do", "clear")
    assert R.decide_status("run-action", "approve", False) == ("todo", "clear")
    assert R.decide_status("handoff", "hold", False) == ("blocked", "keep")
    assert R.decide_status("git-conflict", "reject", False) == ("blocked", "clear")


def test_is_completion():
    assert R.is_completion("entity-approval")
    assert R.is_completion(" Entity-Approval ")
    assert not R.is_completion("gate:network")
    assert not R.is_completion("")


# --------------------------------------------------------------------------- #
# Addressing
# --------------------------------------------------------------------------- #

def _gate(n, kind, status="open", **extra):
    L = [f"## A{n} — {kind} gate",
         "- **Opened:** 2026-06-01T00:00:00Z",
         f"- **Kind:** {kind}",
         f"- **Status:** {status}"]
    for k, v in extra.items():
        L.append(f"- **{k}:** {v}")
    return "\n".join(L) + "\n"


def _set_issue(repo, iid, **fields):
    p = repo.pm / "issues.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    data.setdefault(iid, {"id": iid})
    data[iid].update(fields)
    p.write_text(json.dumps(data, indent=2) + "\n")


def test_resolve_target_by_apr(repo):
    write(repo.pm / "issues" / "FEAT-0001" / "approval.md", _gate(1, "gate:network"))
    repo.core("approvals_render.py")               # stamps APR-0001
    issue, entry, err = R.resolve_target(repo.pm, "APR-0001")
    assert err is None and issue == "FEAT-0001" and entry["n"] == 1


def test_resolve_target_by_issue_id_single(repo):
    write(repo.pm / "issues" / "FEAT-0001" / "approval.md", _gate(1, "gate:network"))
    repo.core("approvals_render.py")
    issue, entry, err = R.resolve_target(repo.pm, "FEAT-0001")
    assert err is None and issue == "FEAT-0001"


def test_resolve_target_ambiguous_issue_id(repo):
    write(repo.pm / "issues" / "FEAT-0001" / "approval.md",
          _gate(1, "gate:network") + "\n" + _gate(2, "gate:deps"))
    repo.core("approvals_render.py")
    issue, entry, err = R.resolve_target(repo.pm, "FEAT-0001")
    assert issue is None and "multiple open gates" in err


def test_resolve_target_unknown(repo):
    issue, entry, err = R.resolve_target(repo.pm, "APR-9999")
    assert issue is None and "unknown id" in err


# --------------------------------------------------------------------------- #
# End-to-end via the CLI (the single mutation path)
# --------------------------------------------------------------------------- #

def _setup(repo, approval_text, status="awaiting_approval"):
    """Lay down one issue's approval.md + an assembled issues.json, park the issue,
    and stamp APR ids. Returns the approvals_render output already run."""
    write(repo.pm / "issues" / "FEAT-0001" / "approval.md", approval_text)
    repo.assemble()                                # issues.json from the folder tree
    _set_issue(repo, "FEAT-0001", status=status,
               claimed_by="agent-1", claimed_at="2026-06-01T00:00:00Z")
    repo.core("approvals_render.py")               # stamp APR-0001…


def test_approve_action_gate_to_todo_clears_claim(repo):
    _setup(repo, _gate(1, "gate:network"))
    res = repo.core("approvals_resolve.py", "APR-0001", "approve",
                    "--pm-dir", str(repo.pm))
    assert res.returncode == 0, res.stderr
    iss = repo.issues()["FEAT-0001"]
    assert iss["status"] == "todo"
    assert iss["claimed_by"] is None and iss["claimed_at"] is None
    # resolved marker appended, original untouched
    af = (repo.pm / "issues" / "FEAT-0001" / "approval.md").read_text()
    assert "## Resolved A1" in af and "approve" in af
    # index dropped the resolved entry
    assert json.loads((repo.pm / "approvals.json").read_text()) == []


def test_approve_completion_gate_to_done_and_rollup(repo):
    _setup(repo, _gate(1, "entity-approval"))
    res = repo.core("approvals_resolve.py", "APR-0001", "approve",
                    "--pm-dir", str(repo.pm))
    assert res.returncode == 0, res.stderr
    iss = repo.issues()["FEAT-0001"]
    assert iss["status"] == "done"
    assert iss["claimed_by"] is None
    # ticket rolled up (tickets_assemble ran on a completion close)
    tickets = json.loads((repo.pm / "tickets.json").read_text())
    assert tickets["PROJ-0001"]["status"] == "done"


def test_reject_completion_gate_to_in_progress_keeps_claim(repo):
    _setup(repo, _gate(1, "entity-approval"))
    res = repo.core("approvals_resolve.py", "APR-0001", "reject",
                    "--note", "needs tests", "--pm-dir", str(repo.pm))
    assert res.returncode == 0, res.stderr
    iss = repo.issues()["FEAT-0001"]
    assert iss["status"] == "in_progress"
    assert iss["claimed_by"] == "agent-1"          # changes requested → claim kept
    assert "needs tests" in \
        (repo.pm / "issues" / "FEAT-0001" / "approval.md").read_text()


def test_reject_action_gate_default_blocked(repo):
    _setup(repo, _gate(1, "gate:data_destructive"))
    res = repo.core("approvals_resolve.py", "APR-0001", "reject",
                    "--note", "too risky", "--pm-dir", str(repo.pm))
    assert res.returncode == 0, res.stderr
    iss = repo.issues()["FEAT-0001"]
    assert iss["status"] == "blocked"
    assert iss["claimed_by"] is None


def test_reject_action_gate_wont_do(repo):
    _setup(repo, _gate(1, "gate:external_publish"))
    res = repo.core("approvals_resolve.py", "APR-0001", "reject", "--wont-do",
                    "--pm-dir", str(repo.pm))
    assert res.returncode == 0, res.stderr
    assert repo.issues()["FEAT-0001"]["status"] == "wont_do"


def test_hold_to_blocked_keeps_claim(repo):
    _setup(repo, _gate(1, "gate:network"))
    res = repo.core("approvals_resolve.py", "APR-0001", "hold",
                    "--note", "not now", "--pm-dir", str(repo.pm))
    assert res.returncode == 0, res.stderr
    iss = repo.issues()["FEAT-0001"]
    assert iss["status"] == "blocked"
    assert iss["claimed_by"] == "agent-1"


def test_last_gate_rule(repo):
    _setup(repo, _gate(1, "gate:network") + "\n" + _gate(2, "gate:deps"))
    # first resolve does NOT flip the issue (a sibling stays open)
    r1 = repo.core("approvals_resolve.py", "APR-0001", "approve",
                   "--pm-dir", str(repo.pm))
    assert r1.returncode == 0, r1.stderr
    info1 = json.loads(r1.stdout.strip().splitlines()[-1])
    assert info1["flipped"] is False
    assert info1["open_siblings"] == ["APR-0002"]
    assert repo.issues()["FEAT-0001"]["status"] == "awaiting_approval"
    # second resolve is the last open gate → flips
    r2 = repo.core("approvals_resolve.py", "APR-0002", "approve",
                   "--pm-dir", str(repo.pm))
    assert r2.returncode == 0, r2.stderr
    assert json.loads(r2.stdout.strip().splitlines()[-1])["flipped"] is True
    assert repo.issues()["FEAT-0001"]["status"] == "todo"


def test_status_flip_failure_leaves_gate_open(monkeypatch, repo):
    _setup(repo, _gate(1, "gate:network"))
    monkeypatch.setattr(R, "apply_status", lambda *a, **k: (False, None))
    monkeypatch.setattr(sys, "argv", [
        "approvals_resolve.py", "APR-0001", "approve", "--pm-dir", str(repo.pm),
    ])

    with pytest.raises(SystemExit) as e:
        R.main()

    assert e.value.code == 2
    af = (repo.pm / "issues" / "FEAT-0001" / "approval.md").read_text()
    assert "## Resolved A1" not in af
    assert repo.issues()["FEAT-0001"]["status"] == "awaiting_approval"
    assert [r["apr"] for r in R.A.collect(repo.pm)] == ["APR-0001"]


def test_side_effect_failures_are_reported_as_warnings(monkeypatch, repo):
    _setup(repo, _gate(1, "entity-approval"))

    def fake_run(script, pm_dir, root):
        if script == "tickets_assemble.py":
            return False, "tickets_assemble.py failed: boom"
        return True, ""

    monkeypatch.setattr(R, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", [
        "approvals_resolve.py", "APR-0001", "approve", "--pm-dir", str(repo.pm),
    ])

    with pytest.raises(SystemExit) as e:
        R.main()

    assert e.value.code == 0
    assert repo.issues()["FEAT-0001"]["status"] == "done"


def test_unknown_id_no_mutation(repo):
    _setup(repo, _gate(1, "gate:network"))
    before = repo.issues()["FEAT-0001"]["status"]
    res = repo.core("approvals_resolve.py", "APR-9999", "approve",
                    "--pm-dir", str(repo.pm))
    assert res.returncode == 2
    assert "unknown id" in res.stderr
    assert repo.issues()["FEAT-0001"]["status"] == before


def test_already_resolved_no_mutation(repo):
    _setup(repo, _gate(1, "gate:network"))
    repo.core("approvals_resolve.py", "APR-0001", "approve", "--pm-dir", str(repo.pm))
    # a second resolve of the same id fails (already resolved)
    res = repo.core("approvals_resolve.py", "APR-0001", "reject", "--pm-dir", str(repo.pm))
    assert res.returncode == 2
    assert "already resolved" in res.stderr


def test_run_verify_pass_approves(repo):
    _setup(repo, _gate(1, "handoff", Verify="test -f marker.txt"))
    (repo.root / "marker.txt").write_text("ok\n")   # verify command will pass
    res = repo.core("approvals_resolve.py", "APR-0001", "approve", "--run-verify",
                    "--pm-dir", str(repo.pm))
    assert res.returncode == 0, res.stderr
    assert repo.issues()["FEAT-0001"]["status"] == "todo"


def test_run_verify_fail_refuses(repo):
    _setup(repo, _gate(1, "handoff", Verify="test -f marker.txt"))
    # marker.txt absent → verify fails → refuse, gate left open
    res = repo.core("approvals_resolve.py", "APR-0001", "approve", "--run-verify",
                    "--pm-dir", str(repo.pm))
    assert res.returncode == 2
    assert "verify failed" in res.stderr
    assert repo.issues()["FEAT-0001"]["status"] == "awaiting_approval"
    # entry still open in the index
    recs = json.loads((repo.pm / "approvals.json").read_text())
    assert [r["apr"] for r in recs] == ["APR-0001"]
