"""sync_to_db.py - change stream -> DB queue translation.

Uses TrackingRemoteLocal (source of truth) synced into TrackingLocal, then drives
sync_to_db and asserts on the resulting queue. No GitHub/GitLab involved.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.target_facing.specseed_target_src.db.database import Database
from src.target_facing.specseed_target_src.scheduling.sync_to_db import sync_to_db
from src.target_facing.specseed_target_src.tracking.tracking_local import TrackingLocal
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import TrackingRemoteLocal


class SyncToDbTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote = TrackingRemoteLocal(db_path=root / "tracking_remote_local.db", author="alice")
        self.local = TrackingLocal(db_path=root / "tracking_local.db", author="agent")
        self.db = Database(db_path=root / "queue.db")

    def sync(self) -> dict:
        return sync_to_db(self.local, self.remote, db=self.db)

    def all_tasks(self) -> list[dict]:
        return [t for t in (self.db.get_task(i) for i in range(1, 300)) if t is not None]

    def live_tasks(self) -> list[dict]:
        return [t for t in self.all_tasks() if t["status"] in ("pending", "in_progress")]

    def actions(self) -> list[str]:
        return [t["action"] for t in self.live_tasks()]

    def test_creates_map_to_tasks_in_tier_order(self) -> None:
        self.remote.create_label("tier:epic")
        self.remote.create_label("tier:issue")
        epic = self.remote.add_entry("Epic", labels=["tier:epic"]).data.id
        issue = self.remote.add_entry("Issue", labels=["tier:issue"]).data.id
        cid = self.remote.add_entry_comment(issue, "hello").data.id
        self.remote.add_entry_comment_reaction(issue, cid, "thumbs_up")

        summary = self.sync()
        self.assertTrue(summary["ok"])
        # epic entry enqueues before issue entry (tier order), labels are entry_labels.
        acts = self.actions()
        self.assertEqual(acts[0], "handle_entry_created")  # epic first
        self.assertIn("handle_label_added", acts)
        self.assertIn("handle_comment_added", acts)
        self.assertIn("handle_reaction_added", acts)
        # repo-level label creates are ignored (2 of them).
        self.assertEqual(summary["ignored"], 2)

    def test_label_entity_context_in_payload(self) -> None:
        entry = self.remote.add_entry("E").data.id
        self.remote.add_entry_label(entry, "status:todo")
        self.sync()
        task = next(t for t in self.all_tasks() if t["action"] == "handle_label_added")
        self.assertEqual(task["payload"]["entity"]["status"], "todo")

    def test_comment_edit_supersedes_prior_comment_task(self) -> None:
        entry = self.remote.add_entry("E").data.id
        cid = self.remote.add_entry_comment(entry, "first").data.id
        self.sync()  # enqueues handle_comment_added for cid

        # Edit the comment on the remote. A real provider bumps the parent entry's
        # updated_at too, which is what makes the edit visible to the sync.
        with sqlite3.connect(self.remote.db_path) as conn:
            conn.execute(
                "UPDATE comments SET body=?, updated_at=? WHERE id=?",
                ("edited", "2999-01-01T00:00:00Z", cid),
            )
            conn.execute(
                "UPDATE entries SET updated_at=? WHERE id=?",
                ("2999-01-01T00:00:00Z", entry),
            )
        summary = self.sync()

        comment_tasks = [t for t in self.live_tasks() if t["payload"].get("comment_id") == cid]
        # exactly one task about this comment; the edit replaced the add.
        self.assertEqual(len(comment_tasks), 1)
        self.assertEqual(comment_tasks[0]["action"], "handle_comment_updated")
        self.assertGreaterEqual(summary["superseded"], 1)

    def test_label_add_then_remove_cancels_out(self) -> None:
        entry = self.remote.add_entry("E").data.id
        self.remote.add_entry_label(entry, "urgent")
        self.sync()  # handle_label_added pending
        # remove the label on the remote
        with sqlite3.connect(self.remote.db_path) as conn:
            conn.execute("DELETE FROM entry_labels WHERE entry_id=? AND label_name=?", (entry, "urgent"))
        self.sync()

        live = [t for t in self.live_tasks() if t["payload"].get("label") == "urgent"]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["action"], "handle_label_removed")

    def test_close_sweeps_pending_and_cleans_up_in_progress(self) -> None:
        entry = self.remote.add_entry("E", labels=["bug"]).data.id
        self.remote.add_entry_comment(entry, "c")
        self.sync()
        pending_before = [t["task_id"] for t in self.live_tasks() if t["status"] == "pending"]

        # claim the oldest task to make it in_progress, leave the rest pending
        claimed = self.db.claim_next()
        self.assertIsNotNone(claimed)

        # close the entry on the remote and re-sync. Microsecond-precision,
        # strictly-increasing timestamps make the close visible without any hack.
        self.remote.set_entry_closed(entry)
        summary = self.sync()

        # every task that was pending before the close is now gone (swept).
        for tid in pending_before:
            if tid != claimed["task_id"]:
                self.assertIsNone(self.db.get_task(tid))
        # the in-progress row was never edited.
        self.assertEqual(self.db.get_task(claimed["task_id"])["status"], "in_progress")
        self.assertEqual(summary["cleanups"], 1)
        self.assertEqual(summary["interrupts_todo"], 1)
        # a cleanup task was enqueued referencing the interrupted task.
        cleanup = next(t for t in self.all_tasks() if t["action"] == "cleanup")
        self.assertEqual(cleanup["payload"]["interrupted_task_id"], claimed["task_id"])
        self.assertEqual(cleanup["payload"]["reason"], "entry_state")

    def test_idempotent_resync_enqueues_nothing(self) -> None:
        self.remote.add_entry("E", labels=["bug"])
        first = self.sync()
        self.assertGreater(first["enqueued"], 0)
        second = self.sync()
        self.assertEqual(second["changes"], 0)
        self.assertEqual(second["enqueued"], 0)


if __name__ == "__main__":
    unittest.main()
