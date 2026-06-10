"""migrating/ - storage version marker + hop chain behavior.

Pattern for new hop coverage: build an old-shape fixture under tmp, call
``run_migrations(storage=..., specseed_dir=...)``, assert the upgraded files +
marker, then run again and assert nothing changes (idempotency).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specseed_runtime.migrating import m_0_3_0__0_3_1
from specseed_runtime.migrating import m_0_3_1__0_4_0
from specseed_runtime.migrating import m_0_4_0__0_5_0
from specseed_runtime.migrating import m_0_5_0__0_7_0
from specseed_runtime.migrating import m_0_19_0__0_20_0
from specseed_runtime.migrating import migrate
from specseed_runtime import storage_paths


_HAS_GIT = shutil.which("git") is not None


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
                applied,
                ["m_0_3_0__0_3_1", "m_0_3_1__0_4_0", "m_0_4_0__0_5_0",
                 "m_0_5_0__0_7_0", "m_0_7_0__0_9_0", "m_0_9_0__0_11_0",
                 "m_0_11_0__0_12_0", "m_0_12_0__0_13_0", "m_0_13_0__0_14_0",
                 "m_0_14_0__0_16_0", "m_0_16_0__0_18_0", "m_0_18_0__0_19_0",
                 "m_0_19_0__0_20_0"],
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
            # Sentinel must stay above the real engine version (code_version ignores
            # the fixture tree and reads the repo's skills/specseed/version.txt).
            migrate.write_storage_version("99.0.0", storage)

            self.assertEqual(migrate.run_migrations(storage=storage, specseed_dir=specseed_dir), [])
            self.assertEqual(migrate.storage_version(storage), "99.0.0")

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


class Hop050To070Test(unittest.TestCase):
    """0.5.0 -> 0.7.0: tasks.not_before column + label re-seed marker drop."""

    def _old_shape(self, root: Path, version: str = "0.6.4") -> tuple[Path, Path]:
        import sqlite3

        specseed_dir = root / ".specseed"
        storage = specseed_dir / "storage"
        storage.mkdir(parents=True)
        skill_version = specseed_dir / "skills" / "specseed" / "version.txt"
        skill_version.parent.mkdir(parents=True)
        skill_version.write_text("0.7.0\n", encoding="utf-8")
        migrate.write_storage_version(version, storage)
        (storage / "seed_state.json").write_text('{"kind": "remote_local"}\n', encoding="utf-8")
        # old-shape queue db: tasks WITHOUT not_before, with one live row
        with sqlite3.connect(storage / "specseed.db") as conn:
            conn.execute(
                """CREATE TABLE tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
                    post_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, last_attempted_at TEXT)"""
            )
            conn.execute(
                "INSERT INTO tasks(action, post_id, created_at) VALUES ('handle_entry_created', '5', 'x')"
            )
        return specseed_dir, storage

    def _columns(self, storage: Path) -> set[str]:
        import sqlite3

        with sqlite3.connect(storage / "specseed.db") as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}

    def test_hop_adds_column_and_drops_seed_marker(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))

            applied = migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)

            self.assertIn("m_0_5_0__0_7_0", applied)
            self.assertIn("not_before", self._columns(storage))
            self.assertFalse((storage / "seed_state.json").exists())
            self.assertEqual(migrate.storage_version(storage), migrate.code_version())
            # the old row survives and the new column reads NULL
            with sqlite3.connect(storage / "specseed.db") as conn:
                row = conn.execute("SELECT post_id, not_before FROM tasks").fetchone()
            self.assertEqual(row, ("5", None))

    def test_hop_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))
            first = m_0_5_0__0_7_0.run(storage, specseed_dir)
            self.assertEqual(
                sorted(p.name for p in first), ["seed_state.json", "specseed.db"]
            )
            self.assertEqual(m_0_5_0__0_7_0.run(storage, specseed_dir), [])
            self.assertIn("not_before", self._columns(storage))

    def test_missing_db_is_noop_for_db_half(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))
            (storage / "specseed.db").unlink()
            changed = [p.name for p in m_0_5_0__0_7_0.run(storage, specseed_dir)]
            self.assertEqual(changed, ["seed_state.json"])


class Hop0160To0180Test(unittest.TestCase):
    """0.16.0 -> 0.18.0: tasks.lane + tasks.priority columns for the two lanes."""

    def _old_shape(self, root: Path, version: str = "0.17.0") -> tuple[Path, Path]:
        import sqlite3

        from specseed_runtime.migrating import m_0_16_0__0_18_0  # noqa: F401

        specseed_dir = root / ".specseed"
        storage = specseed_dir / "storage"
        storage.mkdir(parents=True)
        skill_version = specseed_dir / "skills" / "specseed" / "version.txt"
        skill_version.parent.mkdir(parents=True)
        skill_version.write_text("0.18.0\n", encoding="utf-8")
        migrate.write_storage_version(version, storage)
        # old-shape queue db: tasks WITHOUT lane/priority, one live row
        with sqlite3.connect(storage / "specseed.db") as conn:
            conn.execute(
                """CREATE TABLE tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
                    post_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, last_attempted_at TEXT, not_before TEXT)"""
            )
            conn.execute(
                "INSERT INTO tasks(action, post_id, created_at) VALUES ('handle_entry_created', '5', 'x')"
            )
        return specseed_dir, storage

    def _columns(self, storage: Path) -> set[str]:
        import sqlite3

        with sqlite3.connect(storage / "specseed.db") as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}

    def test_hop_adds_lane_and_priority_with_safe_defaults(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))

            applied = migrate.run_migrations(storage=storage, specseed_dir=specseed_dir)

            self.assertIn("m_0_16_0__0_18_0", applied)
            cols = self._columns(storage)
            self.assertIn("lane", cols)
            self.assertIn("priority", cols)
            # the pre-split row defaults to the control lane at the default priority
            with sqlite3.connect(storage / "specseed.db") as conn:
                row = conn.execute("SELECT post_id, lane, priority FROM tasks").fetchone()
            self.assertEqual(row, ("5", "control", 50))

    def test_hop_is_idempotent(self) -> None:
        from specseed_runtime.migrating import m_0_16_0__0_18_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))
            first = [p.name for p in m_0_16_0__0_18_0.run(storage, specseed_dir)]
            self.assertEqual(first, ["specseed.db"])
            self.assertEqual(m_0_16_0__0_18_0.run(storage, specseed_dir), [])
            self.assertEqual(self._columns(storage) & {"lane", "priority"}, {"lane", "priority"})

    def test_missing_db_is_noop(self) -> None:
        from specseed_runtime.migrating import m_0_16_0__0_18_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = self._old_shape(Path(tmp))
            (storage / "specseed.db").unlink()
            self.assertEqual(m_0_16_0__0_18_0.run(storage, specseed_dir), [])


class Hop070To090Test(unittest.TestCase):
    """0.9.0: drop permissions.git.enabled; bump default-shaped confidence."""

    def _old_shape(self, root: Path, cfg: dict) -> tuple[Path, Path]:
        specseed_dir, storage = _fixture_tree(root, version="0.9.0")
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "configuration.json").write_text(json.dumps(cfg), encoding="utf-8")
        return specseed_dir, storage

    def test_drops_git_enabled_and_bumps_default_confidence(self) -> None:
        from specseed_runtime.migrating import m_0_7_0__0_9_0

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "permissions": {"git": {"enabled": True, "merge_to_dev_branch": True}},
                "review": {"enabled": True, "confidence_threshold": 0.75},
            }
            specseed_dir, storage = self._old_shape(Path(tmp), cfg)
            changed = m_0_7_0__0_9_0.run(storage, specseed_dir)
            self.assertEqual([p.name for p in changed], ["configuration.json"])
            out = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
            self.assertNotIn("enabled", out["permissions"]["git"])
            self.assertTrue(out["permissions"]["git"]["merge_to_dev_branch"])
            self.assertEqual(out["review"]["confidence_threshold"], 0.95)

    def test_keeps_custom_confidence(self) -> None:
        from specseed_runtime.migrating import m_0_7_0__0_9_0

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"permissions": {"git": {}}, "review": {"confidence_threshold": 0.6}}
            specseed_dir, storage = self._old_shape(Path(tmp), cfg)
            m_0_7_0__0_9_0.run(storage, specseed_dir)
            out = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
            self.assertEqual(out["review"]["confidence_threshold"], 0.6)

    def test_idempotent_and_noop_when_clean(self) -> None:
        from specseed_runtime.migrating import m_0_7_0__0_9_0

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"permissions": {"git": {"merge_to_dev_branch": False}},
                   "review": {"confidence_threshold": 0.95}}
            specseed_dir, storage = self._old_shape(Path(tmp), cfg)
            self.assertEqual(m_0_7_0__0_9_0.run(storage, specseed_dir), [])

    def test_missing_config_is_noop(self) -> None:
        from specseed_runtime.migrating import m_0_7_0__0_9_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.9.0")
            storage.mkdir(parents=True, exist_ok=True)
            self.assertEqual(m_0_7_0__0_9_0.run(storage, specseed_dir), [])


class Hop090To0110Test(unittest.TestCase):
    """0.11.0: drop the seed marker so the awaiting_input label re-seeds."""

    def test_deletes_seed_marker_forcing_reseed(self) -> None:
        from specseed_runtime.migrating import m_0_9_0__0_11_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.9.0")
            storage.mkdir(parents=True, exist_ok=True)
            (storage / "seed_state.json").write_text('{"kind": "remote_local"}\n', encoding="utf-8")

            deleted = [p.name for p in m_0_9_0__0_11_0.run(storage, specseed_dir)]

            self.assertEqual(deleted, ["seed_state.json"])
            self.assertFalse((storage / "seed_state.json").exists())

    def test_idempotent_missing_marker_is_noop(self) -> None:
        from specseed_runtime.migrating import m_0_9_0__0_11_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.9.0")
            storage.mkdir(parents=True, exist_ok=True)
            self.assertEqual(m_0_9_0__0_11_0.run(storage, specseed_dir), [])
            self.assertEqual(m_0_9_0__0_11_0.run(storage, specseed_dir), [])


class Hop0110To0120Test(unittest.TestCase):
    """0.12.0: rename the dev-branch config keys to "primary"."""

    def _cfg(self, storage, cfg):
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "configuration.json").write_text(
            json.dumps(cfg) + "\n", encoding="utf-8"
        )

    def test_renames_all_three_keys_preserving_values(self) -> None:
        from specseed_runtime.migrating import m_0_11_0__0_12_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.11.0")
            self._cfg(storage, {
                "dev_branch": "develop",
                "permissions": {
                    "git": {"merge_to_dev_branch": True},
                    "remote": {"push_dev_branch": True, "make_prs": False},
                },
            })

            changed = m_0_11_0__0_12_0.run(storage, specseed_dir)

            self.assertEqual([p.name for p in changed], ["configuration.json"])
            out = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
            self.assertNotIn("dev_branch", out)
            self.assertEqual(out["specseed_primary_branch"], "develop")
            git = out["permissions"]["git"]
            self.assertNotIn("merge_to_dev_branch", git)
            self.assertTrue(git["merge_to_primary"])
            rem = out["permissions"]["remote"]
            self.assertNotIn("push_dev_branch", rem)
            self.assertTrue(rem["push_primary"])

    def test_does_not_clobber_existing_new_keys(self) -> None:
        from specseed_runtime.migrating import m_0_11_0__0_12_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.11.0")
            self._cfg(storage, {
                "dev_branch": "old",
                "specseed_primary_branch": "kept",
            })

            m_0_11_0__0_12_0.run(storage, specseed_dir)

            out = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
            self.assertEqual(out["specseed_primary_branch"], "kept")
            self.assertNotIn("dev_branch", out)

    def test_idempotent_and_noop_when_clean(self) -> None:
        from specseed_runtime.migrating import m_0_11_0__0_12_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.11.0")
            self._cfg(storage, {"specseed_primary_branch": "main", "permissions": {}})

            self.assertEqual(m_0_11_0__0_12_0.run(storage, specseed_dir), [])

    def test_missing_config_is_noop(self) -> None:
        from specseed_runtime.migrating import m_0_11_0__0_12_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.11.0")
            storage.mkdir(parents=True, exist_ok=True)
            self.assertEqual(m_0_11_0__0_12_0.run(storage, specseed_dir), [])


class Hop0120To0130Test(unittest.TestCase):
    """0.13.0: drop the dead ``permissions.remote.make_prs`` switch."""

    def _cfg(self, storage, cfg):
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "configuration.json").write_text(
            json.dumps(cfg) + "\n", encoding="utf-8"
        )

    def test_drops_make_prs_keeps_other_remote_keys(self) -> None:
        from specseed_runtime.migrating import m_0_12_0__0_13_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.12.0")
            self._cfg(storage, {
                "permissions": {
                    "remote": {"push_primary": True, "make_prs": True},
                },
            })

            changed = m_0_12_0__0_13_0.run(storage, specseed_dir)

            self.assertEqual([p.name for p in changed], ["configuration.json"])
            out = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
            rem = out["permissions"]["remote"]
            self.assertNotIn("make_prs", rem)
            self.assertTrue(rem["push_primary"])  # other keys untouched

    def test_idempotent_and_noop_when_absent(self) -> None:
        from specseed_runtime.migrating import m_0_12_0__0_13_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.12.0")
            self._cfg(storage, {"permissions": {"remote": {"push_primary": False}}})

            self.assertEqual(m_0_12_0__0_13_0.run(storage, specseed_dir), [])
            # second run after a strip is still a no-op.
            self._cfg(storage, {"permissions": {"remote": {"make_prs": True}}})
            self.assertEqual([p.name for p in m_0_12_0__0_13_0.run(storage, specseed_dir)],
                             ["configuration.json"])
            self.assertEqual(m_0_12_0__0_13_0.run(storage, specseed_dir), [])

    def test_missing_config_is_noop(self) -> None:
        from specseed_runtime.migrating import m_0_12_0__0_13_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.12.0")
            storage.mkdir(parents=True, exist_ok=True)
            self.assertEqual(m_0_12_0__0_13_0.run(storage, specseed_dir), [])


class Hop0130To0140Test(unittest.TestCase):
    """0.14.0: drop the ``gitignore_specseed_dir`` toggle (always on now)."""

    def _cfg(self, storage, cfg):
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "configuration.json").write_text(
            json.dumps(cfg) + "\n", encoding="utf-8"
        )

    def test_drops_toggle_keeps_other_keys(self) -> None:
        from specseed_runtime.migrating import m_0_13_0__0_14_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.13.0")
            self._cfg(storage, {
                "specseed_dir": ".specseed",
                "gitignore_specseed_dir": False,
            })

            changed = m_0_13_0__0_14_0.run(storage, specseed_dir)

            self.assertEqual([p.name for p in changed], ["configuration.json"])
            out = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
            self.assertNotIn("gitignore_specseed_dir", out)
            self.assertEqual(out["specseed_dir"], ".specseed")  # other keys untouched

    def test_idempotent_and_noop_when_absent(self) -> None:
        from specseed_runtime.migrating import m_0_13_0__0_14_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.13.0")
            self._cfg(storage, {"specseed_dir": ".specseed"})

            self.assertEqual(m_0_13_0__0_14_0.run(storage, specseed_dir), [])
            self._cfg(storage, {"gitignore_specseed_dir": True})
            self.assertEqual([p.name for p in m_0_13_0__0_14_0.run(storage, specseed_dir)],
                             ["configuration.json"])
            self.assertEqual(m_0_13_0__0_14_0.run(storage, specseed_dir), [])

    def test_missing_config_is_noop(self) -> None:
        from specseed_runtime.migrating import m_0_13_0__0_14_0

        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp), version="0.13.0")
            storage.mkdir(parents=True, exist_ok=True)
            self.assertEqual(m_0_13_0__0_14_0.run(storage, specseed_dir), [])


@unittest.skipUnless(_HAS_GIT, "git not available")
class Hop0180To0190Test(unittest.TestCase):
    """0.19.0: ensure the specseed dir ignore rule is committed."""

    def test_commits_specseed_gitignore_rule(self) -> None:
        from specseed_runtime.migrating import m_0_18_0__0_19_0

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            specseed_dir, storage = _fixture_tree(root, version="0.18.0")
            storage.mkdir(parents=True, exist_ok=True)
            (storage / "configuration.json").write_text(
                json.dumps({"specseed_primary_branch": "main"}) + "\n", encoding="utf-8"
            )

            m_0_18_0__0_19_0.run(storage, specseed_dir)

            show = subprocess.run(
                ["git", "show", "HEAD:.gitignore"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(show.returncode, 0)
            self.assertIn(".specseed/", show.stdout)

            # Idempotent: second run has nothing new to commit.
            before = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root, capture_output=True, text=True,
            ).stdout.strip()
            m_0_18_0__0_19_0.run(storage, specseed_dir)
            after = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root, capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual(before, after)


class Hop_0_19_0__0_20_0_Test(unittest.TestCase):
    """Strips the repo-root CLAUDE.md/AGENTS.md router block, reshapes guardrails."""

    _ROUTER = (
        "<!-- specseed:router:start -->\n"
        "**IMPORTANT (specseed):** read the guardrail file in `.specseed/` ...\n"
        "<!-- specseed:router:end -->"
    )

    def _old_shape(self, root: Path):
        specseed_dir, storage = _fixture_tree(root)
        storage.mkdir(parents=True, exist_ok=True)
        repo_root = specseed_dir.parent
        # repo-root router files: one with user content around the block, one block-only
        (repo_root / "CLAUDE.md").write_text(
            "# My project\n\nuser notes\n\n" + self._ROUTER + "\n", encoding="utf-8")
        (repo_root / "AGENTS.md").write_text(self._ROUTER + "\n", encoding="utf-8")
        # an old engine-default guardrail (retire) + a user-edited one (keep)
        (specseed_dir / "AGENTS_INSTRUCTIONS_IMPL.md").write_text(
            "# specseed agent rules (implementing a work issue)\n\nold body\n", encoding="utf-8")
        (specseed_dir / "AGENTS_INSTRUCTIONS_SPEC.md").write_text(
            "my own repo notes\n", encoding="utf-8")
        # a seed marker, so the hop drops it (question -> ask re-seeds on next startup)
        (storage / "seed_state.json").write_text('{"kind": "remote_local"}\n', encoding="utf-8")
        return specseed_dir, storage, repo_root

    def test_strips_router_keeps_user_content_and_reshapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage, repo_root = self._old_shape(Path(tmp))
            m_0_19_0__0_20_0.run(storage, specseed_dir)

            claude = (repo_root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertNotIn("specseed:router", claude)
            self.assertIn("# My project", claude)   # user content preserved
            self.assertIn("user notes", claude)
            # files kept (not deleted), only our block removed
            self.assertTrue((repo_root / "AGENTS.md").exists())
            self.assertNotIn("specseed:router", (repo_root / "AGENTS.md").read_text(encoding="utf-8"))

            # old-default guardrail retired -> empty stub; user-edited one untouched
            impl = (specseed_dir / "AGENTS_INSTRUCTIONS_IMPL.md").read_text(encoding="utf-8")
            self.assertNotIn("old body", impl)
            self.assertIn("<!-- specseed:", impl)
            self.assertEqual(
                (specseed_dir / "AGENTS_INSTRUCTIONS_SPEC.md").read_text(encoding="utf-8"),
                "my own repo notes\n",
            )
            # new ASK-route stubs seeded
            self.assertTrue((specseed_dir / "AGENTS_INSTRUCTIONS_ASK.md").exists())
            self.assertTrue((specseed_dir / "CUSTOM_INSTRUCTIONS_ASK.md").exists())
            # seed marker dropped so the `ask` label re-seeds on next startup
            self.assertFalse((storage / "seed_state.json").exists())

    def test_idempotent_second_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage, repo_root = self._old_shape(Path(tmp))
            m_0_19_0__0_20_0.run(storage, specseed_dir)
            claude1 = (repo_root / "CLAUDE.md").read_text(encoding="utf-8")
            snap = {p.name: p.read_text(encoding="utf-8") for p in specseed_dir.glob("*.md")}

            m_0_19_0__0_20_0.run(storage, specseed_dir)
            self.assertEqual((repo_root / "CLAUDE.md").read_text(encoding="utf-8"), claude1)
            self.assertEqual(
                {p.name: p.read_text(encoding="utf-8") for p in specseed_dir.glob("*.md")}, snap)

    def test_no_root_files_is_safe_and_seeds_stubs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir, storage = _fixture_tree(Path(tmp))
            storage.mkdir(parents=True, exist_ok=True)
            m_0_19_0__0_20_0.run(storage, specseed_dir)  # no CLAUDE/AGENTS present
            self.assertFalse((specseed_dir.parent / "CLAUDE.md").exists())  # never created
            self.assertTrue((specseed_dir / "AGENTS_INSTRUCTIONS_ASK.md").exists())


if __name__ == "__main__":
    unittest.main()
