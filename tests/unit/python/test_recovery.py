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
    def test_first_failure_creates_post_and_schedules_retry_no_agent_yet(self) -> None:
        task = self._failed_task()
        summary = self._fail(task)

        self.assertTrue(summary["retried"])
        self.assertFalse(summary["agent"])  # early retries run before any agent
        # retry scheduled: pending with a future not_before -> not yet claimable
        row = self.db.get_task(task["task_id"])
        self.assertEqual(row["status"], "pending")
        self.assertIsNotNone(row["not_before"])
        self.assertIsNone(self.db.claim_next())  # nothing else queued
        # post exists with the label, the marker, and the plan
        posts = self._error_posts()
        self.assertEqual(len(posts), 1)
        detail = self.remote.get_entry(posts[0].id).data
        self.assertIn("specseed:platform-error task={0}".format(task["task_id"]), detail.body)
        self.assertIn("an agent takes a look", detail.body)
        self.assertIn("Closing this post stops the retries", detail.body)
        self.assertIn(
            recovery.PLATFORM_ERROR_LABEL,
            [getattr(label, "name", str(label)) for label in detail.labels],
        )

    def test_agent_engages_on_third_failure(self) -> None:
        config = {"recovery": {"enabled": True, "max_retries": 5}}
        task = self._failed_task()

        def fail(attempts: int) -> dict:
            current = self.db.get_task(task["task_id"]) | {"attempts": attempts}
            return recovery.on_failure(
                db=self.db, config=config, remote=self.remote,
                task=current, outcome=_outcome(),
            )

        self.assertFalse(fail(1)["agent"])
        self.assertFalse(fail(2)["agent"])
        third = fail(3)
        self.assertTrue(third["agent"])
        self.assertTrue(third["retried"])  # retries continue past engagement
        self.assertFalse(fail(4)["agent"])  # engaged once, not every failure
        resolve_tasks = [
            t for t in (self.db.get_task(i) for i in range(1, 50))
            if t and t["action"] == recovery.PLATFORM_ERROR_ACTION
        ]
        self.assertEqual(len(resolve_tasks), 1)
        self.assertEqual(resolve_tasks[0]["payload"]["reason"], "new")
        # the engagement is announced on the thread
        detail = self.remote.get_entry(self._error_posts()[0].id).data
        self.assertTrue(
            any("agent is taking a look now" in c.body for c in detail.comments)
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
    def test_non_retryable_failure_posts_and_engages_agent_no_retry(self) -> None:
        # A deterministic failure can't be fixed by re-running, but it must still
        # surface: a platform_error post + the resolve agent, with NO retry queued.
        task = self._failed_task()
        summary = recovery.on_failure(
            db=self.db, config=self.config, remote=self.remote,
            task=task, outcome=_outcome(retryable=False, error="missing apr.id"),
        )
        self.assertFalse(summary["retried"])
        self.assertTrue(summary["agent"])
        self.assertIsNotNone(summary["post_id"])
        # the failed task stays failed - no retry scheduled
        self.assertEqual(self.db.get_task(task["task_id"])["status"], "failed")
        # one post, body says retries are off and the raw error is quoted
        posts = self._error_posts()
        self.assertEqual(len(posts), 1)
        detail = self.remote.get_entry(posts[0].id).data
        self.assertIn("Automatic retries are off", detail.body)
        self.assertIn("missing apr.id", detail.body)
        # the resolve agent is engaged with the non-retryable reason
        resolve_tasks = [
            t for t in (self.db.get_task(i) for i in range(1, 50))
            if t and t["action"] == recovery.PLATFORM_ERROR_ACTION
        ]
        self.assertEqual(len(resolve_tasks), 1)
        self.assertEqual(resolve_tasks[0]["payload"]["reason"], "fatal")

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

    # -- retry execution gate ----------------------------------------------- #
    def test_retry_cancelled_true_only_for_closed_post_and_prior_attempts(self) -> None:
        task = self._failed_task()
        self._fail(task)
        retried = self.db.get_task(task["task_id"]) | {"attempts": 2}

        # post open -> retry runs
        self.assertFalse(recovery.retry_cancelled(self.remote, retried))

        # human closes the post -> queued retry must not run
        self.remote.set_entry_closed(self._error_posts()[0].id)
        self.assertTrue(recovery.retry_cancelled(self.remote, retried))

        # first run is never gated, even with a closed post around
        first = dict(retried) | {"attempts": 1}
        self.assertFalse(recovery.retry_cancelled(self.remote, first))

    def test_retry_cancelled_safe_without_remote_or_post(self) -> None:
        task = self._failed_task()
        retried = self.db.get_task(task["task_id"]) | {"attempts": 2}
        self.assertFalse(recovery.retry_cancelled(None, retried))
        self.assertFalse(recovery.retry_cancelled(self.remote, retried))  # no post

    def test_retry_cancelled_never_gates_platform_error_action(self) -> None:
        task = self._failed_task(action=recovery.PLATFORM_ERROR_ACTION)
        # closed error post with this task's marker exists; the action guard
        # must still win (recovery never recovers itself).
        created = self.remote.add_entry(
            recovery.error_post_title(task),
            body=recovery._marker_line(task["task_id"]),
            labels=[recovery.PLATFORM_ERROR_LABEL],
        )
        self.assertTrue(created.ok)
        self.assertTrue(self.remote.set_entry_closed(created.data.id).ok)
        retried = self.db.get_task(task["task_id"]) | {"attempts": 2}
        self.assertFalse(recovery.retry_cancelled(self.remote, retried))


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
