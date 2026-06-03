"""inbox.py — per-issue instruction inbox: parse / append / cursor (pure I/O)."""

import inbox


def _pm(tmp_path):
    pm = tmp_path / ".specseed" / "project_management"
    (pm / "issues" / "FEAT-0001").mkdir(parents=True)
    return pm


def test_append_creates_header_and_increments_seq(tmp_path):
    pm = _pm(tmp_path)
    assert inbox.append_entry(pm, "FEAT-0001", "alice", "add more comments") == 1
    assert inbox.append_entry(pm, "FEAT-0001", "alice", "also rename foo") == 2

    text = inbox.inbox_path(pm, "FEAT-0001").read_text(encoding="utf-8")
    assert text.startswith("# Inbox: FEAT-0001\n")
    assert "### IN-1 —" in text and "— alice" in text
    assert "add more comments" in text
    assert "### IN-2 —" in text


def test_parse_entries_human_and_agent_with_refs(tmp_path):
    pm = _pm(tmp_path)
    inbox.append_entry(pm, "FEAT-0001", "alice", "why did you do X?")
    inbox.append_entry(pm, "FEAT-0001", "bob", "tighten the loop")
    # an agent reply addressing both
    inbox.append_entry(pm, "FEAT-0001", "agent", "Answered + tightened.", re_seqs=[1, 2])

    entries = inbox.read_entries(pm, "FEAT-0001")
    assert [e["seq"] for e in entries] == [1, 2, 3]
    assert entries[0]["author"] == "alice" and entries[0]["is_agent"] is False
    assert entries[0]["body"] == "why did you do X?"
    a = entries[2]
    assert a["is_agent"] is True
    assert a["re"] == [1, 2]
    assert a["body"] == "Answered + tightened."


def test_round_trip_is_idempotent(tmp_path):
    pm = _pm(tmp_path)
    inbox.append_entry(pm, "FEAT-0001", "alice", "line one\nline two")
    text1 = inbox.inbox_path(pm, "FEAT-0001").read_text(encoding="utf-8")
    parsed = inbox.parse_entries(text1)
    assert parsed[0]["body"] == "line one\nline two"
    # re-parsing the same text yields the same structure
    assert inbox.parse_entries(text1) == parsed


def test_cursor_read_write_default_zero(tmp_path):
    pm = _pm(tmp_path)
    assert inbox.read_cursor(pm, "FEAT-0001") == 0          # no state file
    inbox.write_cursor(pm, "FEAT-0001", 4)
    assert inbox.read_cursor(pm, "FEAT-0001") == 4
    assert inbox.state_path(pm, "FEAT-0001").read_text(encoding="utf-8") == \
        "processed_through: IN-4\n"


def test_unprocessed_filters_agent_and_below_cursor(tmp_path):
    pm = _pm(tmp_path)
    inbox.append_entry(pm, "FEAT-0001", "alice", "one")      # IN-1
    inbox.append_entry(pm, "FEAT-0001", "alice", "two")      # IN-2
    inbox.append_entry(pm, "FEAT-0001", "agent", "did it", re_seqs=[1, 2])  # IN-3

    # nothing processed yet → both human asks pending, agent reply excluded
    pend = inbox.unprocessed(pm, "FEAT-0001")
    assert [e["seq"] for e in pend] == [1, 2]

    # advance the cursor to the snapshot max → nothing pending (agent IN-3 still skipped)
    inbox.write_cursor(pm, "FEAT-0001", 2)
    assert inbox.unprocessed(pm, "FEAT-0001") == []


def test_advance_after_success_leaves_late_arrival_unprocessed(tmp_path):
    """Cursor discipline: a batch of 2 is processed and the cursor moves to the snapshot
    max (IN-2); a 3rd human comment that lands during 'processing' is still pending."""
    pm = _pm(tmp_path)
    inbox.append_entry(pm, "FEAT-0001", "alice", "one")      # IN-1
    inbox.append_entry(pm, "FEAT-0001", "alice", "two")      # IN-2
    batch = inbox.unprocessed(pm, "FEAT-0001")
    assert [e["seq"] for e in batch] == [1, 2]

    # a comment arrives mid-turn
    inbox.append_entry(pm, "FEAT-0001", "carol", "three")    # IN-3

    # the runner records its reply then advances to the snapshot max (NOT newest)
    inbox.append_entry(pm, "FEAT-0001", "agent", "handled 1+2", re_seqs=[1, 2])
    inbox.write_cursor(pm, "FEAT-0001", max(e["seq"] for e in batch))

    pend = inbox.unprocessed(pm, "FEAT-0001")
    assert [e["seq"] for e in pend] == [3]                   # the late human ask remains


def test_unprocessed_empty_when_no_file(tmp_path):
    pm = _pm(tmp_path)
    assert inbox.unprocessed(pm, "FEAT-0001") == []
    assert inbox.read_entries(pm, "FEAT-0001") == []
