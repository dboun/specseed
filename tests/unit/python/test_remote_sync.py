"""remote_sync.py — pure local mirror helpers."""

import json
import sys

from conftest import REPO_ROOT, write, fm_issue

REMOTE = REPO_ROOT / "skills" / "specseed" / "scripts" / "remote"
sys.path.insert(0, str(REMOTE))

import remote_sync as rs


def _pm_root(tmp_path):
    (tmp_path / ".specseed" / "project_management").mkdir(parents=True)
    return tmp_path


def _tiny_work_tree(tmp_path):
    root = _pm_root(tmp_path)
    pm = root / ".specseed" / "project_management"
    write(pm / "epics" / "EPIC-0001" / "EPIC-0001.md",
          "---\nid: EPIC-0001\ntitle: Launch\nstatus: todo\n"
          'tickets: ["PROJ-0001"]\n---\nEpic body.\n')
    write(pm / "tickets" / "PROJ-0001" / "PROJ-0001.md",
          "---\nid: PROJ-0001\ntitle: Base ticket\nepic: EPIC-0001\n"
          "type: feature\npriority: high\nstatus: todo\n"
          'depends_on: []\nsatisfies_reqs: ["SRS-001"]\nissues: ["FEAT-0001"]\n'
          "sprint: SPRINT_2026_W01_A\n---\nTicket body.\n")
    write(pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
          fm_issue("FEAT-0001", "PROJ-0001"))
    write(pm / "issues" / "BUG-0001" / "BUG-0001.md",
          fm_issue("BUG-0001", "PROJ-0001", type="bug", title="Existing bug"))
    write(pm / "tickets.json", json.dumps({
        "PROJ-0001": {
            "id": "PROJ-0001", "title": "Base ticket", "status": "todo",
            "epic": "EPIC-0001", "issues": ["FEAT-0001"], "depends_on": [],
            "sprint": "SPRINT_2026_W01_A",
            "issues_done": 0, "issues_total": 1,
        },
    }))
    write(pm / "issues.json", json.dumps({
        "FEAT-0001": {
            "id": "FEAT-0001", "title": "Feature issue", "status": "in_progress",
            "ticket": "PROJ-0001", "claimed_by": "alice",
        },
        "BUG-0001": {
            "id": "BUG-0001", "title": "Existing bug", "status": "todo",
            "ticket": "PROJ-0001", "claimed_by": None,
        },
    }))
    write(pm / "sprints.json", json.dumps({
        "SPRINT_2026_W01_A": {
            "id": "SPRINT_2026_W01_A", "title": "Foundations",
            "status": "in_progress", "starts": "2026-06-01",
            "ends": "2026-06-07", "tickets": ["PROJ-0001"],
        },
    }))
    return root


def test_split_md_handles_frontmatter_body_and_plain_text():
    fm, body = rs.split_md("---\ntitle: Hi\ncount: 2\nitems: [\"a\"]\n---\nBody\n")
    assert fm == {"title": "Hi", "count": 2, "items": ["a"]}
    assert body == "Body\n"

    fm, body = rs.split_md("---\ntitle: Empty\n---\n")
    assert fm == {"title": "Empty"}
    assert body == ""

    fm, body = rs.split_md("No frontmatter\n")
    assert fm == {}
    assert body == "No frontmatter\n"


def test_render_labels_title_and_desired_state():
    ticket = {
        "id": "PROJ-0001", "tier": "ticket", "title": "Build it",
        "status": "in_progress", "sprint": "SPRINT_A",
    }
    assert rs.render_labels(ticket) == [
        "tier:ticket", "status:in_progress", "sprint:SPRINT_A",
    ]
    assert rs.render_title(ticket) == "[PROJ-0001] Build it"
    assert rs._desired_state(ticket) == "open"

    assert rs.render_labels({"id": "EPIC-1", "tier": "epic", "status": "todo"}) == [
        "tier:epic", "status:todo",
    ]
    assert rs.render_labels({"id": "FEAT-1", "tier": "issue", "status": "done"}) == [
        "tier:issue", "status:done",
    ]
    assert rs._desired_state({"status": "done"}) == "closed"
    assert rs._desired_state({"status": "wont_do"}) == "closed"


