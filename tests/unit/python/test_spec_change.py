"""spec_change.py + resolve_remote.py - the spec-change worker's seams.

Covers the enqueue helper the skill calls after writing a reconcile script, and
the config-driven remote resolver the generated script imports. No GitHub/GitLab
is contacted: the local stand-in is the resolved remote here.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from specseed_runtime.db.database import Database
from specseed_runtime.scheduling.spec_change import (
    DEFAULT_SCRIPT_NAME,
    SPEC_CHANGE_ACTION,
    SPEC_CHANGE_PROPOSE_ACTION,
    enqueue_spec_change_propose,
    enqueue_spec_change_run,
    spec_change_dir,
    spec_change_root,
    spec_change_spec_dir,
    staged_spec_files,
)
from specseed_runtime.tracking.resolve_remote import (
    resolve_local,
    resolve_remote,
)
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal


class SpecChangePathsTest(unittest.TestCase):
    def test_spec_change_dir_is_under_storage_spec_change(self) -> None:
        with tempfile.TemporaryDirectory() as storage:
            root = spec_change_root(storage)
            self.assertEqual(root, Path(storage) / "spec-change")
            self.assertEqual(spec_change_dir(42, storage), root / "42")

    def test_spec_change_spec_dir_mirrors_live_spec_under_request(self) -> None:
        with tempfile.TemporaryDirectory() as storage:
            self.assertEqual(
                spec_change_spec_dir(42, storage),
                Path(storage) / "spec-change" / "42" / "spec",
            )

    def test_staged_spec_files_lists_files_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as storage:
            self.assertEqual(staged_spec_files(7, storage), [])  # nothing staged
            staged = spec_change_spec_dir(7, storage)
            (staged / "components").mkdir(parents=True)
            (staged / "sad.md").write_text("# sad\n", encoding="utf-8")
            (staged / "components" / "api-srs.md").write_text("# srs\n", encoding="utf-8")
            found = {p.relative_to(staged).as_posix() for p in staged_spec_files(7, storage)}
            self.assertEqual(found, {"sad.md", "components/api-srs.md"})


class EnqueueSpecChangeTest(unittest.TestCase):
    def _db(self) -> Database:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Database(db_path=Path(tmp.name) / "queue.db")

    def test_enqueue_absolute_script_records_action_and_payload(self) -> None:
        db = self._db()
        with tempfile.TemporaryDirectory() as storage:
            script = spec_change_dir("7", storage) / DEFAULT_SCRIPT_NAME
            script.parent.mkdir(parents=True)
            script.write_text("# apply\n", encoding="utf-8")

            task_id = enqueue_spec_change_run(
                script, request_id="7", route="adapt", db=db, storage=storage
            )

            task = db.get_task(task_id)
            self.assertEqual(task["action"], SPEC_CHANGE_ACTION)
            self.assertEqual(task["post_id"], "7")
            self.assertEqual(task["payload"]["script"], DEFAULT_SCRIPT_NAME)
            self.assertEqual(task["payload"]["dir"], str(script.parent.resolve()))
            self.assertEqual(task["payload"]["route"], "adapt")
            self.assertEqual(task["payload"]["request_id"], "7")
            self.assertFalse(task["payload"]["close_request"])  # off by default
            self.assertEqual(db.pending_count(), 1)

    def test_enqueue_close_request_flag_is_recorded(self) -> None:
        # The approval-path apply is tagged so the executor closes the request post.
        db = self._db()
        with tempfile.TemporaryDirectory() as storage:
            script = spec_change_dir("8", storage) / DEFAULT_SCRIPT_NAME
            script.parent.mkdir(parents=True)
            script.write_text("# apply\n", encoding="utf-8")
            task_id = enqueue_spec_change_run(
                script, request_id="8", route="adapt", db=db, storage=storage,
                close_request=True,
            )
            self.assertTrue(db.get_task(task_id)["payload"]["close_request"])

    def _write_script(self, storage: str, request_id: str) -> Path:
        script = spec_change_dir(request_id, storage) / DEFAULT_SCRIPT_NAME
        script.parent.mkdir(parents=True)
        script.write_text("# apply\n", encoding="utf-8")
        return script

    def test_enqueue_relative_script_resolves_against_request_dir(self) -> None:
        db = self._db()
        with tempfile.TemporaryDirectory() as storage:
            self._write_script(storage, "9")
            task_id = enqueue_spec_change_run(
                DEFAULT_SCRIPT_NAME, request_id="9", route="tweak", db=db, storage=storage
            )
            expected = spec_change_dir("9", storage).resolve()
            self.assertEqual(db.get_task(task_id)["payload"]["dir"], str(expected))

    def test_relative_script_without_request_id_is_rejected(self) -> None:
        db = self._db()
        with self.assertRaises(ValueError):
            enqueue_spec_change_run(DEFAULT_SCRIPT_NAME, db=db)

    def test_repo_root_relative_script_snaps_to_request_dir(self) -> None:
        # Agents pass ".specseed/storage/spec-change/<id>/apply.py" (relative to
        # repo root). Joined naively under the spec-change dir the prefix doubles;
        # enqueue must snap to <dir>/<basename>.
        db = self._db()
        with tempfile.TemporaryDirectory() as storage:
            self._write_script(storage, "16")
            doubled = Path(".specseed/storage/spec-change/16") / DEFAULT_SCRIPT_NAME
            task_id = enqueue_spec_change_run(
                doubled, request_id="16", route="adapt", db=db, storage=storage
            )
            payload = db.get_task(task_id)["payload"]
            self.assertEqual(payload["dir"], str(spec_change_dir("16", storage).resolve()))
            self.assertEqual(payload["script"], DEFAULT_SCRIPT_NAME)

    def test_wrong_absolute_script_snaps_to_request_dir(self) -> None:
        db = self._db()
        with tempfile.TemporaryDirectory() as storage:
            self._write_script(storage, "16")
            wrong = (
                spec_change_dir("16", storage)
                / ".specseed/storage/spec-change/16"
                / DEFAULT_SCRIPT_NAME
            )
            task_id = enqueue_spec_change_run(
                wrong, request_id="16", route="adapt", db=db, storage=storage
            )
            payload = db.get_task(task_id)["payload"]
            self.assertEqual(payload["dir"], str(spec_change_dir("16", storage).resolve()))

    def test_missing_script_fails_loud_at_enqueue(self) -> None:
        db = self._db()
        with tempfile.TemporaryDirectory() as storage:
            with self.assertRaises(ValueError):
                enqueue_spec_change_run(
                    DEFAULT_SCRIPT_NAME, request_id="9", route="tweak", db=db, storage=storage
                )
            self.assertEqual(db.pending_count(), 0)

    def test_enqueue_propose_records_action_and_request(self) -> None:
        db = self._db()
        task_id = enqueue_spec_change_propose("7", route="adapt", db=db)
        task = db.get_task(task_id)
        self.assertEqual(task["action"], SPEC_CHANGE_PROPOSE_ACTION)
        self.assertEqual(task["post_id"], "7")
        self.assertEqual(task["payload"]["request_id"], "7")
        self.assertEqual(task["payload"]["route"], "adapt")
        self.assertEqual(db.pending_count(), 1)


class ResolveRemoteTest(unittest.TestCase):
    def _write_config(self, storage: str, config: dict, remote: dict | None = None) -> None:
        from specseed_runtime.storage_paths import config_file, remote_file
        cf = config_file(storage)
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(config), encoding="utf-8")
        if remote is not None:
            remote_file(storage).write_text(json.dumps(remote), encoding="utf-8")

    def test_missing_config_resolves_to_local_stand_in(self) -> None:
        with tempfile.TemporaryDirectory() as storage:
            self.assertIsInstance(resolve_remote(storage), TrackingRemoteLocal)

    def test_disabled_remote_resolves_to_local_stand_in(self) -> None:
        with tempfile.TemporaryDirectory() as storage:
            self._write_config(storage, {}, {"enabled": False})
            self.assertIsInstance(resolve_remote(storage), TrackingRemoteLocal)

    def test_enabled_remote_without_repo_raises(self) -> None:
        with tempfile.TemporaryDirectory() as storage:
            self._write_config(
                storage,
                {},
                {"enabled": True, "provider": "github", "repo": None},
            )
            with self.assertRaises(ValueError):
                resolve_remote(storage)

    def test_resolve_local_returns_local_tracker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(resolve_local(Path(tmp) / "t.db"), TrackingLocal)


if __name__ == "__main__":
    unittest.main()
