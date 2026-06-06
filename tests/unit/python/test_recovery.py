"""test_recovery.py - failure recovery: backoff retries, platform_error posts,
resolve-agent engagement, the recursion guard, and the close-to-cancel switch.

TrackingRemoteLocal stands in for the remote; no agent, no tokens.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from specseed_runtime.db.database import Database
from specseed_runtime.executing import recovery
from specseed_runtime.executing.dispatch import HandlerOutcome
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal


def _outcome(retryable: bool = True, error: str = "agent exited with code 1") -> HandlerOutcome:
    return HandlerOutcome(success=False, error=error, retryable=retryable)


class BackoffTest(unittest.TestCase):
    def test_schedule_1_5_15_then_repeating(self) -> None:
        self.assertEqual(recovery.retry_delay_s(1), 60)
        self.assertEqual(recovery.retry_delay_s(2), 300)
        self.assertEqual(recovery.retry_delay_s(3), 900)
        self.assertEqual(recovery.retry_delay_s(9), 900)


class RecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote = TrackingRemoteLocal(db_path=root / "remote.db", author="bot")
        self.remote.create_label(recovery.PLATFORM_ERROR_LABEL)
        self.db = Database(db_path=root / "queue.db")
        self.config = {"recovery": {"enabled": True, "max_retries": 2}}

    def _failed_task(self, action: str = "handle_label_removed") -> dict:
        task_id = self.db.enqueue(action, post_id="5", payload={})
        task = self.db.claim_next()
        self.db.complete(task_id, False, "agent exited with code 1")
        return task

    def _fail(self, task: dict) -> dict:
        return recovery.on_failure(
            db=self.db, config=self.config, remote=self.remote,
            task=task, outcome=_outcome(),
        )

    def _error_posts(self) -> list:
        listed = self.remote.list_entries(is_open=None)
        return [e for e in listed.data if str(e.title).startswith("Platform error:")]

    # -- first failure ----------------------------------------------------- #
    def test_first_failure_creates_post_schedules_retry_engages_agent(self) -> None:
        task = self._failed_task()
        summary = self._fail(task)

        self.assertTrue(summary["retried"])
        self.assertTrue(summary["agent"])
        # retry scheduled: pending with a future not_before -> not yet claimable
        row = self.db.get_task(task["task_id"])
        self.assertEqual(row["status"], "pending")
        self.assertIsNotNone(row["not_before"])
        # the only claimable task is the resolve-agent engagement
        claimed = self.db.claim_next()
        self.assertEqual(claimed["action"], recovery.PLATFORM_ERROR_ACTION)
        self.assertEqual(claimed["payload"]["origin_task_id"], task["task_id"])
        self.assertEqual(claimed["payload"]["reason"], "new")
        # post exists with the label, the marker, and the conversation note
        posts = self._error_posts()
        self.assertEqual(len(posts), 1)
        detail = self.remote.get_entry(posts[0].id).data
        self.assertIn("specseed:platform-error task={0}".format(task["task_id"]), detail.body)
        self.assertIn("agent is taking a look", detail.body)
        self.assertIn(
            recovery.PLATFORM_ERROR_LABEL,
            [getattr(label, "name", str(label)) for label in detail.labels],
        )

    # -- repeat failure ---------------------------------------------------- #
    def test_second_failure_comments_instead_of_new_post(self) -> None:
        task = self._failed_task()
        self._fail(task)
        again = self.db.get_task(task["task_id"]) | {"attempts": 2}
        summary = self._fail(again)

        self.assertTrue(summary["retried"])
        self.assertFalse(summary["agent"])  # no second engagement while retrying
        posts = self._error_posts()
        self.assertEqual(len(posts), 1)  # still one post
        detail = self.remote.get_entry(posts[0].id).data
        bodies = [c.body for c in detail.comments]
        self.assertTrue(any("Retry #1 did not fix it" in b for b in bodies))
        self.assertTrue(all(b.startswith("specseed: ") for b in bodies))

    # -- exhaustion -------------------------------------------------------- #
    def test_exhaustion_stops_retries_and_escalates_to_agent(self) -> None:
        task = self._failed_task()
        self._fail(task)
        exhausted = self.db.get_task(task["task_id"]) | {"attempts": 3}  # > max_retries=2
        summary = self._fail(exhausted)

        self.assertFalse(summary["retried"])
        self.assertTrue(summary["agent"])
        self.assertEqual(self.db.get_task(task["task_id"])["status"], "pending")
        # ^ status pending is from the FIRST retry; the exhausted call added none:
        resolve_tasks = [
            t for t in (self.db.get_task(i) for i in range(1, 50))
            if t and t["action"] == recovery.PLATFORM_ERROR_ACTION
        ]
        reasons = {t["payload"]["reason"] for t in resolve_tasks}
        self.assertIn("exhausted", reasons)
        detail = self.remote.get_entry(self._error_posts()[0].id).data
        self.assertTrue(any("No more automatic retries" in c.body for c in detail.comments))

    # -- the cancel switch ------------------------------------------------- #
    def test_closing_the_post_stops_retries(self) -> None:
        task = self._failed_task()
        self._fail(task)
        post_id = self._error_posts()[0].id
        self.remote.set_entry_closed(post_id)

        again = self.db.get_task(task["task_id"]) | {"attempts": 2}
        self.db.complete(task["task_id"], False, "boom")  # back to failed
        summary = self._fail(again)

        self.assertFalse(summary["retried"])
        self.assertFalse(summary["agent"])
        self.assertEqual(self.db.get_task(task["task_id"])["status"], "failed")

    # -- guards ------------------------------------------------------------ #
    def test_non_retryable_failure_does_nothing(self) -> None:
        task = self._failed_task()
        summary = recovery.on_failure(
            db=self.db, config=self.config, remote=self.remote,
            task=task, outcome=_outcome(retryable=False),
        )
        self.assertEqual(summary, {"retried": False, "post_id": None, "agent": False})
        self.assertEqual(self._error_posts(), [])

    def test_recovery_never_recovers_itself(self) -> None:
        task = self._failed_task(action=recovery.PLATFORM_ERROR_ACTION)
        summary = self._fail(task)
        self.assertEqual(summary, {"retried": False, "post_id": None, "agent": False})
        self.assertEqual(self._error_posts(), [])

    def test_disabled_recovery_does_nothing(self) -> None:
        task = self._failed_task()
        summary = recovery.on_failure(
            db=self.db, config={"recovery": {"enabled": False}}, remote=self.remote,
            task=task, outcome=_outcome(),
        )
        self.assertFalse(summary["retried"])
        self.assertEqual(self._error_posts(), [])

    def test_no_remote_still_schedules_retry(self) -> None:
        task = self._failed_task()
        summary = recovery.on_failure(
            db=self.db, config=self.config, remote=None, task=task, outcome=_outcome(),
        )
        self.assertTrue(summary["retried"])
        self.assertIsNone(summary["post_id"])

    # -- recovery ----------------------------------------------------------- #
    def test_success_after_retry_comments_and_closes_post(self) -> None:
        task = self._failed_task()
        self._fail(task)
        post_id = self._error_posts()[0].id

        recovered = self.db.get_task(task["task_id"]) | {"attempts": 2}
        recovery.on_recovered(
            db=self.db, config=self.config, remote=self.remote, task=recovered
        )

        detail = self.remote.get_entry(post_id).data
        self.assertFalse(bool(detail.is_open))
        self.assertTrue(any("Recovered" in c.body for c in detail.comments))


class ClaimHonorsNotBeforeTest(unittest.TestCase):
    def test_future_not_before_is_not_claimable_past_is(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Database(db_path=Path(tmp.name) / "q.db")
        task_id = db.enqueue("handle_entry_created", post_id="1", payload={})
        db.claim_next()
        db.requeue(task_id, not_before="2999-01-01T00:00:00Z")
        self.assertIsNone(db.claim_next())
        db.requeue(task_id, not_before="2000-01-01T00:00:00Z")
        self.assertIsNotNone(db.claim_next())


if __name__ == "__main__":
    unittest.main()