def test_signature_is_stable_and_changes_on_any_input():
    base = rs._sig("title", "open", ["b", "a"], "body")
    assert rs._sig("title", "open", ["a", "b"], "body") == base
    assert rs._sig("title!", "open", ["a", "b"], "body") != base
    assert rs._sig("title", "closed", ["a", "b"], "body") != base
    assert rs._sig("title", "open", ["a"], "body") != base
    assert rs._sig("title", "open", ["a", "b"], "body!") != base


def test_read_entities_and_render_ticket_body(tmp_path):
    root = _tiny_work_tree(tmp_path)
    ents = rs.read_entities(root)

    assert set(ents) == {"EPIC-0001", "PROJ-0001", "FEAT-0001", "BUG-0001"}
    assert ents["EPIC-0001"]["tier"] == "epic"
    assert ents["PROJ-0001"]["tier"] == "ticket"
    assert ents["FEAT-0001"]["tier"] == "issue"

    cfg = {"map": {
        "EPIC-0001": {"n": 1}, "PROJ-0001": {"n": 2}, "FEAT-0001": {"n": 3},
    }}
    body = rs.render_body(root, cfg, ents["PROJ-0001"], ents)
    assert "Ticket body." in body
    assert "- **Epic:** EPIC-0001 Launch (#1)" in body
    assert "- **Issues:** FEAT-0001 Feature issue (#3)" in body
    assert "- **Sprint:** SPRINT_2026_W01_A" in body
    assert "- specseed-id: PROJ-0001" in body


def test_next_id_uses_existing_prefix_folders(tmp_path):
    root = _tiny_work_tree(tmp_path)
    assert rs._next_id(root, "BUG") == "BUG-0002"
    assert rs._next_id(root, "PROJ") == "PROJ-0002"


def test_iid_reverses_the_issue_map():
    cfg = {"map": {"FEAT-0001": {"n": 42, "sig": "x"}, "PROJ-0001": 7,
                   "BUG-0001": {"n": 9}}}
    assert rs._iid(cfg, 42) == "FEAT-0001"
    assert rs._iid(cfg, 7) == "PROJ-0001"      # bare-int map entry
    assert rs._iid(cfg, 9) == "BUG-0001"
    assert rs._iid(cfg, 999) is None           # unmapped
    assert rs._iid(cfg, None) is None
    assert rs._iid({}, 1) is None              # no map key


def test_render_sprint_dashboard_mentions_active_sprint(tmp_path):
    root = _tiny_work_tree(tmp_path)
    out = rs.render_sprint_dashboard(root)
    assert "# Sprint SPRINT_2026_W01_A" in out
    assert "Foundations" in out
    assert "- PROJ-0001 Base ticket" in out
    assert "(0/1)" in out


def test_write_md_and_read_text_round_trip(tmp_path):
    p = tmp_path / "doc.md"
    rs._write_md(p, {"id": "BUG-0001", "items": ["a"], "ok": True}, "Body")

    text = rs._read_text(p)
    assert "id: BUG-0001" in text
    assert 'items: ["a"]' in text
    assert "ok: true" in text
    assert text.endswith("Body\n")
    assert rs._read_text(tmp_path / "missing.md") == "_not generated yet_"


# --------------------------------------------------------------------------- #
# change-request (CR) intake + relay — pure helpers + on-disk state, no network
# --------------------------------------------------------------------------- #
import change_requests as crmod  # noqa: E402


class FakeRemote:
    """Minimal stand-in for remote_config.Remote — records mutations, no network."""

    def __init__(self, comments=None, issues=None, user=None):
        self._comments = comments or []
        self._issues = {i["number"]: i for i in (issues or [])}
        self.user = user or {"login": "owner"}
        self.posted = []          # (number, body)
        self.labels = {}          # number -> labels
        self.labels_ensured = []   # seeded label names
        self.closed = []          # (number, planned)
        self.reopened = []        # number

    def comments_since(self, since):
        return list(self._comments)

    def comment(self, n, body):
        self.posted.append((n, body))

    def whoami(self):
        return self.user

    def list_open_issues(self):
        return list(self._issues.values())

    def get_issue(self, n):
        return self._issues.get(n)

    def set_labels(self, n, labels):
        self.labels[n] = labels

    def ensure_label(self, name):
        self.labels_ensured.append(name)

    def close_issue(self, n, planned=True):
        self.closed.append((n, planned))

    def reopen_issue(self, n):
        self.reopened.append(n)


