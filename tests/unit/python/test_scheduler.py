"""test_scheduler.py - the poll -> sync -> drain loop end to end.

Drives the Scheduler with a TrackingRemoteLocal source of truth, a TrackingLocal
mirror, an injected Database queue, and a FakeAgentRunner, so nothing touches a
real provider, a real agent, or the network. Covers:

* a spec-change post on the remote flows poll -> enqueue -> drain -> agent run;
* an enqueued spec-change script runs through the worker thread and mutates the
  filesystem;
* CONTROL commands (pause / start / stop) map to scheduler state;
* a hard stop cancels the in-flight task and the loop thread joins cleanly.

No GitHub/GitLab.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from specseed_runtime.db.database import Database, LANE_CONTROL, LANE_WORK
from specseed_runtime.executing import cancellation
from specseed_runtime.executing.agent_runner import (
    AgentResult,
    ClaudeAgentRunner,
    CodexAgentRunner,
    FakeAgentRunner,
)
from specseed_runtime.executing.control import CONTROL_TITLE
from specseed_runtime.executing.scheduler import (
    PAUSED,
    RUNNING,
    STOPPED,
    Scheduler,
)
from specseed_runtime.scheduling.spec_change import SPEC_CHANGE_ACTION
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


def _config(*, approvers=("alice",)):
    return {
        "approvals": {"approver_usernames": list(approvers)},
        "permissions": {
            "remote": {
                "post_control": True,
            }
        },
    }


class SchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        cancellation.reset()
        self.addCleanup(cancellation.reset)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text("*.db\n*.db-*\nstorage/\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-m", "root"],
            cwd=self.root, check=True, capture_output=True,
        )
        # The bot owns the remote; operators comment as humans.
        self.remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="bot")
        self.local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        self.db = Database(db_path=self.root / "queue.db")
        self.runner = FakeAgentRunner()

    def _scheduler(self, *, runner=None, config=None) -> Scheduler:
        return Scheduler(
            db=self.db,
            runner=runner if runner is not None else self.runner,
            config=config or _config(),
            storage=self.root / "storage",
            repo_root=self.root,
            remote=self.remote,
            local=self.local,
            poll_interval=0.0,  # always due, so each pass syncs
            tick=0.05,
        )

    def _control_comment(self, author, body):
        prev = self.remote.author
        self.remote.author = author
        try:
            return self.remote.add_entry_comment(self._control_id, body).data.id
        finally:
            self.remote.author = prev

    # -- sync -> drain --------------------------------------------------- #
    def test_run_once_drives_spec_change_post_to_agent(self) -> None:
        self.remote.create_label("spec-change:adapt")
        self.remote.add_entry("Adapt the widget", labels=["spec-change:adapt"])

        sched = self._scheduler()
        summary = sched.run_once()

        self.assertTrue(summary.get("ok"))
        self.assertGreaterEqual(summary.get("drained", 0), 1)
        self.assertGreaterEqual(len(self.runner.calls), 1)
        prompt = self.runner.calls[0]["prompt"]
        self.assertIn("spec-change", prompt)
        self.assertIn("adapt", prompt)

    def test_run_once_writes_platform_log_under_storage(self) -> None:
        self.remote.create_label("spec-change:adapt")
        self.remote.add_entry("Adapt the widget", labels=["spec-change:adapt"])

        sched = self._scheduler()
        sched.run_once()

        log_path = self.root / "storage" / "logs" / "platform.log"
        self.assertTrue(log_path.exists())
        events = [
            json.loads(line)["event"]
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertIn("sync_to_db_complete", events)
        self.assertIn("task_enqueued", events)
        self.assertIn("task_complete", events)

    def test_run_once_runs_spec_change_script_through_worker(self) -> None:
        marker = self.root / "applied.txt"
        script_dir = self.root / "storage" / "spec-change" / "req1"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "apply.py").write_text(
            "from pathlib import Path\n"
            "Path(r'{0}').write_text('applied')\n".format(marker)
        )
        self.db.enqueue(
            SPEC_CHANGE_ACTION,
            post_id="req1",
            payload={"dir": str(script_dir), "script": "apply.py", "request_id": "req1"},
        )

        sched = self._scheduler()
        sched.run_once()

        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(), "applied")

    def test_spec_change_script_gets_target_storage_env(self) -> None:
        # apply.py resolves trackers via default_storage_dir(): the subprocess
        # must see SPECSEED_STORAGE = the target's storage, not the engine's.
        marker = self.root / "seen_env.txt"
        script_dir = self.root / "storage" / "spec-change" / "req-env"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "apply.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path(r'{0}').write_text(os.environ.get('SPECSEED_STORAGE', ''))\n".format(marker)
        )
        self.db.enqueue(
            SPEC_CHANGE_ACTION,
            post_id="req-env",
            payload={"dir": str(script_dir), "script": "apply.py", "request_id": "req-env"},
        )

        sched = self._scheduler()
        sched.run_once()

        self.assertEqual(marker.read_text(), str((self.root / "storage").resolve()))

    def test_idle_post_is_a_no_op(self) -> None:
        # An entry with no actionable status/route just gets bookkeeping success.
        self.remote.create_label("tier:epic")
        self.remote.add_entry("An epic", labels=["tier:epic"])
        sched = self._scheduler()
        sched.run_once()
        self.assertEqual(len(self.runner.calls), 0)  # epics are not implemented

    # -- CONTROL --------------------------------------------------------- #
    def test_control_pause_then_start(self) -> None:
        self._control_id = self.remote.add_entry(CONTROL_TITLE, body="control").data.id
        sched = self._scheduler()
        sched.resume()  # baseline RUNNING (scheduler starts PAUSED until start())
        self.assertEqual(sched.state, RUNNING)

        self._control_comment("alice", "pause")
        sched._process_control()
        self.assertEqual(sched.state, PAUSED)

        self._control_comment("alice", "start")
        sched._process_control()
        self.assertEqual(sched.state, RUNNING)

    def test_control_stop_requests_stop(self) -> None:
        self._control_id = self.remote.add_entry(CONTROL_TITLE, body="control").data.id
        sched = self._scheduler()
        self._control_comment("alice", "stop")
        sched._process_control()
        self.assertEqual(sched.state, STOPPED)
        self.assertTrue(sched._stop.is_set())

    def test_control_ignores_non_approver(self) -> None:
        self._control_id = self.remote.add_entry(CONTROL_TITLE, body="control").data.id
        sched = self._scheduler()
        sched.resume()  # baseline RUNNING (scheduler starts PAUSED until start())
        self.assertEqual(sched.state, RUNNING)

        self._control_comment("mallory", "stop")  # not an approver
        sched._process_control()
        self.assertEqual(sched.state, RUNNING)  # command ignored, state unchanged

    def test_status_command_posts_a_status_comment(self) -> None:
        self._control_id = self.remote.add_entry(CONTROL_TITLE, body="control").data.id
        sched = self._scheduler()
        self._control_comment("alice", "status")
        sched._process_control()
        details = self.remote.get_entry(self._control_id).data
        bot_comments = [c for c in details.comments if c.author == "bot"]
        self.assertEqual(len(bot_comments), 1)
        self.assertIn("STATUS", bot_comments[0].body)

    # -- threaded lifecycle + cancellation ------------------------------- #
    def test_threaded_start_stop_joins_cleanly(self) -> None:
        sched = self._scheduler()
        sched.start()
        self.assertTrue(sched.is_running())
        sched.stop(timeout=5)
        self.assertFalse(sched.is_running())
        self.assertEqual(sched.state, STOPPED)

    def test_hard_stop_cancels_in_flight_task(self) -> None:
        # A runner that blocks until its cancel Event trips, then reports killed.
        def _side_effect(call):
            event = call["cancel"]
            event.wait(timeout=10)
            if event is not None and event.is_set():
                return AgentResult(ok=False, killed=True, error="agent run cancelled")
            return AgentResult(ok=True)

        runner = FakeAgentRunner(side_effect=_side_effect)
        self.remote.create_label("spec-change:tweak")
        self.remote.add_entry("Tweak it", labels=["spec-change:tweak"])

        sched = self._scheduler(runner=runner)
        sched._sync()  # mirror + enqueue the control event
        # The long agent run now lives on the WORK lane. Drain the control event so
        # it SCHEDULES the work_run, then claim that work job - it is the in-flight
        # task a hard stop must cancel + requeue.
        ctrl = self.db.claim_next(LANE_CONTROL)
        self.assertIsNotNone(ctrl)
        sched._process_control_task(ctrl)
        task = self.db.claim_next(LANE_WORK)
        self.assertIsNotNone(task, "the control event should have scheduled a work_run")

        done = threading.Event()

        def _go():
            sched._process_task(task)
            done.set()

        worker = threading.Thread(target=_go)
        worker.start()
        time.sleep(0.3)
        sched.request_stop()  # cancels the in-flight work task
        finished = done.wait(timeout=10)

        self.assertTrue(finished, "stop did not unblock the in-flight task")
        worker.join(timeout=5)
        # The interrupted task was requeued, not marked failed.
        self.assertEqual(self.db.get_task(task["task_id"])["status"], "pending")
        self.assertGreaterEqual(len(runner.calls), 1)

    # -- dependency-gate requeue delay ----------------------------------- #
    def test_requeue_with_delay_sets_future_not_before(self) -> None:
        sched = self._scheduler()
        self.db.enqueue("handle_label_added", post_id="1", payload={})
        task = self.db.claim_next()
        sched._requeue_with_delay(task["task_id"], 120)
        row = self.db.get_task(task["task_id"])
        self.assertEqual(row["status"], "pending")
        self.assertIsNotNone(row["not_before"])  # delayed re-check, not immediate

    def test_requeue_with_no_delay_is_immediate(self) -> None:
        sched = self._scheduler()
        self.db.enqueue("handle_label_added", post_id="1", payload={})
        task = self.db.claim_next()
        sched._requeue_with_delay(task["task_id"], None)
        row = self.db.get_task(task["task_id"])
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["not_before"])

    # -- out-of-band control file (CLI / web service) -------------------- #
    def test_control_file_drives_state_and_heartbeat(self) -> None:
        from specseed_runtime.executing import runner_control

        storage = self.root / "storage"
        storage.mkdir(parents=True, exist_ok=True)
        sched = Scheduler(
            db=self.db,
            runner=self.runner,
            config=_config(),
            storage=storage,
            repo_root=self.root,
            remote=self.remote,
            local=self.local,
            poll_interval=0.0,
            tick=0.05,
            control_file=runner_control.control_file(storage),
            status_file=runner_control.status_file(storage),
            heartbeat_interval=0.0,
        )
        sched.resume()
        self.assertEqual(sched.state, RUNNING)

        runner_control.write_command(storage, runner_control.PAUSED)
        sched._reconcile_control_file()
        self.assertEqual(sched.state, PAUSED)

        runner_control.write_command(storage, runner_control.STOPPED)
        sched._reconcile_control_file()
        self.assertEqual(sched.state, STOPPED)

        sched._heartbeat(force=True)
        status = runner_control.read_runner_status(storage)
        self.assertEqual(status.get("pid"), os.getpid())
        self.assertTrue(status.get("alive"))


class HeartbeatDuringTaskTest(unittest.TestCase):
    """The heartbeat keeps stamping while a long task blocks the loop thread -
    a busy runner must never read as dead (the operator would 'restart' it and
    the new process would kill the live agent)."""

    def test_worker_wait_loop_heartbeats(self) -> None:
        cancellation.reset()
        self.addCleanup(cancellation.reset)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        def slow(call):
            time.sleep(0.3)
            return AgentResult(ok=True, returncode=0)

        db = Database(db_path=root / "queue.db")
        remote = TrackingRemoteLocal(db_path=root / "remote.db", author="bot")
        local = TrackingLocal(db_path=root / "local.db", author="agent")
        remote.create_label("spec-change:adapt")
        remote.add_entry("Adapt", labels=["spec-change:adapt"])
        sched = Scheduler(
            db=db,
            runner=FakeAgentRunner(side_effect=slow),
            config=_config(),
            storage=root / "storage",
            repo_root=root,
            remote=remote,
            local=local,
            poll_interval=0.0,
            tick=0.05,
            status_file=root / "storage" / "runner.json",
            heartbeat_interval=0.05,
        )
        calls = {"n": 0}
        original = sched._heartbeat

        def counting(**kw):
            calls["n"] += 1
            return original(**kw)

        sched._heartbeat = counting
        sched.run_once()
        # the wait loop ticked the heartbeat repeatedly during the 0.3s run
        self.assertGreaterEqual(calls["n"], 3)
        self.assertTrue((root / "storage" / "runtime" / "runner.json").exists())


class FailureRecoveryEndToEndTest(SchedulerTest):
    """A failing agent task ends up: retried-with-backoff + platform_error post."""

    def test_agent_failure_creates_error_post_and_schedules_retry(self) -> None:
        self.remote.create_label("spec-change:adapt")
        post_id = self.remote.add_entry("Adapt it", labels=["spec-change:adapt"]).data.id
        failing = FakeAgentRunner(
            result=AgentResult(ok=False, returncode=1, error="agent exited with code 1")
        )
        sched = self._scheduler(runner=failing)
        sched.run_once()

        # the origin task is parked pending with a future not_before
        origin = next(
            t
            for t in (self.db.get_task(i) for i in range(1, 50))
            if t and str(t.get("post_id")) == str(post_id) and t["status"] == "pending"
        )
        self.assertIsNotNone(origin["not_before"])
        # a platform_error post exists on the remote; its marker links the task
        # (entry-created and label-added both failed -> one post per task)
        posts = [
            e
            for e in self.remote.list_entries(is_open=None).data
            if str(e.title).startswith("Platform error:")
        ]
        self.assertGreaterEqual(len(posts), 1)
        marker = "specseed:platform-error task={0}".format(origin["task_id"])
        bodies = [self.remote.get_entry(p.id).data.body for p in posts]
        self.assertTrue(any(marker in body for body in bodies))


class ConfigReloadOnResumeTest(unittest.TestCase):
    """resume() re-reads config via config_loader and rebuilds derived state."""

    def setUp(self) -> None:
        cancellation.reset()
        self.addCleanup(cancellation.reset)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="bot")
        self.local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        self.db = Database(db_path=self.root / "queue.db")
        self.runner = FakeAgentRunner()

    def _scheduler(self, *, loader=None, poll_interval=None) -> Scheduler:
        return Scheduler(
            db=self.db,
            runner=self.runner,
            config=_config(),
            storage=self.root / "storage",
            repo_root=self.root,
            remote=self.remote,
            local=self.local,
            remote_factory=lambda: self.remote,
            local_factory=lambda: self.local,
            poll_interval=poll_interval,
            tick=0.05,
            config_loader=loader,
        )

    def test_resume_without_loader_keeps_everything(self) -> None:
        sched = self._scheduler()
        config_before = sched.config
        sched.resume()
        self.assertEqual(sched.state, RUNNING)
        self.assertIs(sched.runner, self.runner)
        self.assertIs(sched.config, config_before)

    def test_resume_reloads_config_and_rebuilds_runner_chains(self) -> None:
        new_cfg = dict(_config())
        new_cfg["runner"] = {"implementation": [{"provider": "codex"}]}
        new_cfg["poll_interval_seconds"] = 7
        sched = self._scheduler(loader=lambda: new_cfg)
        sched.resume()
        self.assertEqual(sched.state, RUNNING)
        self.assertIs(sched.config, new_cfg)
        self.assertEqual(sched.poll_interval, 7.0)
        impl = sched.runner.chain_for("implementation")[0]
        self.assertIsInstance(impl, CodexAgentRunner)
        # missing functions ride the configured implementation chain
        self.assertIsInstance(sched.runner.chain_for("spec")[0], CodexAgentRunner)

    def test_explicit_poll_interval_survives_reload(self) -> None:
        new_cfg = dict(_config())
        new_cfg["poll_interval_seconds"] = 7
        sched = self._scheduler(loader=lambda: new_cfg, poll_interval=0.0)
        sched.resume()
        self.assertEqual(sched.poll_interval, 0.0)

    def test_reload_updates_control_channel_in_place_keeping_cursor(self) -> None:
        sched = self._scheduler(loader=lambda: dict(_config(), marker=True))
        channel = sched._ensure_control()
        self.assertIsNotNone(channel)
        channel._cursor = (0, 5)
        sched.resume()
        self.assertIs(sched._control, channel)  # same object: cursor never resets
        self.assertEqual(channel._cursor, (0, 5))
        self.assertTrue(channel.config.get("marker"))
        self.assertIs(channel.permissions, sched.permissions)
        self.assertIs(channel.tracker, self.remote)

    def test_bad_loader_keeps_old_config(self) -> None:
        def boom() -> dict:
            raise OSError("config unreadable")

        sched = self._scheduler(loader=boom)
        config_before = sched.config
        sched.resume()
        self.assertEqual(sched.state, RUNNING)
        self.assertIs(sched.config, config_before)
        self.assertIs(sched.runner, self.runner)

    def test_stopped_scheduler_does_not_reload(self) -> None:
        calls = []

        def loader() -> dict:
            calls.append(1)
            return _config()

        sched = self._scheduler(loader=loader)
        sched.request_stop()
        sched.resume()
        self.assertEqual(sched.state, STOPPED)
        self.assertEqual(calls, [])


class QuotaCircuitTest(SchedulerTest):
    """A quota-exhausted outcome parks the loop and requeues, no recovery."""

    def test_open_circuit_requeues_for_later_and_parks(self) -> None:
        from specseed_runtime.executing.dispatch import HandlerOutcome

        sched = self._scheduler()
        self.db.enqueue("handle_entry_created", post_id="1", payload={})
        task = self.db.claim_next()
        out = HandlerOutcome(success=False, requeue=True, quota=True, quota_until=None)
        sched._open_quota_circuit(int(task["task_id"]), out)

        self.assertTrue(sched._quota_circuit_open())
        # parked in the future -> not claimable right now
        self.assertIsNone(self.db.claim_next())

    def test_circuit_closes_after_deadline(self) -> None:
        sched = self._scheduler()
        sched._quota_paused_until = time.monotonic() - 1  # already elapsed
        self.assertFalse(sched._quota_circuit_open())
        self.assertEqual(sched._quota_paused_until, 0.0)

    def test_quota_park_parses_iso_reset(self) -> None:
        from datetime import datetime, timedelta, timezone

        sched = self._scheduler()
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        secs, not_before = sched._quota_park(future)
        self.assertGreater(secs, 60)
        self.assertTrue(not_before)

    def test_quota_park_default_when_no_hint(self) -> None:
        from specseed_runtime.executing.quota import DEFAULT_PARK_S

        sched = self._scheduler()
        secs, not_before = sched._quota_park(None)
        self.assertEqual(secs, float(DEFAULT_PARK_S))
        self.assertTrue(not_before)


class TwoLaneConcurrencyTest(SchedulerTest):
    """The point of the split: the control lane keeps moving while the work lane is
    busy or parked, so human-facing events are never starved behind a long run."""

    def _seed_blocking_issue(self, release, started):
        def _impl(call):
            started.set()
            release.wait(timeout=10)
            return AgentResult(ok=True, report={"status": "done", "summary": "x"})

        self.remote.create_label("tier:issue")
        self.remote.create_label("status:todo")
        self.remote.add_entry("Do it", labels=["tier:issue", "status:todo"])
        return FakeAgentRunner(side_effect=_impl)

    def _wait(self, predicate, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.03)
        return False

    def _work_task(self):
        """The scheduled work_run row (id is not 1 - that is the control event)."""
        for i in range(1, 50):
            row = self.db.get_task(i)
            if row and row["action"] == "work_run":
                return row
        return None

    def test_control_drains_while_work_blocks(self) -> None:
        release, started = threading.Event(), threading.Event()
        runner = self._seed_blocking_issue(release, started)
        sched = self._scheduler(runner=runner)
        sched.start()
        self.addCleanup(lambda: (release.set(), sched.stop(timeout=5)))
        try:
            self.assertTrue(started.wait(timeout=8), "work agent never started")
            # A fast control item enqueued WHILE the work agent blocks must still drain.
            cid = self.db.enqueue("cleanup", post_id="x", payload={"reason": "t"})
            self.assertTrue(
                self._wait(lambda: self.db.get_task(cid)["status"] == "success"),
                "control item was starved behind the blocked work job",
            )
            # ...and the work job is genuinely still in flight at that moment.
            self.assertEqual(self._work_task()["status"], "in_progress")
        finally:
            release.set()

    def test_quota_parks_work_lane_but_not_control(self) -> None:
        # The work agent reports quota -> the scheduler parks the WORK lane. The
        # control lane must keep draining (approvals/cleanup are not quota-bound).
        def _quota(call):
            return AgentResult(ok=False, quota_exhausted=True, error="usage limit")

        self.remote.create_label("tier:issue")
        self.remote.create_label("status:todo")
        self.remote.add_entry("Do it", labels=["tier:issue", "status:todo"])
        sched = self._scheduler(runner=FakeAgentRunner(side_effect=_quota))
        sched.start()
        self.addCleanup(lambda: sched.stop(timeout=5))
        self.assertTrue(
            self._wait(lambda: sched._quota_paused_until > 0), "quota circuit never opened"
        )
        cid = self.db.enqueue("cleanup", post_id="y", payload={"reason": "t"})
        self.assertTrue(
            self._wait(lambda: self.db.get_task(cid)["status"] == "success"),
            "control item was starved while the work lane was quota-parked",
        )

    def test_stop_mid_work_unblocks_and_requeues(self) -> None:
        release, started = threading.Event(), threading.Event()

        def _impl(call):
            started.set()
            event = call["cancel"]
            event.wait(timeout=10)
            return AgentResult(ok=False, killed=True, error="cancelled")

        self.remote.create_label("tier:issue")
        self.remote.create_label("status:todo")
        self.remote.add_entry("Do it", labels=["tier:issue", "status:todo"])
        sched = self._scheduler(runner=FakeAgentRunner(side_effect=_impl))
        sched.start()
        self.assertTrue(started.wait(timeout=8), "work agent never started")
        sched.request_stop()  # honored mid-work (the control gap that used to freeze)
        sched.stop(timeout=8)
        self.assertFalse(sched.is_running())
        # the interrupted work job was requeued, not lost.
        self.assertEqual(self._work_task()["status"], "pending")


if __name__ == "__main__":
    unittest.main()
