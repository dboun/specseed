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
    default_specseed_dir,
    default_storage_dir,
    prune_agent_output,
    storage_db_path,
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
        with mock.patch.dict(
            "os.environ", {SPECSEED_STORAGE_ENV: "/tmp/target/.specseed/storage"}
        ):
            self.assertEqual(
                storage_db_path("specseed.db"),
                Path("/tmp/target/.specseed/storage/specseed.db"),
            )


class AgentOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Path(self._tmp.name)

    def test_paths_are_flat_under_agent_output(self) -> None:
        self.assertEqual(agent_output_dir(self.storage), self.storage / "agent-output")
        self.assertEqual(agent_output_file(42, self.storage), self.storage / "agent-output" / "42.log")

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