def _cr_root(tmp_path):
    (tmp_path / ".specseed").mkdir(parents=True)
    return tmp_path


def test_is_cr_issue_label_routing():
    assert rs.is_cr_issue({"labels": ["change-request", "bug"]}) is True
    assert rs.is_cr_issue({"labels": ["bug"]}) is False
    assert rs.is_cr_issue({"labels": []}) is False
    assert rs.is_cr_issue({}) is False


def test_ignored_by_label_uses_defaults_and_config_override():
    assert rs.ignored_by_label({"labels": ["draft"]}) is True
    assert rs.ignored_by_label({"labels": ["changes-requested"]}) is True
    assert rs.ignored_by_label({"labels": ["bug"]}) is False
    assert rs.ignored_by_label({"labels": ["park"]}, {"ignore_labels": ["park"]}) is True
    assert rs.ignored_by_label({"labels": ["draft"]}, {"ignore_labels": ["park"]}) is False
    assert rs.ignored_by_label({"labels": ["draft"]}, {"ignore_labels": []}) is False


def test_sync_pull_skips_ignored_remote_issue_even_if_cr_labeled(tmp_path):
    root = _cr_root(tmp_path)
    cfg = {"map": {}, "permanent": {}, "pull_cursor": None,
           "ignore_labels": ["draft"]}
    remote = FakeRemote(issues=[{
        "number": 9, "title": "maybe change scope",
        "labels": ["change-request", "draft"],
        "body": "not ready", "state": "open",
        "raw": {"created_at": "2026-06-02T08:00:00Z"},
    }])
    seen = []

    ingested = rs.sync_pull(root, cfg, remote, log=seen.append)

    assert ingested == []
    assert cfg["map"] == {}
    assert any("skip #9: ignored by label" in msg for msg in seen)


def test_ensure_labels_seeds_custom_ignore_labels_once():
    remote = FakeRemote()
    rs.ensure_labels(remote, dry=False, log=lambda m: None,
                     extra_labels=["park", "draft"])
    assert "park" in remote.labels_ensured
    assert remote.labels_ensured.count("draft") == 1


def test_cr_status_label_mapping():
    assert rs.cr_status_label("open") == "cr:open"
    assert rs.cr_status_label("respec_complete") == "cr:open"
    assert rs.cr_status_label("done") == "cr:done"
    assert rs.cr_status_label("rejected") == "cr:rejected"
    assert rs.cr_status_label("weird") == "cr:open"


def test_bot_comment_marker_round_trip():
    tagged = rs._bot("hello world")
    assert rs.is_bot_comment(tagged) is True
    assert "hello world" in tagged
    assert rs.is_bot_comment("a normal human comment") is False


def test_pick_cr_comments_filters_cursor_author_and_bot():
    cursor = "2026-06-02T10:00:00Z"
    comments = [
        {"author": "alice", "body": "old", "created_at": "2026-06-02T09:00:00Z"},
        {"author": "alice", "body": "please change X", "created_at": "2026-06-02T11:00:00Z"},
        {"author": "owner", "body": rs._bot("agent reply"), "created_at": "2026-06-02T11:30:00Z"},
        {"author": "bob", "body": "not allowed", "created_at": "2026-06-02T11:45:00Z"},
    ]
    texts, newest = rs.pick_cr_comments(comments, cursor, ["alice"])
    assert texts == ["please change X"]            # cursor + author + bot all filtered
    assert newest == "2026-06-02T11:45:00Z"        # high-water mark over ALL comments


def test_pick_cr_comments_empty_allowlist_accepts_any_author():
    texts, _ = rs.pick_cr_comments(
        [{"author": "whoever", "body": "hi", "created_at": "2026-06-02T11:00:00Z"}],
        None, [])
    assert texts == ["hi"]


