"""migrating/ - storage version marker + hop chain behavior.

Pattern for new hop coverage: build an old-shape fixture under tmp, call
``run_migrations(storage=..., specseed_dir=...)``, assert the upgraded files +
marker, then run again and assert nothing changes (idempotency).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.target_facing.specseed_target_src.migrating import m_0_3_0__0_3_1
from src.target_facing.specseed_target_src.migrating import migrate
from src.target_facing.specseed_target_src import storage_paths


def _fixture_tree(root: Path, version: str = "0.3.1") -> tuple[Path, Path]:
    """A minimal specseed dir: skill version file + empty storage."""
    specseed_dir = root / ".specseed"
    (specseed_dir / "skills" / "specseed").mkdir(parents=True)
    (specseed_dir / "skills" / "specseed" / "version.txt").write_text(version + "\n", encoding="utf-8")
    storage = specseed_dir / "storage"
    return specseed_dir, storage


class VersionPlumbingTest(unittest.TestCase):
    def test_parse_version(self) -> None:
        self.assertEqual(migrate.parse_version("0.3.1"), (0, 3, 1))
        self.assertLess(migrate.parse_version("0.3.2"), migrate.parse_version("0.10.0"))

    def test_missing_marker_is_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(migrate.storage_version(Path(tmp) / "storage"), migrate.BASELINE_VERSION)

    def test_marker_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            migrate.write_storage_version("0.3.1", storage)
            self.assertEqual(migrate.storage_version(storage), "0.3.1")

    def test_code_version_falls_back_to_running_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # tmp has no skill version file -> the dev repo's own wins
            self.assertEqual(
                migrate.code_version(Path(tmp)),
                storage_paths.skill_version_file().read_text(encoding="utf-8").strip(),
            )

    def test_default_db_paths_point_into_storage(self) -> None:
        for name in ("specseed.db", "tracking_local.db", "tracking_remote_local.db"):
            path = storage_paths.storage_db_path(name)
            self.assertEqual(path.parent.name, "storage")
            self.assertEqual(path.name, name)


class RunMigrationsTest(unittest.TestCase):
    def test_fresh_storage_applies_chain_from_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.3.1")

            applied = migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)

            self.assertEqual(applied, ["m_0_3_0__0_3_1"])
            self.assertEqual(migrate.storage_version(storage), "0.3.1")

    def test_current_storage_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.3.1")
            migrate.write_storage_version("0.3.1", storage)

            self.assertEqual(migrate.run_migrations(storage=storage, specseed_dir=specseed_dir), [])
            self.assertEqual(migrate.storage_version(storage), "0.3.1")

    def test_newer_storage_never_rewritten_backwards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.3.1")
            migrate.write_storage_version("0.9.9", storage)

            self.assertEqual(migrate.run_migrations(storage=storage, specseed_dir=specseed_dir), [])
            self.assertEqual(migrate.storage_version(storage), "0.9.9")

    def test_z_bump_without_hop_fast_forwards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.3.2")
            migrate.write_storage_version("0.3.1", storage)

            applied = migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)

            self.assertEqual(applied, [])
            self.assertEqual(migrate.storage_version(storage), "0.3.2")

    def test_specseed_dir_derived_from_storage_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp))
            stray = specseed_dir / "specseed_target_src" / "db" / "specseed.db"
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"queue")

            # no explicit specseed_dir: <...>/storage -> parent is the tree
            migrate.run_migrations(storage=storage)

            self.assertEqual((storage / "specseed.db").read_bytes(), b"queue")
            self.assertFalse(stray.exists())


class Hop_0_3_0__0_3_1_Test(unittest.TestCase):
    def _old_shape(self, root: Path) -> tuple[Path, Path]:
        specseed_dir, storage = _fixture_tree(root)
        for rel, name in (
            ("specseed_target_src/db", "specseed.db"),
            ("specseed_target_src/tracking", "tracking_local.db"),
            ("specseed_target_src/tracking", "tracking_remote_local.db"),
        ):
            path = specseed_dir / rel / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        wal = specseed_dir / "specseed_target_src" / "db" / "specseed.db-wal"
        wal.write_bytes(b"wal")
        storage.mkdir(parents=True)
        (storage / "configuration.json").write_text(
            json.dumps({"version": 1, "dev_branch": "custom"}) + "\n", encoding="utf-8"
        )
        return specseed_dir, storage

    def test_moves_dbs_and_drops_config_version_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))

            applied = migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)

            self.assertEqual(applied, ["m_0_3_0__0_3_1"])
            for name in ("specseed.db", "tracking_local.db", "tracking_remote_local.db"):
                self.assertEqual((storage / name).read_bytes(), name.encode())
            self.assertEqual((storage / "specseed.db-wal").read_bytes(), b"wal")
            self.assertEqual(list((specseed_dir / "specseed_target_src").rglob("*.db*")), [])
            cfg = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
            self.assertNotIn("version", cfg)
            self.assertEqual(cfg["dev_branch"], "custom")
            self.assertEqual(migrate.storage_version(storage), "0.3.1")

    def test_idempotent_second_run_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))
            migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)
            before = {p.name: p.read_bytes() for p in storage.iterdir() if p.is_file()}

            self.assertEqual(migrate.run_migrations(storage=storage, specseed_dir=specseed_dir), [])

            after = {p.name: p.read_bytes() for p in storage.iterdir() if p.is_file()}
            self.assertEqual(before, after)

    def test_existing_storage_db_never_clobbered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))
            (storage / "specseed.db").write_bytes(b"newer-data")

            m_0_3_0__0_3_1.run(storage, specseed_dir)

            self.assertEqual((storage / "specseed.db").read_bytes(), b"newer-data")
            # the stray stays put for the human to inspect
            self.assertTrue((specseed_dir / "specseed_target_src" / "db" / "specseed.db").exists())

    def test_unmigratable_old_storage_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp))
            migrate.write_storage_version("0.2.0", storage)

            with self.assertRaises(RuntimeError):
                migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)


if __name__ == "__main__":
    unittest.main()
