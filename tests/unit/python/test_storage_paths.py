"""test_storage_paths.py - default storage resolution + the SPECSEED_STORAGE seam."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from specseed_runtime.storage_paths import (
    SPECSEED_STORAGE_ENV,
    default_specseed_dir,
    default_storage_dir,
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


if __name__ == "__main__":
    unittest.main()
