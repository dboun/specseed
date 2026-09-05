"""test_runner_control.py - control file + heartbeat + detached spawn."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specseed_runtime.executing import runner_control


class ControlFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)

    def test_write_and_read_desired(self) -> None:
        self.assertIsNone(runner_control.read_desired(self.storage))
        runner_control.write_command(self.storage, runner_control.PAUSED)
        self.assertEqual(runner_control.read_desired(self.storage), runner_control.PAUSED)
        runner_control.resume_runner(self.storage)
        self.assertEqual(runner_control.read_desired(self.storage), runner_control.RUNNING)

    def test_write_command_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            runner_control.write_command(self.storage, "frobnicate")

    def test_seq_increments(self) -> None:
        runner_control.write_command(self.storage, runner_control.RUNNING)
        runner_control.write_command(self.storage, runner_control.PAUSED)
        import json

        doc = json.loads(runner_control.control_file(self.storage).read_text())
        self.assertEqual(doc["seq"], 2)


class HeartbeatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)

    def test_missing_status_is_stopped(self) -> None:
        status = runner_control.read_runner_status(self.storage)
        self.assertEqual(status["state"], "stopped")
        self.assertFalse(status["alive"])

    def test_fresh_status_with_live_pid_is_alive(self) -> None:
        runner_control.write_runner_status(self.storage, {"state": "running", "pending": 2})
        status = runner_control.read_runner_status(self.storage)
        self.assertTrue(status["alive"])
        self.assertEqual(status["pid"], os.getpid())
        self.assertEqual(status["pending"], 2)

    def test_dead_pid_reads_as_stopped(self) -> None:
        runner_control.write_runner_status(self.storage, {"state": "running"})
        # Rewrite with an impossible pid so liveness fails.
        import json

        path = runner_control.status_file(self.storage)
        doc = json.loads(path.read_text())
        doc["pid"] = 2_147_480_000
        path.write_text(json.dumps(doc))
        status = runner_control.read_runner_status(self.storage)
        self.assertFalse(status["alive"])
        self.assertEqual(status["state"], "stopped")

    def test_is_pid_alive(self) -> None:
        self.assertTrue(runner_control.is_pid_alive(os.getpid()))
        self.assertFalse(runner_control.is_pid_alive(None))
        self.assertFalse(runner_control.is_pid_alive(2_147_480_000))

    def test_self_reported_stopped_heartbeat_is_not_alive(self) -> None:
        # Fresh stamp, our own (definitely live) pid - but the runner said it is
        # stopping. Its own word wins, or a restart right after `stop` no-ops.
        runner_control.write_runner_status(self.storage, {"state": "stopped"})
        status = runner_control.read_runner_status(self.storage)
        self.assertEqual(status["pid"], os.getpid())
        self.assertFalse(status["alive"])
        self.assertEqual(status["state"], "stopped")


class PidStateTest(unittest.TestCase):
    def test_own_process_is_running_or_sleeping(self) -> None:
        state = runner_control._pid_state(os.getpid())
        self.assertIn(state, ("R", "S", None))

    def test_zombie_pid_is_not_alive(self) -> None:
        # A reaped-but-unwaited child still answers kill(0); it is not a runner.
        import subprocess
        import sys
        import time

        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        self.addCleanup(proc.wait, 10)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and runner_control._pid_state(proc.pid) != "Z":
            time.sleep(0.02)
        if runner_control._pid_state(proc.pid) != "Z":
            self.skipTest("platform does not expose a zombie state for the child")
        self.assertFalse(runner_control.is_pid_alive(proc.pid))
        self.assertFalse(runner_control.pid_is_runner(proc.pid))


class StartRunnerTest(unittest.TestCase):
    def test_start_runner_spawns_and_flags_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            record = {
                "name": "r",
                "target": tmp,
                "specseed_dir": ".specseed",
                "storage": str(storage),
            }

            class FakeProc:
                pid = 9999

            with mock.patch.object(runner_control.subprocess, "Popen", return_value=FakeProc()) as popen:
                status = runner_control.start_runner(record)
            popen.assert_called_once()
            self.assertEqual(status["pid"], 9999)
            self.assertEqual(runner_control.read_desired(storage), runner_control.RUNNING)

    def test_start_runner_noop_when_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            storage.mkdir(parents=True)
            runner_control.write_runner_status(storage, {"state": "running"})
            record = {"name": "r", "target": tmp, "specseed_dir": ".specseed", "storage": str(storage)}
            with mock.patch.object(runner_control.subprocess, "Popen") as popen:
                status = runner_control.start_runner(record)
            popen.assert_not_called()
            self.assertTrue(status["alive"])

    def _stale_status(self, storage: Path, pid: int = 4242) -> None:
        import json

        storage.mkdir(parents=True, exist_ok=True)
        (storage / "runner.json").write_text(
            json.dumps(
                {"pid": pid, "state": "running", "updated_at": "2000-01-01T00:00:00Z"}
            ),
            encoding="utf-8",
        )

    def test_start_runner_refuses_stale_heartbeat_with_live_runner_pid(self) -> None:
        # Busy runner: heartbeat starved but the pid is a live specseed process.
        # A second runner over the same storage would kill its live agent.
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            self._stale_status(storage)
            record = {"name": "r", "target": tmp, "specseed_dir": ".specseed", "storage": str(storage)}
            with mock.patch.object(runner_control, "pid_is_runner", return_value=True), \
                 mock.patch.object(runner_control.subprocess, "Popen") as popen:
                status = runner_control.start_runner(record)
            popen.assert_not_called()
            self.assertEqual(status["state"], "busy")
            self.assertTrue(status["alive"])

    def test_start_runner_proceeds_when_stale_pid_is_not_a_runner(self) -> None:
        # Recycled pid (alive but not specseed) must not block a legit start.
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            self._stale_status(storage)
            record = {"name": "r", "target": tmp, "specseed_dir": ".specseed", "storage": str(storage)}

            class FakeProc:
                pid = 777

            with mock.patch.object(runner_control, "pid_is_runner", return_value=False), \
                 mock.patch.object(runner_control.subprocess, "Popen", return_value=FakeProc()) as popen:
                status = runner_control.start_runner(record)
            popen.assert_called_once()
            self.assertEqual(status["pid"], 777)


    def test_start_right_after_stop_spawns_instead_of_no_op(self) -> None:
        # TKT-15: `stop` then `start` inside STALE_AFTER_S. The heartbeat is
        # seconds old and its pid still answers kill(0) (zombie), so the old
        # liveness test reported success and spawned nothing.
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            storage.mkdir(parents=True)
            runner_control.write_runner_status(storage, {"state": "stopped"})
            record = {"name": "r", "target": tmp, "specseed_dir": ".specseed", "storage": str(storage)}

            class FakeProc:
                pid = 4321

            # pid_is_runner False = the old process is really gone (zombie/exited).
            with mock.patch.object(runner_control, "pid_is_runner", return_value=False), \
                 mock.patch.object(runner_control.subprocess, "Popen", return_value=FakeProc()) as popen:
                status = runner_control.start_runner(record)
            popen.assert_called_once()
            self.assertEqual(status["pid"], 4321)
            self.assertEqual(runner_control.read_desired(storage), runner_control.RUNNING)

    def test_start_right_after_stop_reports_busy_while_draining(self) -> None:
        # Same window, but the runner is still alive finishing its last job:
        # never spawn a second one over the same storage - say busy instead.
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            storage.mkdir(parents=True)
            runner_control.write_runner_status(storage, {"state": "stopped"})
            record = {"name": "r", "target": tmp, "specseed_dir": ".specseed", "storage": str(storage)}
            with mock.patch.object(runner_control, "pid_is_runner", return_value=True), \
                 mock.patch.object(runner_control.subprocess, "Popen") as popen:
                status = runner_control.start_runner(record)
            popen.assert_not_called()
            self.assertEqual(status["state"], "busy")


class PidIsRunnerTest(unittest.TestCase):
    def test_dead_pid_is_not_a_runner(self) -> None:
        import subprocess
        import sys

        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=10)
        self.assertFalse(runner_control.pid_is_runner(proc.pid))

    def test_none_pid_is_not_a_runner(self) -> None:
        self.assertFalse(runner_control.pid_is_runner(None))


if __name__ == "__main__":
    unittest.main()
