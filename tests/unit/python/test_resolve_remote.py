"""test_resolve_remote.py - local stand-in authorship from config.

The UI's human writes and the runtime's platform writes share one local db; the
platform authors as ``platform_username`` so the UI can tell them apart. Older
blank configs keep the ``"remote"`` fallback.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from specseed_runtime.tracking.resolve_remote import resolve_remote


class ResolveRemoteAuthorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)

    def _write_config(self, cfg: dict) -> None:
        from specseed_runtime.storage_paths import config_file
        cf = config_file(self.storage)
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(cfg), encoding="utf-8")

    def test_author_is_platform_username_when_set(self) -> None:
        self._write_config({"platform_username": "specseed"})
        tracker = resolve_remote(self.storage)
        self.assertEqual(tracker.author, "specseed")

    def test_author_falls_back_to_remote_when_blank(self) -> None:
        self._write_config({"platform_username": ""})
        self.assertEqual(resolve_remote(self.storage).author, "remote")

    def test_author_falls_back_to_remote_when_missing(self) -> None:
        # no configuration.json at all
        self.assertEqual(resolve_remote(self.storage).author, "remote")


if __name__ == "__main__":
    unittest.main()
