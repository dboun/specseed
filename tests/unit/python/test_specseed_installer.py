"""specseed.py installer behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import specseed


class SpecseedInstallerTest(unittest.TestCase):
    def test_installs_runtime_and_preserves_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "target"
            repo.mkdir()
            install_root = repo / ".specseed"
            storage_file = install_root / "storage" / "configuration.json"
            stale_file = install_root / "specseed_target_src" / "stale.txt"
            storage_file.parent.mkdir(parents=True)
            stale_file.parent.mkdir(parents=True)
            storage_file.write_text('{"keep": true}\n', encoding="utf-8")
            stale_file.write_text("old\n", encoding="utf-8")

            installed_at, copied = specseed.install(repo)

            self.assertEqual(installed_at, install_root.resolve())
            self.assertEqual({p.name for p in copied}, {"specseed_target_src", "skills"})
            self.assertTrue((install_root / "specseed_target_src" / "configuring" / "configure.py").is_file())
            self.assertTrue((install_root / "skills" / "specseed" / "SKILL.md").is_file())
            self.assertEqual(storage_file.read_text(encoding="utf-8"), '{"keep": true}\n')
            self.assertFalse(stale_file.exists())

    def test_custom_install_dir_configure_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "target"
            repo.mkdir()

            installed_at, _copied = specseed.install(repo, "seedmeta")
            command = specseed.configure_command(repo.resolve(), installed_at)

            self.assertIn("python3 seedmeta/specseed_target_src/configuring/configure.py", command)


if __name__ == "__main__":
    unittest.main()
