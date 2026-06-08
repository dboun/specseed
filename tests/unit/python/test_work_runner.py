"""test_work_runner.py - the WORK lane handlers + the result handoff.

Covers the mechanics introduced by the two-lane split (the lifecycle-transition
correctness is exercised end to end by test_lifecycle_integration via run_once):

* run_agent_job persists the outcome + enqueues a process_work_result control item;
* a stale implement is skipped on the work side;
* quota / entity-gone return without a handoff;
* process_work_result consumes (deletes) the outcome file;
* the persisted-outcome round trip.

FakeAgentRunner + TrackingRemoteLocal/TrackingLocal mirrors. No GitHub/GitLab, no
real agent.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from specseed_runtime.db.database import Database, LANE_CONTROL, LANE_WORK
from specseed_runtime.executing import priorities
from specseed_runtime.executing import work_lane
from specseed_runtime.executing import work_runner
from specseed_runtime.executing.agent_runner import AgentResult, FakeAgentRunner
from specseed_runtime.executing.context import ExecutionContext
from specseed_runtime.executing.permissions import Permissions
from specseed_runtime.entities import issue as _issue  # noqa: F401  (registers tier)
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal


def _config():
    return {"specseed_dir": "seedmeta", "approvals": {"approver_usernames": ["alice"]}, "permissions": {}}


class WorkRunnerBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="alice")
        self.local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        self.db = Database(db_path=self.root / "queue.db")
        self.config = _config()

    def _ctx(self, runner):
        return ExecutionContext(
            db=self.db, local=self.local, remote=self.remote, config=self.config,
            permissions=Permissions(self.config), runner=runner, repo_root=self.root,
            storage=self.root / "storage", cancel=threading.Event(), agent_timeout_s=30.0,
        )

    def _seed_local(self, title, labels):
        for label in labels:
            self.local.create_label(label)
        return self.local.add_entry(title, labels=labels).data.id

    def _seed_both(self, title, labels):
        eid = self._seed_local(title, labels)
        for label in labels:
            self.remote.create_label(label)
        self.remote.add_entry(title, labels=labels)
        return eid


class RunAgentJobTest(WorkRunnerBase):
    def test_success_persists_outcome_and_enqueues_result(self) -> None:
        eid = self._seed_local("Do it", ["issue", "issue:status:in_progress"])
        runner = FakeAgentRunner(result=AgentResult(ok=True, report={"status": "done", "summary": "x"}))
        ctx = self._ctx(runner)
        task = {"task_id": 7, "action": work_lane.WORK_RUN, "post_id": str(eid),
                "payload": {"intent": "implement", "post_id": str(eid)}}

        out = work_runner.run_agent_job(ctx, task)

        self.assertTrue(out.success)
        self.assertEqual(len(runner.calls), 1)  # the agent really ran
        # the outcome file exists and the control result item was enqueued (prio 100).
        self.assertIsNotNone(work_runner.read_work_outcome(ctx, 7))
        result_tasks = [t for t in self._all_tasks() if t["action"] == work_lane.PROCESS_WORK_RESULT]
        self.assertEqual(len(result_tasks), 1)
        self.assertEqual(result_tasks[0]["lane"], LANE_CONTROL)
        self.assertEqual(result_tasks[0]["priority"], priorities.CONTROL_WORK_RESULT)
        self.assertEqual(result_tasks[0]["payload"]["work_task_id"], 7)

    def test_stale_implement_is_skipped(self) -> None:
        # local says in_progress; remote already moved to done -> stale, skip.
        eid = self._seed_both("Do it", ["issue", "issue:status:in_progress"])
        # advance the remote past the judged status
        self.remote.add_entry_label(self.remote.list_entries().data[0].id, "issue:status:done")
        self.remote.remove_entry_label(self.remote.list_entries().data[0].id, "issue:status:in_progress")
        runner = FakeAgentRunner(result=AgentResult(ok=True, report={"status": "done"}))
        ctx = self._ctx(runner)
        task = {"task_id": 9, "action": work_lane.WORK_RUN, "post_id": str(eid),
                "payload": {"intent": "implement", "post_id": str(eid)}}

        out = work_runner.run_agent_job(ctx, task)

        self.assertTrue(out.success)
        self.assertEqual(runner.calls, [])  # the agent never ran
        self.assertIsNone(work_runner.read_work_outcome(ctx, 9))

    def test_entity_gone_is_noop(self) -> None:
        runner = FakeAgentRunner()
        ctx = self._ctx(runner)
        out = work_runner.run_agent_job(
            ctx, {"task_id": 1, "action": work_lane.WORK_RUN, "post_id": "999",
                  "payload": {"intent": "implement", "post_id": "999"}}
        )
        self.assertTrue(out.success)
        self.assertEqual(runner.calls, [])

    def test_quota_returns_without_handoff(self) -> None:
        eid = self._seed_local("Do it", ["issue", "issue:status:in_progress"])
        runner = FakeAgentRunner(result=AgentResult(ok=False, quota_exhausted=True, quota_reset_hint="2026-06-08T00:00:00Z"))
        ctx = self._ctx(runner)
        out = work_runner.run_agent_job(
            ctx, {"task_id": 3, "action": work_lane.WORK_RUN, "post_id": str(eid),
                  "payload": {"intent": "implement", "post_id": str(eid)}}
        )
        self.assertTrue(out.requeue)
        self.assertTrue(out.quota)
        self.assertIsNone(work_runner.read_work_outcome(ctx, 3))
        self.assertEqual([t for t in self._all_tasks() if t["action"] == work_lane.PROCESS_WORK_RESULT], [])

    def test_missing_report_is_retryable(self) -> None:
        eid = self._seed_local("Do it", ["issue", "issue:status:in_progress"])
        runner = FakeAgentRunner(result=AgentResult(ok=True, report=None))
        ctx = self._ctx(runner)
        out = work_runner.run_agent_job(
            ctx, {"task_id": 4, "action": work_lane.WORK_RUN, "post_id": str(eid),
                  "payload": {"intent": "implement", "post_id": str(eid)}}
        )
        self.assertFalse(out.success)
        self.assertTrue(out.retryable)
        self.assertIsNone(work_runner.read_work_outcome(ctx, 4))

    def _all_tasks(self):
        return [t for t in (self.db.get_task(i) for i in range(1, 50)) if t is not None]


class ProcessWorkResultTest(WorkRunnerBase):
    def test_missing_outcome_file_is_noop(self) -> None:
        ctx = self._ctx(FakeAgentRunner())
        out = work_runner.process_work_result(
            ctx, {"task_id": 1, "payload": {"work_task_id": 123, "intent": "implement", "post_id": "1"}}
        )
        self.assertTrue(out.success)

    def test_consumes_outcome_file(self) -> None:
        eid = self._seed_both("Do it", ["issue", "issue:status:in_review"])
        ctx = self._ctx(FakeAgentRunner())
        work_runner.write_work_outcome(
            ctx, 50, "review", eid,
            AgentResult(ok=True, report={"verdict": "approve", "confidence": 0.99, "summary": "ok"}),
        )
        self.assertIsNotNone(work_runner.read_work_outcome(ctx, 50))
        work_runner.process_work_result(
            ctx, {"task_id": 2, "payload": {"work_task_id": 50, "intent": "review", "post_id": str(eid)}}
        )
        # the outcome file is consumed exactly once.
        self.assertIsNone(work_runner.read_work_outcome(ctx, 50))


class OutcomeRoundTripTest(WorkRunnerBase):
    def test_round_trip(self) -> None:
        ctx = self._ctx(FakeAgentRunner())
        work_runner.write_work_outcome(
            ctx, 11, "implement", "5",
            AgentResult(ok=True, returncode=0, report={"status": "done"}, stdout="hello"),
        )
        data = work_runner.read_work_outcome(ctx, 11)
        self.assertEqual(data["intent"], "implement")
        self.assertEqual(data["report"], {"status": "done"})
        result = work_runner._reconstruct_result(data)
        self.assertTrue(result.ok)
        self.assertEqual(result.report, {"status": "done"})


if __name__ == "__main__":
    unittest.main()
