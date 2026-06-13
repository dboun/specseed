"""test_relocate.py - one-time move of pre-0.21 in-target data into the home.

No network, no agent. Drives relocate_legacy_data + the full migrate chain against
a fabricated legacy ``<target>/.specseed/`` tree under an isolated SPECSEED_HOME.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specseed_runtime import registry
from specseed_runtime.migrating import relocate
from specseed_runtime.migrating.migrate import run_migrations, storage_version


class RelocateLegacyDataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self._env = mock.patch.dict(os.environ, {"SPECSEED_HOME": str(self.home)})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.target = Path(self.tmp.name) / "repo"
        self.target.mkdir()

    def _legacy(self) -> Path:
        legacy = self.target / ".specseed"
        storage = legacy / "storage"
        storage.mkdir(parents=True)
        (storage / "configuration.json").write_text('{"x":1}', encoding="utf-8")
        (storage / "specseed.db").write_text("db", encoding="utf-8")
        (storage / "version.txt").write_text("0.20.0", encoding="utf-8")
        (legacy / "spec").mkdir()
        (legacy / "spec" / "sad.md").write_text("SPEC", encoding="utf-8")
        (legacy / "AGENTS_INSTRUCTIONS_IMPL.md").write_text("myimpl", encoding="utf-8")
        (self.target / ".gitignore").write_text("node_modules/\n.specseed/\n", encoding="utf-8")
        return legacy

    def test_relocates_into_home_and_cleans_target(self) -> None:
        legacy = self._legacy()
        dr = relocate.relocate_legacy_data(self.target, ".specseed")

        self.assertEqual(dr, registry.data_root_for(self.target))
        self.assertTrue(str(dr).startswith(str(self.home.resolve())))
        self.assertFalse(legacy.exists())  # legacy dir removed
        # data laid down FLAT at the data root (the version hop reshapes it)
        self.assertTrue((dr / "configuration.json").exists())
        self.assertTrue((dr / "spec" / "sad.md").exists())
        # gitignore line stripped, other entries preserved
        gi = (self.target / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn(".specseed", gi)
        self.assertIn("node_modules", gi)
        # the version marker is readable (flat) so migrate picks the 0.21 hop
        self.assertEqual(storage_version(dr), "0.20.0")

    def test_relocate_then_migrate_reshapes(self) -> None:
        self._legacy()
        dr = relocate.relocate_legacy_data(self.target, ".specseed")
        applied = run_migrations(storage=dr)
        self.assertEqual(applied, ["m_0_20_0__0_21_0"])
        self.assertTrue((dr / "config" / "configuration.json").exists())
        self.assertTrue((dr / "db" / "specseed.db").exists())
        self.assertEqual(
            (dr / "instructions" / "impl" / "repo.md").read_text(encoding="utf-8"), "myimpl")

    def test_idempotent_no_legacy_is_noop(self) -> None:
        # No legacy dir: returns the data root, creates nothing to clean up.
        dr = relocate.relocate_legacy_data(self.target, ".specseed")
        self.assertEqual(dr, registry.data_root_for(self.target))
        # a second call after a real relocation also no-ops (legacy already gone)
        self._legacy()
        relocate.relocate_legacy_data(self.target, ".specseed")
        self.assertFalse((self.target / ".specseed").exists())
        relocate.relocate_legacy_data(self.target, ".specseed")  # no raise


if __name__ == "__main__":
    unittest.main()
