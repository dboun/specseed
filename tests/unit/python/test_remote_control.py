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


# --------------------------------------------------------------------------- #
# Phase 3 — per-issue comment intake + repo-wide dispatch + cursor watermark
# --------------------------------------------------------------------------- #
class ProcessRemote:
    """Stand-in supporting the repo-wide poll: comments_since + comment + whoami."""

    def __init__(self, comments, user=None):
        self._comments = comments
        self.user = user or {"login": "owner"}
        self.posted = []          # (number, body)

    def comments_since(self, since):
        return list(self._comments)

    def comment(self, n, body):
        self.posted.append((n, body))

    def whoami(self):
        return self.user


def _comment(num, body, author="owner", ts="2026-01-01T00:00:00Z", cid=None):
    out = {"issue_number": num, "body": body, "author": author, "created_at": ts}
    if cid is not None:
        out["id"] = cid
    return out


def _cfg(control=100, mapping=None, allow=None, cursor=None):
    return {"permanent": {"control": control},
            "map": mapping or {}, "allowlist": allow if allow is not None else [],
            "cli_cursor": cursor}


def _write_approvals(root, records):
    write(root / ".specseed" / "project_management" / "approvals.json",
          json.dumps(records))


def test_open_aprs_reads_index(tmp_path):
    root = _root(tmp_path)
    assert ctl._open_aprs(root, "FEAT-0001") == []          # no file -> empty
    _write_approvals(root, [
        {"issue": "FEAT-0001", "n": 1, "apr": "APR-0001"},
        {"issue": "FEAT-0001", "n": 2, "apr": "APR-0002"},
        {"issue": "BUG-0001", "n": 1},                      # no apr -> A<n> fallback
    ])
    assert ctl._open_aprs(root, "FEAT-0001") == ["APR-0001", "APR-0002"]
    assert ctl._open_aprs(root, "BUG-0001") == ["A1"]


def test_work_action_text_addressing(tmp_path):
    root = _root(tmp_path)
    _write_approvals(root, [{"issue": "FEAT-0001", "n": 1, "apr": "APR-0001"}])
    # explicit APR id -> passed through verbatim (resolver parses the handle)
    assert ctl._work_action_text(root, "FEAT-0001", "APR-0001 looks good") == \
        "APR-0001 looks good"
    # single open gate, no id -> bare issue id (resolver picks the one gate)
    assert ctl._work_action_text(root, "FEAT-0001", "") == "FEAT-0001"
    assert ctl._work_action_text(root, "FEAT-0001", "ship it") == "FEAT-0001 ship it"
    # >1 open gate, no id -> ambiguous
    _write_approvals(root, [
        {"issue": "FEAT-0001", "n": 1, "apr": "APR-0001"},
        {"issue": "FEAT-0001", "n": 2, "apr": "APR-0002"},
    ])
    assert ctl._work_action_text(root, "FEAT-0001", "") is None


def test_watermark_holds_below_earliest_pending_action():
    # no actions -> advance to the newest handled comment
    assert ctl._watermark(None, [ctl._mark("t1", ""), ctl._mark("t3", ""),
                                 ctl._mark("t2", "")], []) == ("t3", [])
    # an action pending at t2 -> never advance past it; t3 mark is held back
    wm = ctl._watermark(None, [ctl._mark("t1", ""), ctl._mark("t3", "")],
                        [{"created_at": "t2"}])
    assert wm == ("t1", [])
    # never regress below the incoming cursor
    assert ctl._watermark("t5", [ctl._mark("t1", "")], []) == ("t5", [])
    # all empty -> None
    assert ctl._watermark(None, [ctl._mark("", "")], []) == (None, [])


def test_watermark_keeps_same_second_success_ids_before_pending_action():
    marks = [ctl._mark("2026-01-01T00:00:00Z", "10")]
    actions = [{"created_at": "2026-01-01T00:00:00Z", "id": "11"}]
    assert ctl._watermark(None, marks, actions) == ("2026-01-01T00:00:00Z", ["10"])


