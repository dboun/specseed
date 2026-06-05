"""test_registry.py - the global multi-repo registry, isolated under SPECSEED_HOME."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specseed_runtime import registry


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.target = Path(self.tmp.name) / "repo"
        self.target.mkdir()
        self._env = mock.patch.dict(os.environ, {"SPECSEED_HOME": str(self.home)})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_add_creates_record_with_derived_storage(self) -> None:
        record = registry.add_repo(self.target, provider="local")
        self.assertEqual(record["provider"], "local")
        self.assertEqual(Path(record["target"]), self.target.resolve())
        self.assertEqual(
            Path(record["storage"]), self.target.resolve() / ".specseed" / "storage"
        )
        self.assertTrue(registry.registry_file().is_file())

    def test_add_is_idempotent_on_target(self) -> None:
        first = registry.add_repo(self.target, provider="local")
        second = registry.add_repo(self.target, provider="local")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(registry.list_repos()), 1)

    def test_provider_is_final(self) -> None:
        registry.add_repo(self.target, provider="local")
        again = registry.add_repo(self.target, provider="github")
        self.assertEqual(again["provider"], "local")

    def test_unsupported_provider_raises(self) -> None:
        with self.assertRaises(ValueError):
            registry.add_repo(self.target, provider="bitbucket")

    def test_get_repo_by_id_name_and_path(self) -> None:
        record = registry.add_repo(self.target, provider="local", name="widget")
        self.assertEqual(registry.get_repo(record["id"])["id"], record["id"])
        self.assertEqual(registry.get_repo("widget")["id"], record["id"])
        self.assertEqual(registry.get_repo(str(self.target))["id"], record["id"])
        self.assertIsNone(registry.get_repo("nope"))

    def test_remove_repo_drops_record(self) -> None:
        record = registry.add_repo(self.target, provider="local")
        removed = registry.remove_repo(record["id"])
        self.assertEqual(removed["id"], record["id"])
        self.assertEqual(registry.list_repos(), [])


if __name__ == "__main__":
    unittest.main()
