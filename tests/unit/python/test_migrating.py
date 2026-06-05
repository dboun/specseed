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
from unittest import mock

from specseed_runtime.migrating import m_0_3_0__0_3_1
from specseed_runtime.migrating import m_0_3_1__0_4_0
from specseed_runtime.migrating import m_0_4_0__0_5_0
from specseed_runtime.migrating import migrate
from specseed_runtime import storage_paths


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

            # The whole chain up to the running engine, in order.
            self.assertEqual(
                applied, ["m_0_3_0__0_3_1", "m_0_3_1__0_4_0", "m_0_4_0__0_5_0"]
            )
            self.assertEqual(migrate.storage_version(storage), migrate.code_version())

    def test_current_storage_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.3.1")
            migrate.write_storage_version(migrate.code_version(), storage)

            self.assertEqual(migrate.run_migrations(storage=storage, specseed_dir=specseed_dir), [])
            self.assertEqual(migrate.storage_version(storage), migrate.code_version())

    def test_newer_storage_never_rewritten_backwards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.3.1")
            migrate.write_storage_version("0.9.9", storage)

            self.assertEqual(migrate.run_migrations(storage=storage, specseed_dir=specseed_dir), [])
            self.assertEqual(migrate.storage_version(storage), "0.9.9")

    def test_z_bump_without_hop_fast_forwards(self) -> None:
        # A z-bump beyond the last hop (no migration) just fast-forwards the marker.
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp))
            migrate.write_storage_version("0.4.0", storage)

            with mock.patch.object(migrate, "code_version", return_value="0.4.1"):
                applied = migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)

            self.assertEqual(applied, [])
            self.assertEqual(migrate.storage_version(storage), "0.4.1")

    def test_specseed_dir_derived_from_storage_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp))
            stray = specseed_dir / "specseed_runtime" / "db" / "specseed.db"
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
            ("specseed_runtime/db", "specseed.db"),
            ("specseed_runtime/tracking", "tracking_local.db"),
            ("specseed_runtime/tracking", "tracking_remote_local.db"),
        ):
            path = specseed_dir / rel / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        wal = specseed_dir / "specseed_runtime" / "db" / "specseed.db-wal"
        wal.write_bytes(b"wal")
        storage.mkdir(parents=True)
        (storage / "configuration.json").write_text(
            json.dumps({"version": 1, "dev_branch": "custom"}) + "\n", encoding="utf-8"
        )
        return specseed_dir, storage

    def test_moves_dbs_and_drops_config_version_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))

            # Pin the engine to 0.3.1 so only this hop runs (the chain stops there).
            with mock.patch.object(migrate, "code_version", return_value="0.3.1"):
                applied = migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)

            self.assertEqual(applied, ["m_0_3_0__0_3_1"])
            for name in ("specseed.db", "tracking_local.db", "tracking_remote_local.db"):
                self.assertEqual((storage / name).read_bytes(), name.encode())
            self.assertEqual((storage / "specseed.db-wal").read_bytes(), b"wal")
            self.assertEqual(list((specseed_dir / "specseed_runtime").rglob("*.db*")), [])
            cfg = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
            self.assertNotIn("version", cfg)
            self.assertEqual(cfg["dev_branch"], "custom")
            self.assertEqual(migrate.storage_version(storage), "0.3.1")

    def test_idempotent_second_run_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))
            with mock.patch.object(migrate, "code_version", return_value="0.3.1"):
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
            self.assertTrue((specseed_dir / "specseed_runtime" / "db" / "specseed.db").exists())

    def test_unmigratable_old_storage_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp))
            migrate.write_storage_version("0.2.0", storage)

            with self.assertRaises(RuntimeError):
                migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)


class Hop_0_3_1__0_4_0_Test(unittest.TestCase):
    """Deletes engine code an old installer copied into the target; keeps data."""

    def _installed_old_shape(self, root: Path) -> tuple[Path, Path]:
        specseed_dir, storage = _fixture_tree(root)  # also writes skills/ (copied code)
        # Old installs copied the engine in, under either package name.
        for code_dir in ("specseed_runtime", "specseed_target_src"):
            (specseed_dir / code_dir / "executing").mkdir(parents=True)
            (specseed_dir / code_dir / "executing" / "run.py").write_text("# copied\n", encoding="utf-8")
        storage.mkdir(parents=True)
        (storage / "configuration.json").write_text('{"keep": true}\n', encoding="utf-8")
        (specseed_dir / "spec").mkdir()
        (specseed_dir / "spec" / "vision.md").write_text("# vision\n", encoding="utf-8")
        migrate.write_storage_version("0.3.1", storage)
        return specseed_dir, storage

    def test_deletes_copied_code_keeps_storage_and_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._installed_old_shape(Path(tmp))

            applied = migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)

            self.assertIn("m_0_3_1__0_4_0", applied)
            self.assertFalse((specseed_dir / "specseed_runtime").exists())
            self.assertFalse((specseed_dir / "specseed_target_src").exists())
            self.assertFalse((specseed_dir / "skills").exists())
            # data survives untouched
            self.assertEqual(
                (storage / "configuration.json").read_text(encoding="utf-8"), '{"keep": true}\n'
            )
            self.assertEqual((specseed_dir / "spec" / "vision.md").read_text(encoding="utf-8"), "# vision\n")
            self.assertEqual(migrate.storage_version(storage), migrate.code_version())

    def test_run_returns_deleted_dirs_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, _storage = self._installed_old_shape(Path(tmp))

            deleted = {p.name for p in m_0_3_1__0_4_0.run(_storage, specseed_dir)}
            self.assertEqual(deleted, {"specseed_runtime", "specseed_target_src", "skills"})

            # nothing left to delete the second time
            self.assertEqual(m_0_3_1__0_4_0.run(_storage, specseed_dir), [])


class Hop_0_4_0__0_5_0_Test(unittest.TestCase):
    def _old_shape(self, root: Path) -> tuple[Path, Path]:
        specseed_dir, storage = _fixture_tree(root, version="0.5.0")
        storage.mkdir(parents=True)
        migrate.write_storage_version("0.4.0", storage)
        (storage / "seed_state.json").write_text('{"kind": "remote_github"}', encoding="utf-8")
        (storage / "configuration.json").write_text('{"keep": true}\n', encoding="utf-8")
        return specseed_dir, storage

    def test_deletes_seed_marker_forcing_reseed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))

            applied = migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)

            self.assertIn("m_0_4_0__0_5_0", applied)
            self.assertFalse((storage / "seed_state.json").exists())
            # unrelated storage data survives
            self.assertEqual(
                (storage / "configuration.json").read_text(encoding="utf-8"), '{"keep": true}\n'
            )
            self.assertEqual(migrate.storage_version(storage), migrate.code_version())

    def test_run_returns_marker_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _specseed_dir, storage = self._old_shape(Path(tmp))

            deleted = [p.name for p in m_0_4_0__0_5_0.run(storage, _specseed_dir)]
            self.assertEqual(deleted, ["seed_state.json"])

            self.assertEqual(m_0_4_0__0_5_0.run(storage, _specseed_dir), [])


if __name__ == "__main__":
    unittest.main()