def test_process_work_issue_approve_routes_to_resolver(tmp_path):
    root = _root(tmp_path)
    _write_approvals(root, [{"issue": "FEAT-0001", "n": 1, "apr": "APR-0007"}])
    cfg = _cfg(mapping={"FEAT-0001": {"n": 42}})
    remote = ProcessRemote([_comment(42, "approve APR-0007", ts="t2")])

    actions, cfg = ctl.process(root, cfg, remote)
    assert actions == [{"verb": "approve", "text": "APR-0007",
                        "reply_to": 42, "created_at": "t2"}]
    assert remote.posted == []                       # the runner replies after resolving
    # cursor held below the pending action so a later failure can retry it
    assert cfg["cli_cursor"] is None


def test_process_work_issue_rejects_apr_from_other_issue(tmp_path):
    root = _root(tmp_path)
    _write_approvals(root, [
        {"issue": "FEAT-0001", "n": 1, "apr": "APR-0001"},
        {"issue": "FEAT-0002", "n": 1, "apr": "APR-0002"},
    ])
    cfg = _cfg(mapping={"FEAT-0001": {"n": 42}, "FEAT-0002": {"n": 43}})
    remote = ProcessRemote([_comment(42, "approve APR-0002", ts="t2")])

    actions, cfg = ctl.process(root, cfg, remote)
    assert actions == []
    assert remote.posted[0][0] == 42
    assert "not an open gate on `FEAT-0001`" in remote.posted[0][1]
    assert cfg["cli_cursor"] == "t2"


def test_process_ambiguous_work_approve_lists_aprs_no_action(tmp_path):
    root = _root(tmp_path)
    _write_approvals(root, [
        {"issue": "FEAT-0001", "n": 1, "apr": "APR-0001"},
        {"issue": "FEAT-0001", "n": 2, "apr": "APR-0002"},
    ])
    cfg = _cfg(mapping={"FEAT-0001": {"n": 42}})
    remote = ProcessRemote([_comment(42, "approve", ts="t2")])

    actions, cfg = ctl.process(root, cfg, remote)
    assert actions == []
    assert len(remote.posted) == 1
    n, body = remote.posted[0]
    assert n == 42 and "APR-0001" in body and "APR-0002" in body
    assert cfg["cli_cursor"] == "t2"                 # handled in-process -> cursor advances


def test_process_unauthorized_on_work_issue(tmp_path):
    root = _root(tmp_path)
    _write_approvals(root, [{"issue": "FEAT-0001", "n": 1, "apr": "APR-0001"}])
    cfg = _cfg(mapping={"FEAT-0001": {"n": 42}}, allow=["alice"])
    remote = ProcessRemote([_comment(42, "approve APR-0001", author="mallory", ts="t2")])

    actions, cfg = ctl.process(root, cfg, remote)
    assert actions == []
    assert remote.posted[0][0] == 42
    assert "not authorized" in remote.posted[0][1]
    assert cfg["cli_cursor"] == "t2"                 # advances past the rejected comment


def test_process_freeform_work_comment_lands_in_inbox(tmp_path):
    import inbox
    root = _root(tmp_path)
    (root / ".specseed" / "project_management" / "issues" / "FEAT-0001").mkdir(parents=True)
    cfg = _cfg(mapping={"FEAT-0001": {"n": 42}}, allow=["alice"])
    remote = ProcessRemote([_comment(42, "can you also add logging?",
                                     author="alice", ts="t2")])

    actions, cfg = ctl.process(root, cfg, remote)
    assert actions == []
    # the comment is appended to the issue's inbox as a human entry...
    pm = root / ".specseed" / "project_management"
    pend = inbox.unprocessed(pm, "FEAT-0001")
    assert len(pend) == 1
    assert pend[0]["author"] == "alice"
    assert pend[0]["body"] == "can you also add logging?"
    # ...and a single 📝 ack is posted (bot-prefixed so it never re-ingests)
    assert remote.posted[0][0] == 42
    assert remote.posted[0][1].startswith("📝")
    assert "queued" in remote.posted[0][1]
    assert cfg["cli_cursor"] == "t2"                 # captured in-process → cursor advances


