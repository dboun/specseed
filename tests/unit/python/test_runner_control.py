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


if __name__ == "__main__":
    unittest.main()
