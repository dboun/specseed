"""test_spec_change_executor.py - the spec-change subprocess executor.

Writes a real temp apply.py that drops a marker file, runs it through dispatch,
and asserts the marker exists. Covers permission gating and cooperative cancel.
No GitHub/GitLab.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from src.target_facing.specseed_target_src.db.database import Database
from src.target_facing.specseed_target_src.executing.agent_runner import FakeAgentRunner
from src.target_facing.specseed_target_src.executing.context import ExecutionContext
from src.target_facing.specseed_target_src.executing.dispatch import dispatch
from src.target_facing.specseed_target_src.executing.permissions import Permissions
from src.target_facing.specseed_target_src.scheduling.spec_change import SPEC_CHANGE_ACTION
from src.target_facing.specseed_target_src.tracking.tracking_local import TrackingLocal
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


def _config(*, backend_enabled=False, post_issues=True):
    return {
        "backend": {"enabled": backend_enabled, "provider": None},
        "permissions": {"remote": {"post_issues": post_issues}},
    }


class SpecChangeExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="alice")
        self.local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        self.db = Database(db_path=self.root / "queue.db")

    def _ctx(self, config, *, cancel=None, timeout_s=30.0):
        return ExecutionContext(
            db=self.db,
            local=self.local,
            remote=self.remote,
            config=config,
            permissions=Permissions(config),
            runner=FakeAgentRunner(),
            repo_root=self.root,
            storage=self.root / "storage",
            cancel=cancel if cancel is not None else threading.Event(),
            agent_timeout_s=timeout_s,
        )

    def _write_script(self, body):
        script_dir = self.root / "spec-change" / "req1"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / "apply.py"
        script_path.write_text(body)
        return script_dir, script_path

    def _task(self, script_dir):
        return {
            "action": SPEC_CHANGE_ACTION,
            "post_id": "req1",
            "payload": {"dir": str(script_dir), "script": "apply.py", "request_id": "req1"},
        }

    def test_runs_script_and_creates_marker(self) -> None:
        marker = self.root / "marker.txt"
        script_dir, _ = self._write_script(
            "from pathlib import Path\n"
            "Path(r'{0}').write_text('ran')\n".format(marker)
        )
        ctx = self._ctx(_config())
        out = dispatch(ctx, self._task(script_dir))
        self.assertTrue(out.success, out.error)
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(), "ran")

    def test_pythonpath_lets_script_import_src(self) -> None:
        # The script imports from src.* to prove PYTHONPATH=repo_root works.
        marker = self.root / "import_marker.txt"
        script_dir, _ = self._write_script(
            "import importlib\n"
            "importlib.import_module('src.target_facing.specseed_target_src.scheduling.spec_change')\n"
            "from pathlib import Path\n"
            "Path(r'{0}').write_text('imported')\n".format(marker)
        )
        # repo_root must be the actual repo for the import to resolve.
        ctx = ExecutionContext(
            db=self.db,
            local=self.local,
            remote=self.remote,
            config=_config(),
            permissions=Permissions(_config()),
            runner=FakeAgentRunner(),
            repo_root=Path(__file__).resolve().parents[3],
            storage=self.root / "storage",
            cancel=threading.Event(),
            agent_timeout_s=30.0,
        )
        out = dispatch(ctx, self._task(script_dir))
        self.assertTrue(out.success, out.error)
        self.assertTrue(marker.exists())

    def test_permission_off_blocks_run(self) -> None:
        marker = self.root / "blocked_marker.txt"
        script_dir, _ = self._write_script(
            "from pathlib import Path\n"
            "Path(r'{0}').write_text('ran')\n".format(marker)
        )
        # Backend enabled but post_issues off -> not permitted.
        ctx = self._ctx(_config(backend_enabled=True, post_issues=False))
        out = dispatch(ctx, self._task(script_dir))
        self.assertFalse(out.success)
        self.assertIn("not permitted", out.error)
        self.assertFalse(marker.exists())

    def test_nonzero_exit_is_failure(self) -> None:
        script_dir, _ = self._write_script("import sys\nsys.exit(3)\n")
        ctx = self._ctx(_config())
        out = dispatch(ctx, self._task(script_dir))
        self.assertFalse(out.success)
        self.assertFalse(out.requeue)
        self.assertIn("code 3", out.error)

    def test_cancel_kills_sleeper(self) -> None:
        # A long sleeper should be torn down promptly when cancel is set.
        script_dir, _ = self._write_script("import time\ntime.sleep(60)\n")
        cancel = threading.Event()
        ctx = self._ctx(_config(), cancel=cancel, timeout_s=60.0)

        result_box = {}

        def _go():
            result_box["out"] = dispatch(ctx, self._task(script_dir))

        thread = threading.Thread(target=_go)
        start = time.monotonic()
        thread.start()
        # Give the subprocess time to spawn, then cancel.
        time.sleep(1.0)
        cancel.set()
        thread.join(timeout=30.0)
        elapsed = time.monotonic() - start

        self.assertFalse(thread.is_alive(), "dispatch did not return after cancel")
        self.assertLess(elapsed, 30.0)
        out = result_box["out"]
        self.assertFalse(out.success)
        self.assertTrue(out.requeue)
        self.assertIn("cancel", out.error)


if __name__ == "__main__":
    unittest.main()
