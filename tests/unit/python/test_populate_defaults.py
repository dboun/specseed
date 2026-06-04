"""populate_defaults.py - default label/post seeding for tracking backends."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.target.specseed_target_src.tracking.populate_defaults import (
    DEFAULT_POSTS,
    DESIRED_LABELS,
    populate_defaults,
)
from src.target.specseed_target_src.tracking.tracking_remote_local import TrackingRemoteLocal


class PopulateDefaultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = TrackingRemoteLocal(
            db_path=Path(self.tmp.name) / "tracking_remote_local.db",
            author="alice",
        )

    def label_names(self) -> set[str]:
        return {label.name for label in self.remote.list_labels().data.labels}

    def entry(self, title: str):
        entries = self.remote.list_entries().data
        return next(entry for entry in entries if entry.title == title)

    def test_populates_labels_default_posts_and_drafts_unlabeled_entries(self) -> None:
        self.remote.create_label("duplicate")
        self.remote.create_label("enhancement")
        unlabelled_id = self.remote.add_entry("Inbox question").data.id
        old_labelled_id = self.remote.add_entry("Old labelled", labels=["duplicate"]).data.id
        existing_roadmap_id = self.remote.add_entry(
            "ROADMAP",
            body="Keep this body",
            labels=["duplicate"],
        ).data.id

        summary = populate_defaults("remote_local", tracker=self.remote)

        self.assertEqual(set(summary["deleted_labels"]), {"duplicate", "enhancement"})
        self.assertEqual(self.label_names(), DESIRED_LABELS)

        titles = {entry.title for entry in self.remote.list_entries().data}
        self.assertTrue({title for title, *_ in DEFAULT_POSTS}.issubset(titles))

        roadmap = self.remote.get_entry(existing_roadmap_id).data
        self.assertEqual(roadmap.body, "Keep this body")
        self.assertIn("management", [label.name for label in roadmap.labels])

        current_sprint = self.entry("Current sprint")
        self.assertEqual(
            {label.name for label in current_sprint.labels},
            {"current_sprint", "management"},
        )

        drafted_ids = set(summary["drafted_entries"])
        self.assertIn(unlabelled_id, drafted_ids)
        self.assertIn(old_labelled_id, drafted_ids)

        with sqlite3.connect(self.remote.db_path) as conn:
            pinned = {row[0] for row in conn.execute("SELECT entry_id FROM pinned_entries")}
        self.assertEqual(
            pinned,
            {
                self.entry("TIMELINE").id,
                existing_roadmap_id,
                self.entry("CONTROL").id,
            },
        )

    def test_second_run_is_idempotent(self) -> None:
        first = populate_defaults("remote_local", tracker=self.remote)
        second = populate_defaults("remote_local", tracker=self.remote)

        self.assertEqual(second["deleted_labels"], [])
        self.assertEqual(second["drafted_entries"], [])
        self.assertTrue(all(not item["created"] for item in second["default_posts"].values()))
        self.assertEqual(
            len(self.remote.list_entries().data),
            len(first["default_posts"]),
        )


if __name__ == "__main__":
    unittest.main()
