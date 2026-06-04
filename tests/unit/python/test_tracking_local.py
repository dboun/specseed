"""tracking_local.py - local sqlite TrackingBase implementation.

Syncs are exercised the way the real system works: a TrackingRemoteLocal stands
in for the remote source of truth, and a TrackingLocal is the local copy synced
from it. No GitHub/GitLab is involved.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest import mock

from src.target_facing.specseed_target_src.tracking import tracking_local
from src.target_facing.specseed_target_src.tracking.tracking_base import (
    TrackingEntryDetails,
    TrackingLabel,
    TrackingReaction,
    TrackingSyncChange,
)
from src.target_facing.specseed_target_src.tracking.tracking_local import TrackingLocal
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import TrackingRemoteLocal


class _FrozenDateTime(datetime):
    """A datetime whose now() is frozen; strptime/arithmetic still real."""

    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return datetime(2026, 6, 4, 1, 2, 3, tzinfo=tz)


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

    def test_edit_entry_changes_title_and_body_independently(self) -> None:
        tmp, local = self.make_local()
        self.addCleanup(tmp.cleanup)
        entry_id = local.add_entry("Old title", body="Old body").data.id

        body_only = local.edit_entry(entry_id, body="New body")
        title_only = local.edit_entry(entry_id, title="New title")

        self.assertTrue(body_only.ok)
        self.assertTrue(title_only.ok)
        details = local.get_entry(entry_id).data
        self.assertEqual(details.title, "New title")
        self.assertEqual(details.body, "New body")

        self.assertFalse(local.edit_entry(entry_id).ok)  # nothing to edit
        self.assertFalse(local.edit_entry(entry_id, title="  ").ok)  # blank title
        self.assertFalse(local.edit_entry(999, body="x").ok)  # missing entry

    def test_remove_entry_label_is_idempotent_and_supports_status_swap(self) -> None:
        tmp, local = self.make_local()
        self.addCleanup(tmp.cleanup)
        entry_id = local.add_entry(
            "Work item", labels=["ticket", "ticket:status:todo"]
        ).data.id

        local.add_entry_label(entry_id, "ticket:status:in_progress")
        removed = local.remove_entry_label(entry_id, "ticket:status:todo")
        removed_again = local.remove_entry_label(entry_id, "ticket:status:todo")

        self.assertTrue(removed.ok)
        names = {label.name for label in removed.data.labels}
        self.assertEqual(names, {"ticket", "ticket:status:in_progress"})
        self.assertTrue(removed_again.ok)  # already gone -> still ok
        self.assertFalse(local.remove_entry_label(999, "x").ok)  # missing entry

    def test_delete_entry_removes_it_and_cascades(self) -> None:
        tmp, local = self.make_local()
        self.addCleanup(tmp.cleanup)
        entry_id = local.add_entry("Doomed", labels=["epic"]).data.id
        local.add_entry_comment(entry_id, "a comment")
        local.pin_entry(entry_id)

        deleted = local.delete_entry(entry_id)

        self.assertTrue(deleted.ok)
        self.assertEqual(deleted.data.id, entry_id)
        self.assertFalse(local.get_entry(entry_id).ok)
        self.assertFalse(local.delete_entry(entry_id).ok)  # already gone
        with sqlite3.connect(local.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM comments WHERE entry_id = ?", (entry_id,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM entry_labels WHERE entry_id = ?", (entry_id,)).fetchone()[0],
                0,
            )

    def test_sync_detects_remote_entry_deletion(self) -> None:
        tmp_r, remote = self.make_local("remote")
        tmp_l, local = self.make_local("local")
        self.addCleanup(tmp_r.cleanup)
        self.addCleanup(tmp_l.cleanup)
        entry_id = remote.add_entry("Mirror me").data.id
        local.sync_from_remote(remote)
        self.assertTrue(local.get_entry(entry_id).ok)

        remote.delete_entry(entry_id)
        changes = local.sync_from_remote(remote)

        self.assertTrue(changes.ok)
        self.assertTrue(
            any(c.resource_type == "entry" and c.action == "delete" for c in changes.data)
        )
        self.assertFalse(local.get_entry(entry_id).ok)

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


    def test_entry_reactions_are_returned_on_entry_details(self) -> None:
        tmp, local = self.make_local(author="alice")
        self.addCleanup(tmp.cleanup)
        entry_id = local.add_entry("Approve me").data.id

        reaction = local.add_entry_reaction(entry_id, "thumbs_up")
        local.add_entry_reaction(entry_id, "thumbs_up")
        details = local.get_entry(entry_id)

        self.assertTrue(reaction.ok)
        self.assertEqual(reaction.data.entry_id, entry_id)
        self.assertEqual(reaction.data.reaction, "thumbs_up")
        self.assertEqual(
            details.data.reactions,
            [TrackingReaction("thumbs_up", count=2, users=["alice", "alice"])],
        )

    def test_entry_reaction_error_envelopes(self) -> None:
        tmp, local = self.make_local()
        self.addCleanup(tmp.cleanup)
        entry_id = local.add_entry("E").data.id

        bad = local.add_entry_reaction(entry_id, "confetti")
        missing = local.add_entry_reaction(999, "eyes")

        self.assertFalse(bad.ok)
        self.assertEqual(bad.error, "unsupported reaction: confetti")
        self.assertFalse(missing.ok)
        self.assertEqual(missing.error, "entry not found: 999")

    def test_sync_copies_and_deletes_entry_reactions(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        remote = TrackingRemoteLocal(db_path=root / "tracking_remote_local.db", author="alice")
        local = TrackingLocal(db_path=root / "tracking_local.db", author="bob")

        entry_id = remote.add_entry("Gate").data.id
        remote.add_entry_reaction(entry_id, "thumbs_up")

        result = local.sync_from_remote(remote)
        self.assertTrue(result.ok)
        created = [(c.action, c.resource_type, c.parent_id) for c in result.data]
        self.assertIn(("create", "entry_reaction", entry_id), created)
        synced = local.get_entry(entry_id)
        self.assertEqual(synced.data.reactions, [TrackingReaction("thumbs_up", 1, ["alice"])])

        # second sync is a no-op
        self.assertEqual(local.sync_from_remote(remote).data, [])

        # delete it on the remote and confirm the deletion syncs down
        with sqlite3.connect(root / "tracking_remote_local.db") as conn:
            conn.execute("DELETE FROM entry_reactions")
            conn.execute(
                "UPDATE entries SET updated_at = ? WHERE id = ?",
                ("2999-01-01T00:00:00Z", entry_id),
            )
        deletion = local.sync_from_remote(remote)
        self.assertTrue(deletion.ok)
        self.assertIn(
            ("delete", "entry_reaction"),
            [(c.action, c.resource_type) for c in deletion.data],
        )
        self.assertEqual(local.get_entry(entry_id).data.reactions, [])

    def test_now_is_strictly_increasing_even_with_a_frozen_clock(self) -> None:
        # Two writes in the same instant must still get distinct, ordered stamps,
        # otherwise sync_from_remote would miss the second one.
        with mock.patch.object(tracking_local, "_last_stamp", ""), mock.patch.object(
            tracking_local, "datetime", _FrozenDateTime
        ):
            stamps = [tracking_local._now() for _ in range(5)]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len(set(stamps)), len(stamps))

    def test_two_same_instant_updates_are_both_detected_by_sync(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        remote = TrackingRemoteLocal(db_path=root / "tracking_remote_local.db", author="alice")
        local = TrackingLocal(db_path=root / "tracking_local.db", author="bob")

        entry_id = remote.add_entry("E").data.id
        local.sync_from_remote(remote)

        # Freeze the clock so both updates land on the same wall-clock instant;
        # the monotonic guard must still make them distinguishable.
        with mock.patch.object(tracking_local, "datetime", _FrozenDateTime):
            remote.set_entry_closed(entry_id)
            result = local.sync_from_remote(remote)

        actions = [(c.action, c.resource_type, c.field) for c in result.data]
        self.assertIn(("state", "entry", "is_open"), actions)


if __name__ == "__main__":
    unittest.main()
