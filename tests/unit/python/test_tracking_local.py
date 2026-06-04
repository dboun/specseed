"""tracking_local.py - local sqlite TrackingBase implementation.

Syncs are exercised the way the real system works: a TrackingRemoteLocal stands
in for the remote source of truth, and a TrackingLocal is the local copy synced
from it. No GitHub/GitLab is involved.
"""

from __future__ import annotations

import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path


TRACKING_DIR = (
    Path(__file__).resolve().parents[3]
    / "skills"
    / "specseed"
    / "to_copy"
    / "scripts"
    / "tracking"
)
sys.path.insert(0, str(TRACKING_DIR))

from tracking_base import (  # noqa: E402
    TrackingEntryDetails,
    TrackingLabel,
    TrackingReaction,
    TrackingSyncChange,
)
from tracking_local import TrackingLocal  # noqa: E402
from tracking_remote_local import TrackingRemoteLocal  # noqa: E402


class TrackingLocalTest(unittest.TestCase):
    def make_local(self, author: str = "alice") -> tuple[tempfile.TemporaryDirectory, TrackingLocal]:
        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "tracking_local.db"
        return tmp, TrackingLocal(db_path=db_path, author=author)

    def test_add_and_get_entry_round_trips_full_details(self) -> None:
        tmp, local = self.make_local()
        self.addCleanup(tmp.cleanup)

        created = local.add_entry(
            "Build local backend",
            body="Use sqlite.",
            labels=["feature", "local"],
            assignees=["alice", "bob"],
        )
        self.assertTrue(created.ok)

        details = local.get_entry(created.data.id)

        self.assertTrue(details.ok)
        self.assertIsInstance(details.data, TrackingEntryDetails)
        self.assertEqual(details.data.id, 1)
        self.assertEqual(details.data.title, "Build local backend")
        self.assertEqual(details.data.body, "Use sqlite.")
        self.assertEqual(details.data.author, "alice")
        self.assertEqual(details.data.assignees, ["alice", "bob"])
        self.assertEqual([label.name for label in details.data.labels], ["feature", "local"])
        self.assertTrue(details.data.is_open)
        self.assertEqual(details.data.url, "local://tracking_local/entries/1")
        self.assertIsNotNone(details.data.created_at)
        self.assertIsNotNone(details.data.updated_at)

    def test_entries_persist_between_instances(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "tracking_local.db"

        first = TrackingLocal(db_path=db_path, author="alice")
        created = first.add_entry("Persist me", labels=["persist"])

        second = TrackingLocal(db_path=db_path, author="bob")
        details = second.get_entry(created.data.id)

        self.assertTrue(details.ok)
        self.assertEqual(details.data.title, "Persist me")
        self.assertEqual([label.name for label in details.data.labels], ["persist"])
        self.assertEqual(details.data.author, "alice")

    def test_list_entries_filters_by_state_label_assignee_and_updated_since(self) -> None:
        tmp, local = self.make_local()
        self.addCleanup(tmp.cleanup)

        first = local.add_entry("First", labels=["bug"], assignees=["alice"])
        second = local.add_entry("Second", labels=["feature"], assignees=["bob"])
        before_close = local.get_entry(second.data.id).data.updated_at
        local.set_entry_closed(second.data.id)

        open_bug = local.list_entries(is_open=True, labels=["bug"], assignee="alice")
        closed = local.list_entries(is_open=False)
        updated = local.list_entries(updated_since=before_close)

        self.assertEqual([entry.id for entry in open_bug.data], [first.data.id])
        self.assertEqual([entry.id for entry in closed.data], [second.data.id])
        self.assertIn(second.data.id, [entry.id for entry in updated.data])

    def test_open_close_and_pin_are_idempotent(self) -> None:
        tmp, local = self.make_local()
        self.addCleanup(tmp.cleanup)
        entry_id = local.add_entry("Stateful").data.id

        closed_once = local.set_entry_closed(entry_id)
        closed_twice = local.set_entry_closed(entry_id)
        opened = local.set_entry_open(entry_id)
        pinned_once = local.pin_entry(entry_id)
        pinned_twice = local.pin_entry(entry_id)

        self.assertTrue(closed_once.ok)
        self.assertFalse(closed_once.data.is_open)
        self.assertTrue(closed_twice.ok)
        self.assertFalse(closed_twice.data.is_open)
        self.assertTrue(opened.ok)
        self.assertTrue(opened.data.is_open)
        self.assertTrue(pinned_once.ok)
        self.assertTrue(pinned_once.data.pinned)
        self.assertTrue(pinned_twice.ok)
        self.assertTrue(pinned_twice.data.pinned)

    def test_labels_can_be_created_listed_and_attached_without_duplicates(self) -> None:
        tmp, local = self.make_local()
        self.addCleanup(tmp.cleanup)
        entry_id = local.add_entry("Needs labels").data.id

        created = local.create_label("urgent", color="ff0000", description="Do first")
        ensured = local.ensure_label("urgent", color="00ff00", description="Ignored")
        first_attach = local.add_entry_label(entry_id, "urgent")
        second_attach = local.add_entry_label(entry_id, "urgent")
        listed = local.list_labels()

        self.assertEqual(created.data, TrackingLabel("urgent", "ff0000", "Do first"))
        self.assertEqual(ensured.data, TrackingLabel("urgent", "ff0000", "Do first"))
        self.assertEqual([label.name for label in first_attach.data.labels], ["urgent"])
        self.assertEqual([label.name for label in second_attach.data.labels], ["urgent"])
        self.assertEqual(listed.data.labels, [TrackingLabel("urgent", "ff0000", "Do first")])

    def test_comments_and_reactions_are_returned_on_entry_details(self) -> None:
        tmp, local = self.make_local(author="alice")
        self.addCleanup(tmp.cleanup)
        entry_id = local.add_entry("Discuss").data.id
        comment_id = local.add_entry_comment(entry_id, "Looks good").data.id

        reaction = local.add_entry_comment_reaction(entry_id, comment_id, "thumbs_up")
        local.add_entry_comment_reaction(entry_id, comment_id, "thumbs_up")
        details = local.get_entry(entry_id)

        self.assertTrue(reaction.ok)
        self.assertEqual(reaction.data.entry_id, entry_id)
        self.assertEqual(reaction.data.comment_id, comment_id)
        self.assertEqual(reaction.data.reaction, "thumbs_up")
        self.assertEqual(len(details.data.comments), 1)
        self.assertEqual(details.data.comments[0].body, "Looks good")
        self.assertEqual(
            details.data.comments[0].reactions,
            [TrackingReaction("thumbs_up", count=2, users=["alice", "alice"])],
        )

    def test_error_envelopes_for_missing_entries_and_bad_input(self) -> None:
        tmp, local = self.make_local()
        self.addCleanup(tmp.cleanup)
        entry_id = local.add_entry("Has comment").data.id
        comment_id = local.add_entry_comment(entry_id, "body").data.id

        missing = local.get_entry(999)
        bad_title = local.add_entry("")
        bad_comment = local.add_entry_comment(entry_id, "")
        bad_reaction = local.add_entry_comment_reaction(entry_id, comment_id, "confetti")
        missing_comment = local.add_entry_comment_reaction(entry_id, 999, "eyes")

        self.assertFalse(missing.ok)
        self.assertEqual(missing.error, "entry not found: 999")
        self.assertFalse(bad_title.ok)
        self.assertEqual(bad_title.error, "entry title is required")
        self.assertFalse(bad_comment.ok)
        self.assertEqual(bad_comment.error, "comment body is required")
        self.assertFalse(bad_reaction.ok)
        self.assertEqual(bad_reaction.error, "unsupported reaction: confetti")
        self.assertFalse(missing_comment.ok)
        self.assertEqual(missing_comment.error, "comment not found: 999")

    def test_sync_from_remote_copies_remote_into_local_in_action_order(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        remote_path = Path(tmp.name) / "tracking_remote_local.db"
        local_path = Path(tmp.name) / "tracking_local.db"
        remote = TrackingRemoteLocal(db_path=remote_path, author="alice")
        local = TrackingLocal(db_path=local_path, author="bob")

        remote.create_label("tier:epic", color="111111", description="Epics")
        remote.create_label("tier:issue", color="222222", description="Issues")
        epic_id = remote.add_entry("Epic", labels=["tier:epic"]).data.id
        issue_id = remote.add_entry("Issue", labels=["tier:issue"]).data.id
        remote.pin_entry(epic_id)
        comment_id = remote.add_entry_comment(issue_id, "Source comment").data.id
        remote.add_entry_comment_reaction(issue_id, comment_id, "heart")

        result = local.sync_from_remote(remote)

        self.assertTrue(result.ok)
        self.assertTrue(all(isinstance(change, TrackingSyncChange) for change in result.data))
        ordered = [(change.action, change.resource_type, change.resource_id) for change in result.data]
        self.assertEqual(
            ordered,
            [
                ("create", "label", "tier:epic"),
                ("create", "label", "tier:issue"),
                ("create", "entry", epic_id),
                ("create", "entry", issue_id),
                ("create", "entry_label", f"{epic_id}:tier:epic"),
                ("create", "entry_label", f"{issue_id}:tier:issue"),
                ("create", "pin", epic_id),
                ("create", "comment", comment_id),
                ("create", "reaction", 1),
            ],
        )

        synced_issue = local.get_entry(issue_id)
        self.assertTrue(synced_issue.ok)
        self.assertEqual(synced_issue.data.comments[0].body, "Source comment")
        self.assertEqual(synced_issue.data.comments[0].reactions, [TrackingReaction("heart", 1, ["alice"])])

        second = local.sync_from_remote(remote)
        self.assertTrue(second.ok)
        self.assertEqual(second.data, [])

    def test_sync_from_remote_records_updates_and_deletions(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        remote_path = Path(tmp.name) / "tracking_remote_local.db"
        local_path = Path(tmp.name) / "tracking_local.db"
        remote = TrackingRemoteLocal(db_path=remote_path, author="alice")
        local = TrackingLocal(db_path=local_path, author="bob")

        entry_id = remote.add_entry("Original", body="Body", labels=["bug"]).data.id
        comment_id = remote.add_entry_comment(entry_id, "Keep me").data.id
        remote.add_entry_comment_reaction(entry_id, comment_id, "eyes")
        local.sync_from_remote(remote)

        with sqlite3.connect(remote_path) as conn:
            conn.execute(
                "UPDATE entries SET title = ?, updated_at = ? WHERE id = ?",
                ("Updated", "2999-01-01T00:00:00Z", entry_id),
            )
            conn.execute("DELETE FROM comment_reactions WHERE id = 1")
            conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            conn.execute("DELETE FROM entry_labels WHERE entry_id = ? AND label_name = ?", (entry_id, "bug"))

        result = local.sync_from_remote(remote)

        self.assertTrue(result.ok)
        ordered = [(change.action, change.resource_type, change.field) for change in result.data]
        self.assertIn(("update", "entry", "title"), ordered)
        self.assertIn(("update", "entry", "updated_at"), ordered)
        self.assertEqual(
            ordered[-3:],
            [
                ("delete", "reaction", None),
                ("delete", "comment", None),
                ("delete", "entry_label", None),
            ],
        )
        details = local.get_entry(entry_id)
        self.assertTrue(details.ok)
        self.assertEqual(details.data.title, "Updated")
        self.assertEqual(details.data.comments, [])
        self.assertEqual(details.data.labels, [])


if __name__ == "__main__":
    unittest.main()
