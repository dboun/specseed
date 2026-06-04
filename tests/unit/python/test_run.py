"""run.py - first-run remote seeding wired into the launcher."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.target_facing.specseed_target_src.executing import run
from src.target_facing.specseed_target_src.tracking.populate_defaults import (
    FIRST_ADAPT_DRAFT_TITLE,
)
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import (
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
        marker = json.loads((self.storage / "seed_state.json").read_text(encoding="utf-8"))
        self.assertEqual(marker, {"kind": "remote_local", "repo": None})

    def test_changed_backend_reseeds(self) -> None:
        config = {"enabled": False, "provider": None}
        with mock.patch.object(run, "resolve_remote", return_value=self.remote):
            run.ensure_remote_seeded(self.storage, config)

        # A stale marker for a different backend must not block re-seeding.
        (self.storage / "seed_state.json").write_text(
            json.dumps({"kind": "remote_github", "repo": "o/r"}) + "\n",
            encoding="utf-8",
        )
        with mock.patch.object(run, "resolve_remote", return_value=self.remote) as resolve:
            again = run.ensure_remote_seeded(self.storage, config)
        self.assertIsNotNone(again)
        self.assertEqual(resolve.call_count, 1)


if __name__ == "__main__":
    unittest.main()