def test_ingest_cr_issue_creates_local_cr_and_maps_it(tmp_path):
    root = _cr_root(tmp_path)
    cfg = {"map": {}, "permanent": {}, "pull_cursor": None}
    remote = FakeRemote(issues=[{
        "number": 7, "title": "[bug] please add export", "labels": ["change-request"],
        "body": "we need CSV export", "state": "open", "raw": {"created_at": "2026-06-02T08:00:00Z"},
    }])

    ingested = rs.sync_pull(root, cfg, remote, log=lambda m: None)

    assert ingested == [(7, "CR-0001")]
    cr = crmod.load_cr(root, "CR-0001")
    assert cr["status"] == "open" and cr["turn"] == "agent"
    assert cr["remote_issue"] == 7
    assert "CSV export" in cr["request"]
    assert cfg["map"]["CR-0001"]["n"] == 7        # mapped → never re-ingested
    assert remote.posted and remote.posted[0][0] == 7
    assert rs.is_bot_comment(remote.posted[0][1])  # filing note is bot-tagged


def test_sync_cr_comments_stashes_comment_and_flips_turn(tmp_path):
    root = _cr_root(tmp_path)
    cr_id = crmod.create_cr(root, "Title", "the request", remote_issue=42)
    crmod.advance_cursor(root, cr_id, "2026-06-02T10:00:00Z")
    crmod.set_turn(root, cr_id, "human")          # waiting; a comment should re-arm it

    remote = FakeRemote(comments=[
        {"issue_number": 42, "author": "alice", "body": "make it blue",
         "created_at": "2026-06-02T11:00:00Z"},
        {"issue_number": 42, "author": "owner", "body": rs._bot("prev reply"),
         "created_at": "2026-06-02T11:30:00Z"},
        {"issue_number": 99, "author": "alice", "body": "other thread",
         "created_at": "2026-06-02T12:00:00Z"},
    ])
    touched = rs.sync_cr_comments(root, {"allowlist": ["alice"]}, remote, log=lambda m: None)

    assert touched == [cr_id]
    assert crmod.read_pending_comment(root, cr_id) == "make it blue"
    cr = crmod.load_cr(root, cr_id)
    assert cr["turn"] == "agent"
    assert cr["comment_cursor"] == "2026-06-02T11:30:00Z"   # past our own bot reply


def test_sync_cr_comments_skips_terminal_and_local_only(tmp_path):
    root = _cr_root(tmp_path)
    done = crmod.create_cr(root, "Done one", "x", remote_issue=1)
    crmod.set_status(root, done, "done", turn=None)
    crmod.create_cr(root, "Local only", "y")       # no remote_issue
    remote = FakeRemote(comments=[
        {"issue_number": 1, "author": "alice", "body": "late comment",
         "created_at": "2026-06-02T11:00:00Z"}])

    touched = rs.sync_cr_comments(root, {"allowlist": ["alice"]}, remote, log=lambda m: None)
    assert touched == []                            # terminal skipped, local-only skipped


def test_reflect_cr_state_labels_closes_and_heals(tmp_path):
    root = _cr_root(tmp_path)
    done = crmod.create_cr(root, "D", "x", remote_issue=1)
    crmod.set_status(root, done, "done", turn=None)
    live = crmod.create_cr(root, "L", "y", remote_issue=2)   # open, but issue hand-closed

    remote = FakeRemote(issues=[
        {"number": 1, "labels": ["change-request", "cr:open"], "state": "open"},
        {"number": 2, "labels": ["change-request", "cr:open"], "state": "closed"},
    ])
    rs.reflect_cr_state(root, {}, remote, log=lambda m: None)

    assert remote.labels[1] == ["change-request", "cr:done"]
    assert remote.closed == [(1, True)]             # done → closed (planned)
    assert remote.reopened == [2]                   # live CR → reopened
    assert any(rs.is_bot_comment(b) for n, b in remote.posted if n == 2)


def test_post_cr_reply_tags_bot_and_targets_issue(tmp_path):
    root = _cr_root(tmp_path)
    cr_id = crmod.create_cr(root, "T", "x", remote_issue=55)
    remote = FakeRemote()

    assert rs.post_cr_reply(root, {}, remote, cr_id, "my drafted plan") is True
    assert remote.posted[0][0] == 55
    assert rs.is_bot_comment(remote.posted[0][1])
    assert "my drafted plan" in remote.posted[0][1]

    # empty reply or local-only CR → no post
    assert rs.post_cr_reply(root, {}, remote, cr_id, "   ") is False
    local = crmod.create_cr(root, "L", "y")
    assert rs.post_cr_reply(root, {}, remote, local, "hi") is False
