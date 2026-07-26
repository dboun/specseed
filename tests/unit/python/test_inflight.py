"""test_inflight.py - the child-process ledger + startup orphan reclaim.

Real children are plain ``sleep``/``python`` processes - never an agent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from specseed_runtime.db.database import Database
from specseed_runtime.executing import inflight


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)

    def _ledger(self) -> dict:
        return json.loads((self.storage / inflight.INFLIGHT_FILE).read_text(encoding="utf-8"))

    def test_record_then_clear(self) -> None:
        inflight.record(self.storage, 7, 12345, "claude")
        entry = self._ledger()["7"]
        self.assertEqual(entry["pid"], 12345)
        self.assertEqual(entry["binary"], "claude")
        inflight.clear(self.storage, 7)
        self.assertEqual(self._ledger(), {})

    def test_none_storage_is_noop(self) -> None:
        inflight.record(None, 1, 1, "x")
        inflight.clear(None, 1)  # nothing raised, nothing written

    def test_record_overwrites_same_task(self) -> None:
        inflight.record(self.storage, 7, 111, "claude")
        inflight.record(self.storage, 7, 222, "codex")
        self.assertEqual(self._ledger()["7"]["pid"], 222)


class ReclaimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)
        self.db = Database(db_path=self.storage / "queue.db")

    def test_kills_live_orphan_and_requeues_in_progress(self) -> None:
        # A real orphan: a sleeping python child recorded in the ledger.
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        task_id = self.db.enqueue("handle_entry_created", post_id="1", payload={})
        self.db.claim_next()  # -> in_progress
        # Empty binary skips the recycled-pid command check; sandboxed test
        # runners may forbid `ps`, but this still covers killing a recorded child.
        inflight.record(self.storage, task_id, proc.pid, "")

        summary = inflight.reclaim(self.storage, self.db)

        self.assertEqual(summary["killed"], 1)
        self.assertEqual(summary["requeued"], 1)
        proc.wait(timeout=10)  # reaped: the child is gone
        self.assertEqual(self.db.get_task(task_id)["status"], "pending")
        self.assertEqual(json.loads((self.storage / inflight.INFLIGHT_FILE).read_text()), {})

    def test_dead_pid_is_stale_not_killed(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=10)
        inflight.record(self.storage, 1, proc.pid, sys.executable)
        summary = inflight.reclaim(self.storage, self.db)
        self.assertEqual(summary["killed"], 0)
        self.assertEqual(summary["stale"], 1)

    def test_recycled_pid_with_other_binary_is_never_signalled(self) -> None:
        # A live process whose command does NOT contain the recorded binary.
        proc = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        inflight.record(self.storage, 1, proc.pid, "definitely-not-sleep-binary")

        summary = inflight.reclaim(self.storage, self.db)

        self.assertEqual(summary["killed"], 0)
        self.assertEqual(summary["stale"], 1)
        time.sleep(0.2)
        self.assertIsNone(proc.poll())  # still alive - untouched

    def test_requeues_orphan_tasks_even_without_ledger(self) -> None:
        task_id = self.db.enqueue("handle_entry_created", post_id="1", payload={})
        self.db.claim_next()
        summary = inflight.reclaim(self.storage, self.db)
        self.assertEqual(summary["requeued"], 1)
        self.assertEqual(self.db.get_task(task_id)["status"], "pending")

    def test_reclaim_twice_is_idempotent(self) -> None:
        self.db.enqueue("handle_entry_created", post_id="1", payload={})
        self.db.claim_next()
        inflight.reclaim(self.storage, self.db)
        second = inflight.reclaim(self.storage, self.db)
        self.assertEqual(second, {"killed": 0, "stale": 0, "requeued": 0})


class DispatchLedgerWiringTest(unittest.TestCase):
    """The agent on_start hook records, and completion clears."""

    def test_run_agent_records_and_clears(self) -> None:
        from specseed_runtime.executing.agent_runner import AgentResult, FakeAgentRunner
        from specseed_runtime.executing.dispatch import _run_agent

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        storage = Path(tmp.name)
        seen: dict = {}

        def side_effect(call):
            call["on_start"](4242, "claude")
            seen.update(json.loads((storage / inflight.INFLIGHT_FILE).read_text()))
            return AgentResult(ok=True, returncode=0)

        class Ctx:
            runner = FakeAgentRunner(side_effect=side_effect)
            repo_root = storage
            cancel = None
            agent_timeout_s = 5.0

        Ctx.storage = storage
        _run_agent(Ctx, "p", "implement", task_id=9)

        self.assertEqual(seen["9"]["pid"], 4242)  # recorded while running
        ledger = json.loads((storage / inflight.INFLIGHT_FILE).read_text())
        self.assertEqual(ledger, {})  # cleared after


if __name__ == "__main__":
    unittest.main()
