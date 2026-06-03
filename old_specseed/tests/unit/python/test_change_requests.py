"""change_requests.py + add_change_request.py — CR entity I/O + local intake.

Pure local, no agent, no network — zero tokens.
"""

import json

import pytest

import change_requests as cr
from conftest import run_scripts


def _mk_root(tmp_path):
    """A bare specseed root (just the .specseed dir — a CR needs no work layer)."""
    (tmp_path / ".specseed").mkdir()
    return tmp_path


# --------------------------------------------------------------------------- #
# create / next_cr_id
# --------------------------------------------------------------------------- #
def test_create_writes_parseable_folder(tmp_path):
    root = _mk_root(tmp_path)
    cr_id = cr.create_cr(root, "Switch to OAuth", "drop passwords")
    assert cr_id == "CR-0001"
    f = cr.cr_dir(root) / "CR-0001" / "cr.md"
    assert f.exists()
    loaded = cr.load_cr(root, "CR-0001")
    assert loaded["id"] == "CR-0001"
    assert loaded["title"] == "Switch to OAuth"
    assert loaded["status"] == "open"
    assert loaded["turn"] == "agent"
    assert loaded["priority"] == "urgent"
    assert loaded["request"] == "drop passwords"


def test_next_cr_id_increments(tmp_path):
    root = _mk_root(tmp_path)
    assert cr.create_cr(root, "a", "x") == "CR-0001"
    assert cr.create_cr(root, "b", "y") == "CR-0002"
    assert cr.create_cr(root, "c", "z") == "CR-0003"


# --------------------------------------------------------------------------- #
# round-trip
# --------------------------------------------------------------------------- #
def test_round_trip_null_and_int_fields_survive(tmp_path):
    root = _mk_root(tmp_path)
    cr.create_cr(root, "T", "body", remote_issue=42)
    loaded = cr.load_cr(root, "CR-0001")
    # nullable bookkeeping fields default to None and round-trip as None (not "null")
    assert loaded["session_id"] is None
    assert loaded["branch"] is None
    assert loaded["comment_cursor"] is None
    # an int remote_issue round-trips as an int (json.loads), not a string
    assert loaded["remote_issue"] == 42
    assert isinstance(loaded["remote_issue"], int)


def test_multiline_request_body_survives(tmp_path):
    root = _mk_root(tmp_path)
    body = "line one\nline two\nline three"
    cr.create_cr(root, "T", body)
    assert cr.load_cr(root, "CR-0001")["request"] == body


def test_request_body_with_markdown_headers_survives(tmp_path):
    # an ingested remote issue body may contain `## ...` / `### ...` lines; only the
    # exact `## Request` / `## Log` headers delimit sections, so these stay content.
    root = _mk_root(tmp_path)
    body = "## Background\nsome context\n### Details\nmore text"
    cr.create_cr(root, "T", body)
    assert cr.load_cr(root, "CR-0001")["request"] == body


# --------------------------------------------------------------------------- #
# kind (change vs bootstrap)
# --------------------------------------------------------------------------- #
def test_kind_defaults_to_change(tmp_path):
    root = _mk_root(tmp_path)
    cr.create_cr(root, "T", "b")
    assert cr.load_cr(root, "CR-0001")["kind"] == "change"


def test_kind_bootstrap_round_trips(tmp_path):
    root = _mk_root(tmp_path)
    cr.create_cr(root, "Build the thing", "a CLI todo app", kind="bootstrap")
    loaded = cr.load_cr(root, "CR-0001")
    assert loaded["kind"] == "bootstrap"
    assert cr.list_crs(root)[0]["kind"] == "bootstrap"


def test_bad_kind_rejected(tmp_path):
    root = _mk_root(tmp_path)
    with pytest.raises(ValueError):
        cr.create_cr(root, "T", "b", kind="sideways")


def test_legacy_record_without_kind_loads_as_change(tmp_path):
    # a cr.md written before `kind` existed (no kind: line) must still load.
    root = _mk_root(tmp_path)
    d = cr.cr_dir(root) / "CR-0001"
    d.mkdir(parents=True)
    (d / "cr.md").write_text(
        "---\nid: CR-0001\ntitle: Legacy\nstatus: open\nturn: agent\n"
        "priority: urgent\ncreated_at: 2026-01-01T00:00:00Z\nsession_id: null\n"
        "branch: null\nremote_issue: null\ncomment_cursor: null\n---\n"
        "## Request\nold body\n\n## Log\n", encoding="utf-8")
    assert cr.load_cr(root, "CR-0001")["kind"] == "change"


