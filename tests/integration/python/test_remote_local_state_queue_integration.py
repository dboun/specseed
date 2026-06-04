"""Remote-local state changes -> local mirror -> DB queue integration tests.

These are opt-in, scary-path tests. They never hit GitHub/GitLab and never run
agents; TrackingRemoteLocal is the source of truth and sqlite is the only store.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.target_facing.specseed_target_src.db.database import Database
from src.target_facing.specseed_target_src.executing import cancellation
from src.target_facing.specseed_target_src.scheduling.sync_to_db import sync_to_db
from src.target_facing.specseed_target_src.tracking.tracking_local import TrackingLocal
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


pytestmark = pytest.mark.integration


def _stamp(n: int) -> str:
    return f"2999-01-01T00:00:{n:02d}.000000Z"


class Harness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote = TrackingRemoteLocal(db_path=root / "remote.db", author="human")
        self.local = TrackingLocal(db_path=root / "local.db", author="agent")
        self.db = Database(db_path=root / "queue.db")

    def sync(self) -> dict:
        return sync_to_db(self.local, self.remote, db=self.db)

    def all_tasks(self) -> list[dict]:
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
        tasks: list[dict] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            tasks.append(item)
        return tasks

    def live_tasks(self) -> list[dict]:
        return [t for t in self.all_tasks() if t["status"] in ("pending", "in_progress")]

    def actions(self) -> list[str]:
        return [t["action"] for t in self.live_tasks()]

    def touch_entry(self, entry_id: int | str, n: int) -> None:
        with sqlite3.connect(self.remote.db_path) as conn:
            conn.execute("UPDATE entries SET updated_at=? WHERE id=?", (_stamp(n), entry_id))

    def edit_entry_sql(
        self,
        entry_id: int | str,
        n: int,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> None:
        sets = ["updated_at=?"]
        values: list[object] = [_stamp(n)]
        if title is not None:
            sets.append("title=?")
            values.append(title)
        if body is not None:
            sets.append("body=?")
            values.append(body)
        values.append(entry_id)
        with sqlite3.connect(self.remote.db_path) as conn:
            conn.execute(f"UPDATE entries SET {', '.join(sets)} WHERE id=?", values)

    def edit_comment_sql(self, comment_id: int | str, body: str, n: int) -> None:
        with sqlite3.connect(self.remote.db_path) as conn:
            row = conn.execute(
                "SELECT entry_id FROM comments WHERE id=?", (comment_id,)
            ).fetchone()
            assert row is not None
            conn.execute(
                "UPDATE comments SET body=?, updated_at=? WHERE id=?",
                (body, _stamp(n), comment_id),
            )
            conn.execute(
                "UPDATE entries SET updated_at=? WHERE id=?",
                (_stamp(n), row[0]),
            )

    def delete_comment_reaction_sql(self, reaction_id: int | str, n: int) -> None:
        with sqlite3.connect(self.remote.db_path) as conn:
            row = conn.execute(
                """
                SELECT c.entry_id
                FROM comment_reactions r
                JOIN comments c ON c.id = r.comment_id
                WHERE r.id = ?
                """,
                (reaction_id,),
            ).fetchone()
            assert row is not None
            conn.execute("DELETE FROM comment_reactions WHERE id=?", (reaction_id,))
            conn.execute("UPDATE entries SET updated_at=? WHERE id=?", (_stamp(n), row[0]))

    def delete_entry_reaction_sql(self, reaction_id: int | str, n: int) -> None:
        with sqlite3.connect(self.remote.db_path) as conn:
            row = conn.execute(
                "SELECT entry_id FROM entry_reactions WHERE id=?", (reaction_id,)
            ).fetchone()
            assert row is not None
            conn.execute("DELETE FROM entry_reactions WHERE id=?", (reaction_id,))
            conn.execute("UPDATE entries SET updated_at=? WHERE id=?", (_stamp(n), row[0]))

    def claim_oldest(self) -> dict:
        task = self.db.claim_next()
        assert task is not None
        return task

    def claim_for_post(self, post_id: int | str) -> dict:
        while True:
            task = self.claim_oldest()
            if task["post_id"] == str(post_id):
                return task
            self.db.complete(task["task_id"], success=True)


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    cancellation.reset()
    return Harness(tmp_path)


def _only_live_for(tasks: list[dict], **payload: object) -> list[dict]:
    out = []
    for task in tasks:
        if task["status"] not in ("pending", "in_progress"):
            continue
        if all(task["payload"].get(key) == value for key, value in payload.items()):
            out.append(task)
    return out


def test_create_storm_mirrors_remote_and_queues_tier_ordered_work(h: Harness) -> None:
    h.remote.create_label("repo-only", color="111111", description="ignored")
    epic = h.remote.add_entry("Epic", labels=["epic", "epic:status:todo"]).data.id
    ticket = h.remote.add_entry("Ticket", labels=["ticket", "ticket:status:todo"]).data.id
    issue = h.remote.add_entry("Issue", labels=["issue", "issue:status:todo"]).data.id
    comment = h.remote.add_entry_comment(issue, "human note").data.id
    h.remote.add_entry_comment_reaction(issue, comment, "heart")
    h.remote.add_entry_reaction(issue, "thumbs_up")
    h.remote.pin_entry(ticket)

    summary = h.sync()

    assert summary["ok"] is True
    assert h.local.get_entry(issue).data.comments[0].body == "human note"
    assert h.local.get_entry(issue).data.reactions[0].kind == "thumbs_up"
    created_ids = [
        task["post_id"]
        for task in h.live_tasks()
        if task["action"] == "handle_entry_created"
    ]
    assert created_ids[:3] == [str(epic), str(ticket), str(issue)]
    assert "handle_comment_added" in h.actions()
    assert "handle_reaction_added" in h.actions()
    assert "handle_entry_reaction_added" in h.actions()
    assert summary["ignored"] >= 2  # repo label + pin do not spawn agent work.


def test_entry_title_body_update_coalesces_and_replaces_pending_create(h: Harness) -> None:
    entry = h.remote.add_entry("Old", body="old", labels=["issue"]).data.id
    h.sync()

    h.edit_entry_sql(entry, 1, title="New", body="new")
    summary = h.sync()

    entry_tasks = [
        t
        for t in h.live_tasks()
        if t["post_id"] == str(entry) and t["action"].startswith("handle_entry_")
    ]
    assert summary["coalesced"] >= 1
    assert summary["superseded"] >= 1
    assert [t["action"] for t in entry_tasks] == ["handle_entry_updated"]
    assert h.local.get_entry(entry).data.title == "New"
    assert h.local.get_entry(entry).data.body == "new"


def test_comment_edit_supersedes_comment_create_without_losing_parent(h: Harness) -> None:
    entry = h.remote.add_entry("Discuss", labels=["issue"]).data.id
    comment = h.remote.add_entry_comment(entry, "first").data.id
    h.sync()

    h.edit_comment_sql(comment, "edited", 2)
    summary = h.sync()

    tasks = _only_live_for(h.all_tasks(), comment_id=comment)
    assert summary["superseded"] >= 1
    assert [(t["action"], t["post_id"]) for t in tasks] == [
        ("handle_comment_updated", str(entry))
    ]
    assert h.local.get_entry(entry).data.comments[0].body == "edited"


def test_status_label_swap_keeps_both_sides_with_entity_context(h: Harness) -> None:
    entry = h.remote.add_entry(
        "Move state", labels=["issue", "issue:status:todo"]
    ).data.id
    h.sync()

    h.remote.add_entry_label(entry, "issue:status:in_progress")
    h.remote.remove_entry_label(entry, "issue:status:todo")
    summary = h.sync()

    status_tasks = [
        t
        for t in h.live_tasks()
        if t["payload"].get("label", "").startswith("issue:status:")
    ]
    by_action = {t["action"]: t["payload"] for t in status_tasks}
    assert summary["ok"] is True
    assert by_action["handle_label_added"]["entity"]["status"] == "in_progress"
    assert by_action["handle_label_removed"]["entity"]["status"] == "todo"


def test_label_add_then_remove_across_polls_cancels_old_pending_work(h: Harness) -> None:
    entry = h.remote.add_entry("Label churn", labels=["issue"]).data.id
    h.remote.add_entry_label(entry, "urgent")
    h.sync()

    h.remote.remove_entry_label(entry, "urgent")
    summary = h.sync()

    live = _only_live_for(h.all_tasks(), label="urgent")
    assert summary["superseded"] >= 1
    assert [(t["action"], t["post_id"]) for t in live] == [
        ("handle_label_removed", str(entry))
    ]


def test_comment_reaction_add_then_remove_cancels_old_pending_work(h: Harness) -> None:
    entry = h.remote.add_entry("React", labels=["issue"]).data.id
    comment = h.remote.add_entry_comment(entry, "vote here").data.id
    reaction = h.remote.add_entry_comment_reaction(entry, comment, "eyes").data
    h.sync()

    h.delete_comment_reaction_sql(reaction_id=1, n=3)
    summary = h.sync()

    live = _only_live_for(h.all_tasks(), reaction_id=1)
    assert summary["superseded"] >= 1
    assert [(t["action"], t["post_id"]) for t in live] == [
        ("handle_reaction_removed", str(comment))
    ]
    assert reaction.reaction == "eyes"


def test_entry_reaction_add_then_remove_cancels_gate_recheck_work(h: Harness) -> None:
    entry = h.remote.add_entry(
        "Approval gate", labels=["issue", "issue:status:awaiting_approval"]
    ).data.id
    h.remote.add_entry_reaction(entry, "thumbs_up")
    h.sync()

    h.delete_entry_reaction_sql(reaction_id=1, n=4)
    summary = h.sync()

    live = _only_live_for(h.all_tasks(), reaction_id=1)
    assert summary["superseded"] >= 1
    assert [(t["action"], t["post_id"]) for t in live] == [
        ("handle_entry_reaction_removed", str(entry))
    ]


def test_close_sweeps_pending_child_work_and_prearms_in_progress_cancel(h: Harness) -> None:
    filler = h.remote.add_entry("Filler", labels=["issue"]).data.id
    entry = h.remote.add_entry("Close me", labels=["issue"]).data.id
    comment = h.remote.add_entry_comment(entry, "review").data.id
    h.remote.add_entry_comment_reaction(entry, comment, "heart")
    h.remote.add_entry_reaction(entry, "thumbs_down")
    h.sync()

    claimed = h.claim_for_post(entry)
    h.remote.set_entry_closed(entry)
    summary = h.sync()

    assert filler != entry
    assert summary["cleanups"] == 1
    assert cancellation.is_cancelled(claimed["task_id"])
    assert h.db.get_task(claimed["task_id"])["status"] == "in_progress"
    cleanup = [t for t in h.live_tasks() if t["action"] == "cleanup"]
    assert cleanup[0]["payload"]["interrupted_task_id"] == claimed["task_id"]
    assert [
        t
        for t in h.live_tasks()
        if t["task_id"] != claimed["task_id"]
        and (
            t["post_id"] == str(entry)
            or (
                t["action"] == "handle_reaction_added"
                and t["payload"].get("reaction_id") == 1
            )
        )
        and t["action"] != "cleanup"
    ] == []


def test_delete_entry_removes_local_graph_and_leaves_only_cleanup_for_running_work(
    h: Harness,
) -> None:
    entry = h.remote.add_entry("Delete me", labels=["issue", "obsolete"]).data.id
    comment = h.remote.add_entry_comment(entry, "child").data.id
    h.remote.add_entry_comment_reaction(entry, comment, "thumbs_up")
    h.sync()
    claimed = h.claim_for_post(entry)

    h.remote.delete_entry(entry)
    summary = h.sync()

    assert summary["cleanups"] == 1
    assert h.local.get_entry(entry).ok is False
    assert h.db.get_task(claimed["task_id"])["status"] == "in_progress"
    assert [
        t
        for t in h.live_tasks()
        if t["task_id"] != claimed["task_id"]
        and (
            t["post_id"] == str(entry)
            or (
                t["action"] in ("handle_reaction_added", "handle_reaction_removed")
                and t["payload"].get("reaction_id") == 1
            )
        )
        and t["action"] != "cleanup"
    ] == []


def test_reopen_after_close_becomes_single_reopened_task(h: Harness) -> None:
    entry = h.remote.add_entry("Reopen", labels=["issue"]).data.id
    h.sync()
    for task in h.live_tasks():
        h.db.complete(task["task_id"], success=True)

    h.remote.set_entry_closed(entry)
    h.sync()
    h.remote.set_entry_open(entry)
    summary = h.sync()

    live = [t for t in h.live_tasks() if t["post_id"] == str(entry)]
    assert summary["enqueued"] == 1
    assert [(t["action"], t["payload"]) for t in live] == [
        ("handle_entry_reopened", live[0]["payload"])
    ]
    assert h.local.get_entry(entry).data.is_open is True


def test_pull_request_changes_mirror_but_never_enqueue_agent_work(h: Harness) -> None:
    pr = h.remote.add_pull_request(
        "Draft PR",
        source_branch="feature/x",
        target_branch="main",
        labels=["ready-for-review"],
    ).data.id
    h.remote.add_pull_request_comment(pr, "please review")

    summary = h.sync()

    assert summary["ok"] is True
    assert summary["enqueued"] == 0
    assert summary["ignored"] >= 2
    mirrored = h.local.get_pull_request(pr)
    assert mirrored.ok is True
    assert mirrored.data.title == "Draft PR"
    assert mirrored.data.comments[0].body == "please review"