def test_process_control_status_still_inline(tmp_path):
    root = _root(tmp_path)
    write(root / ".specseed" / "project_management" / "issues.json", json.dumps({}))
    write(root / ".specseed" / "project_management" / "sprints.json", json.dumps({}))
    cfg = _cfg(control=100)
    remote = ProcessRemote([_comment(100, "status", ts="t2")])

    actions, cfg = ctl.process(root, cfg, remote)
    assert actions == []
    assert remote.posted[0][0] == 100
    assert "**status**" in remote.posted[0][1]
    assert cfg["cli_cursor"] == "t2"


def test_process_unmapped_issue_ignored(tmp_path):
    root = _root(tmp_path)
    cfg = _cfg(mapping={"FEAT-0001": {"n": 42}})
    remote = ProcessRemote([_comment(999, "approve APR-0001", ts="t2")])   # no map entry

    actions, cfg = ctl.process(root, cfg, remote)
    assert actions == []
    assert remote.posted == []                       # silently skipped (ROADMAP/CR/etc)
    assert cfg["cli_cursor"] == "t2"


def test_process_skips_bot_echo_and_old_comments(tmp_path):
    root = _root(tmp_path)
    cfg = _cfg(control=100, cursor="t1")
    remote = ProcessRemote([
        _comment(100, "status", ts="t1"),           # <= cursor -> skipped
        _comment(100, "✅ Resolved APR-0001: done.", ts="t2"),  # bot echo -> skipped
    ])
    write(root / ".specseed" / "project_management" / "issues.json", json.dumps({}))
    write(root / ".specseed" / "project_management" / "sprints.json", json.dumps({}))

    actions, cfg = ctl.process(root, cfg, remote)
    assert actions == []
    assert remote.posted == []
    assert cfg["cli_cursor"] == "t2"                 # echo counts as handled


def test_process_control_work_verb_returned_holds_cursor(tmp_path):
    root = _root(tmp_path)
    cfg = _cfg(control=100)
    remote = ProcessRemote([
        _comment(100, "adapt add OAuth", ts="t2"),
        _comment(100, "✅ Ran `adapt`.", ts="t3"),    # later bot echo
    ])
    actions, cfg = ctl.process(root, cfg, remote)
    assert actions == [{"verb": "adapt", "text": "add OAuth",
                        "reply_to": 100, "created_at": "t2"}]
    # the later echo (t3) is NOT allowed to advance the cursor past the pending action
    assert cfg["cli_cursor"] is None


def test_process_same_second_failed_action_remains_visible_next_pass(tmp_path):
    root = _root(tmp_path)
    cfg = _cfg(control=100, mapping={"FEAT-0001": {"n": 42}})
    _write_approvals(root, [{"issue": "FEAT-0001", "n": 1, "apr": "APR-0001"}])
    remote = ProcessRemote([
        _comment(42, "approve APR-0001", ts="2026-01-01T00:00:00Z", cid=10),
        _comment(42, "approve APR-0001", ts="2026-01-01T00:00:00Z", cid=11),
    ])

    actions, cfg = ctl.process(root, cfg, remote)
    assert [a["id"] for a in actions] == ["10", "11"]
    # Simulate runner advancing only the first same-second action after success.
    cfg["cli_cursor"] = "2026-01-01T00:00:00Z"
    cfg["cli_cursor_ids"] = ["10"]

    actions, cfg = ctl.process(root, cfg, remote)
    assert [a["id"] for a in actions] == ["11"]