# --------------------------------------------------------------------------- #
# field setters
# --------------------------------------------------------------------------- #
def test_set_status_mutates_only_its_field(tmp_path):
    root = _mk_root(tmp_path)
    cr.create_cr(root, "T", "b", remote_issue=7)
    cr.set_status(root, "CR-0001", "respec_complete", turn=None)
    loaded = cr.load_cr(root, "CR-0001")
    assert loaded["status"] == "respec_complete"
    assert loaded["turn"] is None
    # everything else intact
    assert loaded["title"] == "T"
    assert loaded["remote_issue"] == 7
    assert loaded["request"] == "b"


def test_set_status_keeps_turn_by_default(tmp_path):
    root = _mk_root(tmp_path)
    cr.create_cr(root, "T", "b")  # turn == agent
    cr.set_status(root, "CR-0001", "done")
    assert cr.load_cr(root, "CR-0001")["turn"] == "agent"


def test_set_session_and_branch(tmp_path):
    root = _mk_root(tmp_path)
    cr.create_cr(root, "T", "b")
    cr.set_session(root, "CR-0001", "abc-123-uuid")
    cr.set_branch(root, "CR-0001", "cr/CR-0001")
    loaded = cr.load_cr(root, "CR-0001")
    assert loaded["session_id"] == "abc-123-uuid"
    assert loaded["branch"] == "cr/CR-0001"


def test_advance_cursor_and_append_log(tmp_path):
    root = _mk_root(tmp_path)
    cr.create_cr(root, "T", "b")
    cr.advance_cursor(root, "CR-0001", "2026-06-02T10:00:00Z")
    cr.append_log(root, "CR-0001", "entered respec mode")
    loaded = cr.load_cr(root, "CR-0001")
    assert loaded["comment_cursor"] == "2026-06-02T10:00:00Z"
    assert "entered respec mode" in loaded["log"]


# --------------------------------------------------------------------------- #
# enum validation
# --------------------------------------------------------------------------- #
def test_bad_status_rejected(tmp_path):
    root = _mk_root(tmp_path)
    cr.create_cr(root, "T", "b")
    with pytest.raises(ValueError):
        cr.set_status(root, "CR-0001", "bogus")


def test_bad_turn_rejected(tmp_path):
    root = _mk_root(tmp_path)
    cr.create_cr(root, "T", "b")
    with pytest.raises(ValueError):
        cr.set_turn(root, "CR-0001", "sideways")


# --------------------------------------------------------------------------- #
# list_crs FIFO
# --------------------------------------------------------------------------- #
def test_list_crs_fifo_by_created_at(tmp_path):
    root = _mk_root(tmp_path)
    # write two CRs with controlled stamps (out of folder-name order to prove the sort)
    cr.create_cr(root, "first", "x")
    cr.create_cr(root, "second", "y")
    c1 = cr.load_cr(root, "CR-0001")
    c1["created_at"] = "2026-06-02T12:00:00Z"
    cr.save_cr(root, c1)
    c2 = cr.load_cr(root, "CR-0002")
    c2["created_at"] = "2026-06-02T08:00:00Z"  # earlier
    cr.save_cr(root, c2)

    listed = cr.list_crs(root)
    assert [c["id"] for c in listed] == ["CR-0002", "CR-0001"]  # FIFO by created_at
    assert listed[0]["title"] == "second"
    assert set(listed[0]) >= {"id", "status", "turn", "priority", "created_at", "remote_issue"}


# --------------------------------------------------------------------------- #
# add_change_request.py end to end (subprocess)
# --------------------------------------------------------------------------- #
def test_add_change_request_cli(tmp_path):
    root = _mk_root(tmp_path)
    res = run_scripts("add_change_request.py", "--title", "Switch to OAuth",
                      "--desc", "drop passwords", cwd=root)
    assert res.returncode == 0, res.stderr
    assert "CR-0001" in res.stdout
    assert (cr.cr_dir(root) / "CR-0001" / "cr.md").exists()

    # a second run increments
    res2 = run_scripts("add_change_request.py", "--title", "Add billing",
                       "--desc", "stripe", cwd=root)
    assert res2.returncode == 0, res2.stderr
    assert "CR-0002" in res2.stdout


def test_add_change_request_no_specseed(tmp_path):
    res = run_scripts("add_change_request.py", "--title", "x", "--desc", "y", cwd=tmp_path)
    assert res.returncode == 2
    assert "ERROR" in res.stderr
