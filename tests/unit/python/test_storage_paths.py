"""test_storage_paths.py - default storage resolution + the SPECSEED_STORAGE seam."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import tempfile

from specseed_runtime.storage_paths import (
    SPECSEED_STORAGE_ENV,
    agent_output_dir,
    agent_output_file,
    config_file,
    control_file,
    default_specseed_dir,
    default_storage_dir,
    instructions_dir,
    platform_log_file,
    prune_agent_output,
    runner_file,
    sessions_file,
    spec_change_root,
    spec_dir,
    storage_db_path,
    token_file,
    tracker_dir,
    version_marker_file,
)


class DefaultStorageDirTest(unittest.TestCase):
    def test_without_env_is_engine_repo_storage(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop(SPECSEED_STORAGE_ENV, None)
            self.assertEqual(default_storage_dir(), default_specseed_dir() / "storage")

    def test_env_overrides_to_target_storage(self) -> None:
        with mock.patch.dict(
            "os.environ", {SPECSEED_STORAGE_ENV: "/tmp/target/.specseed/storage"}
        ):
            self.assertEqual(
                default_storage_dir(), Path("/tmp/target/.specseed/storage")
            )

    def test_env_value_is_user_expanded(self) -> None:
        with mock.patch.dict("os.environ", {SPECSEED_STORAGE_ENV: "~/somewhere/storage"}):
            self.assertEqual(
                default_storage_dir(), Path("~/somewhere/storage").expanduser()
            )

    def test_blank_env_falls_back(self) -> None:
        with mock.patch.dict("os.environ", {SPECSEED_STORAGE_ENV: ""}):
            self.assertEqual(default_storage_dir(), default_specseed_dir() / "storage")

    def test_storage_db_path_honors_env_default(self) -> None:
        with mock.patch.dict("os.environ", {SPECSEED_STORAGE_ENV: "/tmp/data/root"}):
            self.assertEqual(
                storage_db_path("specseed.db"), Path("/tmp/data/root/db/specseed.db")
            )


class SubdirLayoutTest(unittest.TestCase):
    """Every accessor routes its file to the right single-purpose subdir."""

    root = Path("/tmp/data/root")

    def test_db_files_split_queue_vs_tracker(self) -> None:
        self.assertEqual(storage_db_path("specseed.db", self.root), self.root / "db" / "specseed.db")
        self.assertEqual(
            storage_db_path("tracking_local.db", self.root), self.root / "tracker" / "tracking_local.db"
        )
        self.assertEqual(tracker_dir(self.root), self.root / "tracker")

    def test_config_files_under_config(self) -> None:
        self.assertEqual(config_file(self.root), self.root / "config" / "configuration.json")
        self.assertEqual(token_file(self.root), self.root / "config" / "token_remote.txt")
        self.assertEqual(version_marker_file(self.root), self.root / "config" / "version.txt")

    def test_runtime_files_under_runtime(self) -> None:
        self.assertEqual(control_file(self.root), self.root / "runtime" / "control.json")
        self.assertEqual(runner_file(self.root), self.root / "runtime" / "runner.json")
        self.assertEqual(sessions_file(self.root), self.root / "runtime" / "sessions.json")

    def test_logs_spec_instructions(self) -> None:
        self.assertEqual(platform_log_file(self.root), self.root / "logs" / "platform.log")
        self.assertEqual(spec_dir(self.root), self.root / "spec")
        self.assertEqual(spec_change_root(self.root), self.root / "spec-change")
        self.assertEqual(instructions_dir(self.root), self.root / "instructions")


class AgentOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Path(self._tmp.name)

    def test_paths_under_logs_agent_output(self) -> None:
        self.assertEqual(agent_output_dir(self.storage), self.storage / "logs" / "agent-output")
        self.assertEqual(
            agent_output_file(42, self.storage), self.storage / "logs" / "agent-output" / "42.log"
        )

    def test_prune_keeps_newest_n(self) -> None:
        out = agent_output_dir(self.storage)
        out.mkdir(parents=True)
        # mtimes ascending with task id so "newest" is deterministic
        for i in range(5):
            p = out / f"{i}.log"
            p.write_text("x", encoding="utf-8")
            import os
            os.utime(p, (1000 + i, 1000 + i))
        prune_agent_output(self.storage, keep=2)
        survivors = sorted(p.name for p in out.glob("*.log"))
        self.assertEqual(survivors, ["3.log", "4.log"])

    def test_prune_missing_dir_is_noop(self) -> None:
        prune_agent_output(self.storage, keep=2)  # no agent-output dir yet
        self.assertFalse(agent_output_dir(self.storage).exists())


if __name__ == "__main__":
    unittest.main()
