"""specseed.py installer behavior."""

from __future__ import annotations

import json
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

            installed_at, copied, summary = specseed.install(repo)

            self.assertEqual(installed_at, install_root.resolve())
            self.assertEqual({p.name for p in copied}, {"specseed_target_src", "skills"})
            self.assertTrue((install_root / "specseed_target_src" / "configuring" / "configure.py").is_file())
            self.assertTrue((install_root / "skills" / "specseed" / "SKILL.md").is_file())
            self.assertEqual(storage_file.read_text(encoding="utf-8"), '{"keep": true}\n')
            self.assertFalse(stale_file.exists())
            self.assertEqual(summary["rescued"], [])

    def test_custom_install_dir_configure_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "target"
            repo.mkdir()

            installed_at, _copied, _summary = specseed.install(repo, "seedmeta")
            command = specseed.configure_command(repo.resolve(), installed_at)

            self.assertIn("python3 seedmeta/specseed_target_src/configuring/configure.py", command)

    def test_install_writes_storage_version_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "target"
            repo.mkdir()

            installed_at, _copied, summary = specseed.install(repo)

            shipped = (installed_at / "skills" / "specseed" / "version.txt").read_text(encoding="utf-8").strip()
            marker = (installed_at / "storage" / "version.txt").read_text(encoding="utf-8").strip()
            self.assertEqual(marker, shipped)
            self.assertIn("storage", summary["migration"])

    def test_reinstall_rescues_stray_databases_into_storage(self) -> None:
        """Pre-0.3.1 dbs sat inside the runtime dirs the installer wipes."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "target"
            repo.mkdir()
            install_root = repo / ".specseed"
            old_db = install_root / "specseed_target_src" / "db" / "specseed.db"
            old_wal = Path(str(old_db) + "-wal")
            old_tracking = install_root / "specseed_target_src" / "tracking" / "tracking_local.db"
            old_db.parent.mkdir(parents=True)
            old_tracking.parent.mkdir(parents=True)
            old_db.write_bytes(b"queue-bytes")
            old_wal.write_bytes(b"wal-bytes")
            old_tracking.write_bytes(b"tracking-bytes")

            _installed_at, _copied, summary = specseed.install(repo)

            storage = install_root / "storage"
            self.assertEqual(
                {p.name for p in summary["rescued"]},
                {"specseed.db", "tracking_local.db"},
            )
            self.assertEqual((storage / "specseed.db").read_bytes(), b"queue-bytes")
            self.assertEqual((storage / "specseed.db-wal").read_bytes(), b"wal-bytes")
            self.assertEqual((storage / "tracking_local.db").read_bytes(), b"tracking-bytes")
            # the runtime tree got wiped+refreshed; no stray dbs survive in it
            self.assertEqual(list((install_root / "specseed_target_src").rglob("*.db")), [])

    def test_rescue_never_clobbers_existing_storage_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / ".specseed"
            stray = install_root / "specseed_target_src" / "db" / "specseed.db"
            kept = install_root / "storage" / "specseed.db"
            stray.parent.mkdir(parents=True)
            kept.parent.mkdir(parents=True)
            stray.write_bytes(b"old")
            kept.write_bytes(b"new")

            rescued = specseed.rescue_stray_databases(install_root)

            self.assertEqual(rescued, [])
            self.assertEqual(kept.read_bytes(), b"new")
            self.assertEqual(stray.read_bytes(), b"old")  # stays put, not deleted

    def test_reinstall_drops_legacy_config_version_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "target"
            repo.mkdir()
            config = repo / ".specseed" / "storage" / "configuration.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"version": 1, "dev_branch": "custom"}) + "\n", encoding="utf-8")

            specseed.install(repo)

            cfg = json.loads(config.read_text(encoding="utf-8"))
            self.assertNotIn("version", cfg)
            self.assertEqual(cfg["dev_branch"], "custom")


if __name__ == "__main__":
    unittest.main()
