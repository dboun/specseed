"""Pull request tracking and dev-only clearing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.target_facing.specseed_target_src.tracking.clear_everything import clear_everything
from src.target_facing.specseed_target_src.tracking.pull_request import TrackingPullRequestDetails
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import TrackingRemoteLocal


class TrackingPullRequestAndClearTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = TrackingRemoteLocal(
            db_path=Path(self.tmp.name) / "tracking_remote_local.db",
            author="alice",
        )

    def test_local_pull_request_round_trips_separately_from_posts(self) -> None:
        self.remote.create_label("review")
        created = self.remote.add_pull_request(
            "Implement worker",
            source_branch="feature/worker",
            target_branch="main",
            body="Ready for review.",
            labels=["review"],
            assignees=["bob"],
        )

        self.assertTrue(created.ok)
        listed = self.remote.list_pull_requests(labels=["review"], assignee="bob")
        details = self.remote.get_pull_request(created.data.id)
        posts = self.remote.list_entries()

        self.assertEqual([pr.id for pr in listed.data], [created.data.id])
        self.assertIsInstance(details.data, TrackingPullRequestDetails)
        self.assertEqual(details.data.source_branch, "feature/worker")
        self.assertEqual(details.data.target_branch, "main")
        self.assertEqual(details.data.body, "Ready for review.")
        self.assertEqual([label.name for label in details.data.labels], ["review"])
        self.assertEqual(posts.data, [])

    def test_clear_everything_hard_deletes_local_tracking_state(self) -> None:
        self.remote.create_label("review")
        self.remote.add_entry("Post", labels=["review"])
        self.remote.add_pull_request("PR", "feature/pr", "main", labels=["review"])

        summary = clear_everything("remote_local", tracker=self.remote)

        self.assertEqual(summary["mode"], "hard_delete")
        self.assertEqual(summary["posts"], 1)
        self.assertEqual(summary["pull_requests"], 1)
        self.assertEqual(summary["labels"], 1)
        self.assertEqual(self.remote.list_entries().data, [])
        self.assertEqual(self.remote.list_pull_requests().data, [])
        self.assertEqual(self.remote.list_labels().data.labels, [])

    def test_local_sync_copies_pull_requests_and_labels(self) -> None:
        local = TrackingRemoteLocal(
            db_path=Path(self.tmp.name) / "tracking_local.db",
            author="agent",
        )
        self.remote.create_label("review")
        pr_id = self.remote.add_pull_request(
            "PR",
            "feature/pr",
            "main",
            labels=["review"],
        ).data.id

        result = local.sync_from_remote(self.remote)

        self.assertTrue(result.ok)
        ordered = [(change.action, change.resource_type, change.resource_id) for change in result.data]
        self.assertIn(("create", "pull_request", pr_id), ordered)
        self.assertIn(("create", "pull_request_label", f"{pr_id}:review"), ordered)
        synced = local.get_pull_request(pr_id).data
        self.assertEqual(synced.source_branch, "feature/pr")
        self.assertEqual([label.name for label in synced.labels], ["review"])


if __name__ == "__main__":
    unittest.main()
