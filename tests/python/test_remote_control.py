"""remote_control.py — local CONTROL parsing and file replies."""

import json
import sys

from conftest import REPO_ROOT, write

REMOTE = REPO_ROOT / "skills" / "specseed" / "scripts" / "remote"
sys.path.insert(0, str(REMOTE))

import remote_control as ctl


class FakeRemote:
    def __init__(self, user=None, fail=False):
        self.user = user or {"login": "owner"}
        self.fail = fail

    def whoami(self):
        if self.fail:
            raise RuntimeError("no auth")
        return self.user


def _root(tmp_path):
    (tmp_path / ".specseed" / "memory").mkdir(parents=True)
    (tmp_path / ".specseed" / "project_management").mkdir(parents=True)
    return tmp_path


def test_parse_uses_first_non_empty_line():
    assert ctl._parse("\n approve FEAT-0001 A\nignored") == ("approve", "FEAT-0001 A")
    assert ctl._parse("reject FEAT-0002 needs more") == ("reject", "FEAT-0002 needs more")
    assert ctl._parse("status") == ("status", "")
    assert ctl._parse("dance now") == ("dance", "now")
    assert ctl._parse("\n\n") == (None, "")


def test_allowed_honors_allowlist_and_owner_fallback():
    assert ctl._allowed({"allowlist": ["alice"]}, FakeRemote(), "alice") is True
    assert ctl._allowed({"allowlist": ["alice"]}, FakeRemote(), "bob") is False

    assert ctl._allowed({"allowlist": []}, FakeRemote({"login": "owner"}), "owner") is True
    assert ctl._allowed({"allowlist": []}, FakeRemote({"username": "maint"}), "maint") is True
    assert ctl._allowed({"allowlist": []}, FakeRemote(fail=True), "owner") is False


def test_ctl_file_defaults_and_round_trips(tmp_path):
    root = _root(tmp_path)
    assert ctl.ctl_path(root) == root / ".specseed" / "memory" / "runner.ctl"
    assert ctl.read_ctl(root) == "run"

    ctl.write_ctl(root, "pause")
    assert ctl.read_ctl(root) == "pause"
    assert ctl.ctl_path(root).read_text(encoding="utf-8") == "pause\n"


def test_status_reply_reads_local_json(tmp_path):
    root = _root(tmp_path)
    pm = root / ".specseed" / "project_management"
    write(pm / "issues.json", json.dumps({
        "FEAT-0001": {"id": "FEAT-0001", "status": "in_progress"},
        "BUG-0001": {"id": "BUG-0001", "status": "todo"},
        "BUG-0002": {"id": "BUG-0002", "status": "blocked"},
        "BUG-0003": {"id": "BUG-0003", "status": "done"},
    }))
    write(pm / "sprints.json", json.dumps({
        "SPRINT_1": {"id": "SPRINT_1", "status": "in_progress"},
        "SPRINT_2": {"id": "SPRINT_2", "status": "todo"},
    }))
    ctl.write_ctl(root, "pause")

    out = ctl._status_reply(root, {})
    assert "**status**" in out
    assert "runner: `pause`" in out
    assert "active sprint: SPRINT_1" in out
    assert "in-flight: FEAT-0001" in out
    assert "ready issues: 2" in out


def test_cr_rollup_summarizes_live_crs():
    assert ctl._cr_rollup([]) is None
    assert ctl._cr_rollup([{"id": "CR-1", "status": "done"}]) is None   # terminal only
    line = ctl._cr_rollup([
        {"id": "CR-1", "status": "open", "turn": "human"},
        {"id": "CR-2", "status": "open", "turn": "agent"},
        {"id": "CR-3", "status": "respec_complete", "turn": None},
        {"id": "CR-4", "status": "rejected"},          # excluded (terminal)
    ])
    assert line.startswith("CRs: 3 open (")
    assert "CR-1 awaiting you" in line
    assert "CR-2 in progress" in line
    assert "CR-3 regenerating" in line
    assert "CR-4" not in line


def test_crs_reply_and_status_rollup_read_disk(tmp_path):
    import change_requests as crmod
    root = _root(tmp_path)
    cr_id = crmod.create_cr(root, "Add export", "need CSV", remote_issue=12)
    crmod.set_turn(root, cr_id, "human")

    crs_out = ctl._crs_reply(root)
    assert "**crs**" in crs_out
    assert "`CR-0001` Add export — open" in crs_out
    assert "turn: human" in crs_out
    assert "(#12)" in crs_out

    # the status reply now carries a CR roll-up line
    status_out = ctl._status_reply(root, {})
    assert "CRs: 1 open (CR-0001 awaiting you)" in status_out

    # no CRs → graceful
    assert "none" in ctl._crs_reply(tmp_path / "nope")


def test_approvals_reply_reads_pending_records(tmp_path):
    root = _root(tmp_path)
    pm = root / ".specseed" / "project_management"

    assert "none pending" in ctl._approvals_reply(root)

    write(pm / "approvals.json", json.dumps([
        {"issue": "FEAT-0001", "n": 1, "summary": "Need deploy OK", "kind": "network"},
    ]))
    out = ctl._approvals_reply(root)
    assert "pending HITL gates" in out
    assert "`FEAT-0001` A1: Need deploy OK (network)" in out
    assert "approve FEAT-0001" in out
    assert "reject FEAT-0001 <note>" in out
