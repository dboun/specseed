"""configure.py path-selection behavior."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.target_facing.specseed_target_src.configuring import configure


class ConfigureSpecseedDirTest(unittest.TestCase):
    def test_custom_specseed_dir_is_saved_and_gitignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage_hint = root / "bootstrap" / "storage"
            answers = iter([
                "seedmeta",  # specseed dir
                "",          # append to .gitignore
                "",          # local only
                "",          # local git on
                "",          # merge dev off
                "",          # merge main off
                "",          # no approvers
                "",          # default interval
                "",          # write config
            ])

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(configure, "_input", side_effect=lambda _prompt: next(answers)):
                    rc = configure.run_interactive(storage_hint, explicit_storage=False)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(rc, 0)
            config_path = root / "seedmeta" / "storage" / "configuration.json"
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(cfg["specseed_dir"], "seedmeta")
            self.assertIn("seedmeta/", (root / ".gitignore").read_text(encoding="utf-8"))

    def test_abort_does_not_gitignore_specseed_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            answers = iter([
                "seedmeta",  # specseed dir
                "",          # would append
                "",          # local only
                "",          # local git on
                "",          # merge dev off
                "",          # merge main off
                "",          # no approvers
                "",          # default interval
                "n",         # abort write
            ])

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(configure, "_input", side_effect=lambda _prompt: next(answers)):
                    rc = configure.run_interactive(root / "bootstrap" / "storage", explicit_storage=False)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(rc, 1)
            self.assertFalse((root / ".gitignore").exists())
            self.assertFalse((root / "seedmeta" / "storage" / "configuration.json").exists())


if __name__ == "__main__":
    unittest.main()
