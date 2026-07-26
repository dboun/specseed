"""run.py - first-run remote seeding wired into the launcher."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specseed_runtime.db.database import Database
from specseed_runtime.executing import run
from specseed_runtime.storage_paths import config_file, runner_file, seed_marker_file
from specseed_runtime.executing.agent_runner import (
    ClaudeAgentRunner,
    CodexAgentRunner,
)
from specseed_runtime.tracking.populate_defaults import (
    FIRST_ADAPT_DRAFT_TITLE,
)
from specseed_runtime.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


class BackendKindTest(unittest.TestCase):
    def test_maps_remote_state_to_populate_kind(self) -> None:
        self.assertEqual(run._backend_kind({"enabled": False}), "remote_local")
        self.assertEqual(run._backend_kind({}), "remote_local")
        self.assertEqual(
            run._backend_kind({"enabled": True, "provider": "github"}),
            "remote_github",
        )
        self.assertEqual(
            run._backend_kind({"enabled": True, "provider": "gitlab"}),
            "remote_gitlab",
        )

    def test_rejects_enabled_remote_without_known_provider(self) -> None:
        with self.assertRaises(ValueError):
            run._backend_kind({"enabled": True, "provider": None})


class EnsureRemoteSeededTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name) / "storage"
        self.storage.mkdir(parents=True)
        self.remote = TrackingRemoteLocal(
            db_path=Path(self.tmp.name) / "tracking_remote_local.db",
            author="remote",
        )

    def _titles(self) -> set[str]:
        return {entry.title for entry in self.remote.list_entries(is_open=None).data}

    def test_first_run_seeds_then_marker_skips_subsequent_runs(self) -> None:
        config = {"enabled": False, "provider": None}
        with mock.patch.object(run, "resolve_remote", return_value=self.remote) as resolve:
            first = run.ensure_remote_seeded(self.storage, config)
            second = run.ensure_remote_seeded(self.storage, config)

        # First run seeds the draft adapt post; second run is skipped by the marker.
        self.assertIsNotNone(first)
        self.assertTrue(first["default_posts"][FIRST_ADAPT_DRAFT_TITLE]["created"])
        self.assertIsNone(second)
        self.assertEqual(resolve.call_count, 1)

        self.assertIn(FIRST_ADAPT_DRAFT_TITLE, self._titles())
        marker = json.loads(seed_marker_file(self.storage).read_text(encoding="utf-8"))
        self.assertEqual(marker, {"kind": "remote_local", "repo": None})

    def test_changed_backend_reseeds(self) -> None:
        config = {"enabled": False, "provider": None}
        with mock.patch.object(run, "resolve_remote", return_value=self.remote):
            run.ensure_remote_seeded(self.storage, config)

        # A stale marker for a different backend must not block re-seeding.
        seed_marker_file(self.storage).write_text(
            json.dumps({"kind": "remote_github", "repo": "o/r"}) + "\n",
            encoding="utf-8",
        )
        with mock.patch.object(run, "resolve_remote", return_value=self.remote) as resolve:
            again = run.ensure_remote_seeded(self.storage, config)
        self.assertIsNotNone(again)
        self.assertEqual(resolve.call_count, 1)


class BuildSchedulerMigratesStorageTest(unittest.TestCase):
    def test_build_scheduler_migrates_storage_first(self) -> None:
        """The launcher's storage migrates before config/db reads."""
        with tempfile.TemporaryDirectory() as tmp:
            specseed_dir = Path(tmp) / ".specseed"
            version_file = specseed_dir / "skills" / "specseed" / "version.txt"
            version_file.parent.mkdir(parents=True)
            version_file.write_text("0.3.1\n", encoding="utf-8")
            storage = specseed_dir / "storage"
            stray = specseed_dir / "specseed_runtime" / "db" / "specseed.db"
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"queue-bytes")

            scheduler = run.build_scheduler(
                storage=storage,
                db=Database(db_path=Path(tmp) / "queue.db"),
                runner=mock.Mock(),
            )

            self.assertIsNotNone(scheduler)
            # After the 0.21 reshape the queue db lands in db/ and the marker in config/.
            self.assertEqual((storage / "db" / "specseed.db").read_bytes(), b"queue-bytes")
            self.assertFalse(stray.exists())
            # Migrates up to the running engine (target's copied skills don't pin it).
            from specseed_runtime.migrating.migrate import code_version
            self.assertEqual(
                (storage / "config" / "version.txt").read_text(encoding="utf-8").strip(),
                code_version(),
            )


