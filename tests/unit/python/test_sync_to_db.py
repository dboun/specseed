"""sync_to_db.py - change stream -> DB queue translation.

Uses TrackingRemoteLocal (source of truth) synced into TrackingLocal, then drives
sync_to_db and asserts on the resulting queue. No GitHub/GitLab involved.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from specseed_runtime.db.database import Database
from specseed_runtime.scheduling.sync_to_db import sync_to_db
from specseed_runtime.tracking.comment import TrackingEntryComment, TrackingReaction
from specseed_runtime.tracking.post import (
    TrackingEntryDetails,
    TrackingEntryId,
    TrackingEntryOpenState,
    TrackingLabel,
    TrackingLabelList,
    TrackingLabelSet,
    TrackingPinState,
)
from specseed_runtime.tracking.pull_request import (
    TrackingPullRequestDetails,
    TrackingPullRequestId,
    TrackingPullRequestOpenState,
)
from specseed_runtime.tracking.tracking_base import (
    TrackingBase,
    TrackingCommentId,
    TrackingReactionResult,
    TrackingResult,
)
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal


class ProviderRemoteStub(TrackingBase):
    def __init__(self) -> None:
        labels = [TrackingLabel("bug", "d73a4a")]
        self.entry = TrackingEntryDetails(
            id=7,
            title="Provider bug",
            labels=labels,
            is_open=True,
            author="alice",
            assignees=["agent"],
            created_at="2026-06-04T10:00:00Z",
            updated_at="2026-06-05T01:00:00Z",
            body="Crash from provider remote",
            comments=[
                TrackingEntryComment(
                    id=70,
                    body="please fix",
                    author="alice",
                    created_at="2026-06-05T01:01:00Z",
                    updated_at="2026-06-05T01:01:00Z",
                    reactions=[TrackingReaction("heart", count=1, users=["bob"])],
                )
            ],
            reactions=[TrackingReaction("thumbs_up", count=1, users=["carol"])],
        )

    def list_entries(self, is_open=None, labels=None, assignee=None, updated_since=None):
        return TrackingResult(ok=True, data=[self.entry])

    def get_entry(self, entry_id):
        return TrackingResult(ok=True, data=self.entry)

    def list_labels(self):
        return TrackingResult(ok=True, data=TrackingLabelList(labels=list(self.entry.labels)))

    def list_pull_requests(self, is_open=None, labels=None, assignee=None, updated_since=None):
        return TrackingResult(ok=True, data=[])

    def get_pull_request(self, pull_request_id):
        return TrackingResult(ok=False, error=f"pull request not found: {pull_request_id}")

    def sync_from_remote(self, remote):
        return TrackingResult(ok=False, error="not implemented")

    def is_entry_open(self, entry_id):
        return TrackingResult(ok=True, data=TrackingEntryOpenState(id=entry_id, is_open=True))

    def set_entry_open(self, entry_id):
        return TrackingResult(ok=True, data=TrackingEntryOpenState(id=entry_id, is_open=True))

    def set_entry_closed(self, entry_id):
        return TrackingResult(ok=True, data=TrackingEntryOpenState(id=entry_id, is_open=False))

    def pin_entry(self, entry_id):
        return TrackingResult(ok=True, data=TrackingPinState(id=entry_id, pinned=True))

    def delete_entry(self, entry_id):
        return TrackingResult(ok=True, data=TrackingEntryId(id=entry_id))

    def edit_entry(self, entry_id, title=None, body=None):
        return TrackingResult(ok=True, data=TrackingEntryId(id=entry_id))

    def add_entry(self, title, body=None, labels=None, assignees=None):
        return TrackingResult(ok=True, data=TrackingEntryId(id=7))

    def add_entry_comment(self, entry_id, body):
        return TrackingResult(ok=True, data=TrackingCommentId(id=70))

    def add_entry_comment_reaction(self, entry_id, comment_id, reaction):
        return TrackingResult(ok=True, data=TrackingReactionResult(entry_id, comment_id, reaction))

    def add_entry_reaction(self, entry_id, reaction):
        return TrackingResult(ok=True, data=TrackingReactionResult(entry_id, entry_id, reaction))

    def get_entry_labels(self, entry_id):
        return TrackingResult(ok=True, data=TrackingLabelSet(entry_id=entry_id, labels=list(self.entry.labels)))

    def add_entry_label(self, entry_id, label):
        return self.get_entry_labels(entry_id)

    def remove_entry_label(self, entry_id, label):
        return TrackingResult(ok=True, data=TrackingLabelSet(entry_id=entry_id, labels=[]))

    def create_label(self, name, color=None, description=None):
        return TrackingResult(ok=True, data=TrackingLabel(name, color, description))

    def ensure_label(self, name, color=None, description=None):
        return self.create_label(name, color, description)

    def add_pull_request(self, title, source_branch, target_branch, body=None, labels=None, assignees=None):
        return TrackingResult(ok=True, data=TrackingPullRequestId(id=1))

    def add_pull_request_comment(self, pull_request_id, body):
        return TrackingResult(ok=True, data=TrackingCommentId(id=1))

    def add_pull_request_comment_reaction(self, pull_request_id, comment_id, reaction):
        return TrackingResult(ok=True, data=TrackingReactionResult(pull_request_id, comment_id, reaction))

    def set_pull_request_closed(self, pull_request_id):
        return TrackingResult(ok=True, data=TrackingPullRequestOpenState(id=pull_request_id, is_open=False))


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

    def test_entry_reaction_maps_to_entry_reaction_task_on_the_entry(self) -> None:
        entry = self.remote.add_entry("Gate").data.id
        self.remote.add_entry_reaction(entry, "thumbs_up")

        summary = self.sync()
        self.assertTrue(summary["ok"])
        task = next(
            t for t in self.all_tasks() if t["action"] == "handle_entry_reaction_added"
        )
        # parent is the ENTRY (so the work path can resolve the entity's gate),
        # not a comment id like a comment reaction.
        self.assertEqual(str(task["post_id"]), str(entry))
        self.assertEqual(task["payload"]["kind"], "thumbs_up")

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

    def test_close_does_not_sweep_unrelated_entry_sharing_comment_id(self) -> None:
        # Comment ids and entry ids are separate sqlite sequences in one integer
        # space: entry A gets id 1, and entry B's first comment ALSO gets id 1.
        # Closing B sweeps its comment-id keys; that sweep must only touch
        # comment-keyed (reaction) tasks, never entry A's tasks.
        entry_a = self.remote.add_entry("A", labels=["bug"]).data.id
        entry_b = self.remote.add_entry("B").data.id
        cid = self.remote.add_entry_comment(entry_b, "c").data.id
        self.assertEqual(str(cid), str(entry_a))  # the collision this guards
        self.remote.add_entry_comment_reaction(entry_b, cid, "thumbs_up")
        self.sync()

        a_tasks = [
            t for t in self.live_tasks()
            if t["post_id"] == str(entry_a) and not t["action"].startswith("handle_reaction")
        ]
        reaction_tasks = [t for t in self.live_tasks() if t["action"] == "handle_reaction_added"]
        self.assertTrue(a_tasks)
        self.assertTrue(reaction_tasks)

        self.remote.set_entry_closed(entry_b)
        self.sync()

        # A's pending tasks survive; B's comment-reaction task is swept.
        for task in a_tasks:
            self.assertIsNotNone(self.db.get_task(task["task_id"]))
        for task in reaction_tasks:
            self.assertIsNone(self.db.get_task(task["task_id"]))

    def test_idempotent_resync_enqueues_nothing(self) -> None:
        self.remote.add_entry("E", labels=["bug"])
        first = self.sync()
        self.assertGreater(first["enqueued"], 0)
        second = self.sync()
        self.assertEqual(second["changes"], 0)
        self.assertEqual(second["enqueued"], 0)

    def test_provider_remote_syncs_into_local_without_type_crash(self) -> None:
        provider = ProviderRemoteStub()

        first = sync_to_db(self.local, provider, db=self.db)
        self.assertTrue(first["ok"])
        self.assertNotIn("error", first)
        self.assertIn("handle_entry_created", self.actions())
        self.assertIn("handle_label_added", self.actions())
        self.assertIn("handle_comment_added", self.actions())
        self.assertIn("handle_reaction_added", self.actions())
        self.assertIn("handle_entry_reaction_added", self.actions())

        second = sync_to_db(self.local, provider, db=self.db)
        self.assertTrue(second["ok"])
        self.assertEqual(second["changes"], 0)


if __name__ == "__main__":
    unittest.main()
