"""remote_local.py - local sqlite RemoteBase implementation."""

from __future__ import annotations

import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path


REMOTE_DIR = (
    Path(__file__).resolve().parents[3]
    / "skills"
    / "specseed"
    / "to_copy"
    / "scripts"
    / "remote"
)
sys.path.insert(0, str(REMOTE_DIR))

from remote_base import RemoteEntryDetails, RemoteLabel, RemoteReaction, RemoteSyncChange  # noqa: E402
from remote_local import RemoteLocal  # noqa: E402


class RemoteLocalTest(unittest.TestCase):
    def make_remote(self, author: str = "alice") -> tuple[tempfile.TemporaryDirectory, RemoteLocal]:
        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "remote_local.db"
        return tmp, RemoteLocal(db_path=db_path, author=author)

    def test_add_and_get_entry_round_trips_full_details(self) -> None:
        tmp, remote = self.make_remote()
        self.addCleanup(tmp.cleanup)

        created = remote.add_entry(
            "Build local backend",
            body="Use sqlite.",
            labels=["feature", "local"],
            assignees=["alice", "bob"],
        )
        self.assertTrue(created.ok)

        details = remote.get_entry(created.data.id)

        self.assertTrue(details.ok)
        self.assertIsInstance(details.data, RemoteEntryDetails)
        self.assertEqual(details.data.id, 1)
        self.assertEqual(details.data.title, "Build local backend")
        self.assertEqual(details.data.body, "Use sqlite.")
        self.assertEqual(details.data.author, "alice")
        self.assertEqual(details.data.assignees, ["alice", "bob"])
        self.assertEqual([label.name for label in details.data.labels], ["feature", "local"])
        self.assertTrue(details.data.is_open)
        self.assertEqual(details.data.url, "local://remote_local/entries/1")
        self.assertIsNotNone(details.data.created_at)
        self.assertIsNotNone(details.data.updated_at)

    def test_entries_persist_between_instances(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "remote_local.db"

        first = RemoteLocal(db_path=db_path, author="alice")
        created = first.add_entry("Persist me", labels=["persist"])

        second = RemoteLocal(db_path=db_path, author="bob")
        details = second.get_entry(created.data.id)

        self.assertTrue(details.ok)
        self.assertEqual(details.data.title, "Persist me")
        self.assertEqual([label.name for label in details.data.labels], ["persist"])
        self.assertEqual(details.data.author, "alice")

    def test_list_entries_filters_by_state_label_assignee_and_updated_since(self) -> None:
        tmp, remote = self.make_remote()
        self.addCleanup(tmp.cleanup)

        first = remote.add_entry("First", labels=["bug"], assignees=["alice"])
        second = remote.add_entry("Second", labels=["feature"], assignees=["bob"])
        before_close = remote.get_entry(second.data.id).data.updated_at
        remote.set_entry_closed(second.data.id)

        open_bug = remote.list_entries(is_open=True, labels=["bug"], assignee="alice")
        closed = remote.list_entries(is_open=False)
        updated = remote.list_entries(updated_since=before_close)

        self.assertEqual([entry.id for entry in open_bug.data], [first.data.id])
        self.assertEqual([entry.id for entry in closed.data], [second.data.id])
        self.assertIn(second.data.id, [entry.id for entry in updated.data])

    def test_open_close_and_pin_are_idempotent(self) -> None:
        tmp, remote = self.make_remote()
        self.addCleanup(tmp.cleanup)
        entry_id = remote.add_entry("Stateful").data.id

        closed_once = remote.set_entry_closed(entry_id)
        closed_twice = remote.set_entry_closed(entry_id)
        opened = remote.set_entry_open(entry_id)
        pinned_once = remote.pin_entry(entry_id)
        pinned_twice = remote.pin_entry(entry_id)

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
        tmp, remote = self.make_remote()
        self.addCleanup(tmp.cleanup)
        entry_id = remote.add_entry("Needs labels").data.id

        created = remote.create_label("urgent", color="ff0000", description="Do first")
        ensured = remote.ensure_label("urgent", color="00ff00", description="Ignored")
        first_attach = remote.add_entry_label(entry_id, "urgent")
        second_attach = remote.add_entry_label(entry_id, "urgent")
        listed = remote.list_labels()

        self.assertEqual(created.data, RemoteLabel("urgent", "ff0000", "Do first"))
        self.assertEqual(ensured.data, RemoteLabel("urgent", "ff0000", "Do first"))
        self.assertEqual([label.name for label in first_attach.data.labels], ["urgent"])
        self.assertEqual([label.name for label in second_attach.data.labels], ["urgent"])
        self.assertEqual(listed.data.labels, [RemoteLabel("urgent", "ff0000", "Do first")])

    def test_comments_and_reactions_are_returned_on_entry_details(self) -> None:
        tmp, remote = self.make_remote(author="alice")
        self.addCleanup(tmp.cleanup)
        entry_id = remote.add_entry("Discuss").data.id
        comment_id = remote.add_entry_comment(entry_id, "Looks good").data.id

        reaction = remote.add_entry_comment_reaction(entry_id, comment_id, "thumbs_up")
        remote.add_entry_comment_reaction(entry_id, comment_id, "thumbs_up")
        details = remote.get_entry(entry_id)

        self.assertTrue(reaction.ok)
        self.assertEqual(reaction.data.entry_id, entry_id)
        self.assertEqual(reaction.data.comment_id, comment_id)
        self.assertEqual(reaction.data.reaction, "thumbs_up")
        self.assertEqual(len(details.data.comments), 1)
        self.assertEqual(details.data.comments[0].body, "Looks good")
        self.assertEqual(
            details.data.comments[0].reactions,
            [RemoteReaction("thumbs_up", count=2, users=["alice", "alice"])],
        )

    def test_error_envelopes_for_missing_entries_and_bad_input(self) -> None:
        tmp, remote = self.make_remote()
        self.addCleanup(tmp.cleanup)
        entry_id = remote.add_entry("Has comment").data.id
        comment_id = remote.add_entry_comment(entry_id, "body").data.id

        missing = remote.get_entry(999)
        bad_title = remote.add_entry("")
        bad_comment = remote.add_entry_comment(entry_id, "")
        bad_reaction = remote.add_entry_comment_reaction(entry_id, comment_id, "confetti")
        missing_comment = remote.add_entry_comment_reaction(entry_id, 999, "eyes")

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

    def test_sync_from_remote_copies_local_database_in_action_order(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source_path = Path(tmp.name) / "remote_local1.db"
        target_path = Path(tmp.name) / "remote_local2.db"
        source = RemoteLocal(db_path=source_path, author="alice")
        target = RemoteLocal(db_path=target_path, author="bob")

        source.create_label("tier:epic", color="111111", description="Epics")
        source.create_label("tier:issue", color="222222", description="Issues")
        epic_id = source.add_entry("Epic", labels=["tier:epic"]).data.id
        issue_id = source.add_entry("Issue", labels=["tier:issue"]).data.id
        source.pin_entry(epic_id)
        comment_id = source.add_entry_comment(issue_id, "Source comment").data.id
        source.add_entry_comment_reaction(issue_id, comment_id, "heart")

        result = target.sync_from_remote(source)

        self.assertTrue(result.ok)
        self.assertTrue(all(isinstance(change, RemoteSyncChange) for change in result.data))
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

        synced_issue = target.get_entry(issue_id)
        self.assertTrue(synced_issue.ok)
        self.assertEqual(synced_issue.data.comments[0].body, "Source comment")
        self.assertEqual(synced_issue.data.comments[0].reactions, [RemoteReaction("heart", 1, ["alice"])])

        second = target.sync_from_remote(source)
        self.assertTrue(second.ok)
        self.assertEqual(second.data, [])

    def test_sync_from_remote_records_updates_and_deletions(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source_path = Path(tmp.name) / "remote_local1.db"
        target_path = Path(tmp.name) / "remote_local2.db"
        source = RemoteLocal(db_path=source_path, author="alice")
        target = RemoteLocal(db_path=target_path, author="bob")

        entry_id = source.add_entry("Original", body="Body", labels=["bug"]).data.id
        comment_id = source.add_entry_comment(entry_id, "Keep me").data.id
        source.add_entry_comment_reaction(entry_id, comment_id, "eyes")
        target.sync_from_remote(source)

        with sqlite3.connect(source_path) as conn:
            conn.execute(
                "UPDATE entries SET title = ?, updated_at = ? WHERE id = ?",
                ("Updated", "2999-01-01T00:00:00Z", entry_id),
            )
            conn.execute("DELETE FROM comment_reactions WHERE id = 1")
            conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            conn.execute("DELETE FROM entry_labels WHERE entry_id = ? AND label_name = ?", (entry_id, "bug"))

        result = target.sync_from_remote(source)

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
        details = target.get_entry(entry_id)
        self.assertTrue(details.ok)
        self.assertEqual(details.data.title, "Updated")
        self.assertEqual(details.data.comments, [])
        self.assertEqual(details.data.labels, [])


if __name__ == "__main__":
    unittest.main()