class BuildSchedulerRefusesOwnedStorageTest(unittest.TestCase):
    """One runner per storage: a live owner refuses a second build (whose
    startup reclaim would kill the owner's in-flight agent)."""

    def _stale_owner(self, storage: Path, pid: int = 4242) -> None:
        rf = runner_file(storage)
        rf.parent.mkdir(parents=True, exist_ok=True)
        rf.write_text(
            json.dumps(
                {"pid": pid, "state": "running", "updated_at": "2000-01-01T00:00:00Z"}
            ),
            encoding="utf-8",
        )

    def test_live_owner_refuses_second_runner(self) -> None:
        from specseed_runtime.executing import runner_control

        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / ".specseed" / "storage"
            self._stale_owner(storage)
            with mock.patch.object(runner_control, "pid_is_runner", return_value=True):
                with self.assertRaises(RuntimeError) as caught:
                    run.build_scheduler(
                        storage=storage,
                        repo_root=tmp,
                        db=Database(db_path=Path(tmp) / "queue.db"),
                        runner=mock.Mock(),
                    )
            self.assertIn("already owns", str(caught.exception))

    def test_dead_owner_builds_and_reclaims(self) -> None:
        from specseed_runtime.executing import runner_control

        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / ".specseed" / "storage"
            self._stale_owner(storage)
            db = Database(db_path=Path(tmp) / "queue.db")
            task_id = db.enqueue("handle_entry_created", post_id="1", payload={})
            db.claim_next()  # orphaned in_progress from the dead owner
            with mock.patch.object(runner_control, "pid_is_runner", return_value=False):
                scheduler = run.build_scheduler(
                    storage=storage, repo_root=tmp, db=db, runner=mock.Mock()
                )
            self.assertIsNotNone(scheduler)
            self.assertEqual(db.get_task(task_id)["status"], "pending")


class BuildSchedulerConfigReloadTest(unittest.TestCase):
    """A config-built scheduler re-reads configuration.json on resume."""

    def _write_config(self, storage: Path, provider: str) -> None:
        cf = config_file(storage)
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(
            json.dumps({"runner": {"implementation": [{"provider": provider}]}}),
            encoding="utf-8",
        )

    def test_resume_picks_up_edited_configuration_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / ".specseed" / "storage"
            self._write_config(storage, "claude")
            scheduler = run.build_scheduler(
                storage=storage,
                repo_root=tmp,
                db=Database(db_path=Path(tmp) / "queue.db"),
            )
            self.assertIsInstance(
                scheduler.runner.chain_for("implementation")[0], ClaudeAgentRunner
            )

            self._write_config(storage, "codex")  # the paused-runner config edit
            scheduler.resume()
            self.assertIsInstance(
                scheduler.runner.chain_for("implementation")[0], CodexAgentRunner
            )
            scheduler.request_stop()

    def test_injected_runner_never_swapped_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / ".specseed" / "storage"
            self._write_config(storage, "claude")
            double = mock.Mock()
            scheduler = run.build_scheduler(
                storage=storage,
                repo_root=tmp,
                db=Database(db_path=Path(tmp) / "queue.db"),
                runner=double,
            )
            self._write_config(storage, "codex")
            scheduler.resume()
            self.assertEqual(scheduler.runner.chain_for("implementation"), [double])
            scheduler.request_stop()


if __name__ == "__main__":
    unittest.main()
