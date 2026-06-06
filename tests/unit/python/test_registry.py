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


class DevModeTest(unittest.TestCase):
    """Dev checkout (src/specseed_runtime/) uses <repo>/data-dev, installed uses ~/.specseed."""

    def test_is_dev_true_in_this_checkout(self) -> None:
        # registry.py lives at <repo>/src/specseed_runtime/ here.
        self.assertTrue(registry.is_dev())

    def test_installed_copy_is_not_dev(self) -> None:
        # install.sh copies the same src/specseed_runtime layout to ~/.specseed.
        here = Path("/home/u/.specseed/src/specseed_runtime/registry.py")
        self.assertFalse(registry._looks_dev(here, Path("/home/u")))

    def test_checkout_outside_install_dir_is_dev(self) -> None:
        here = Path("/home/u/work/specseed/src/specseed_runtime/registry.py")
        self.assertTrue(registry._looks_dev(here, Path("/home/u")))

    def test_wrong_layout_is_not_dev(self) -> None:
        here = Path("/opt/specseed/specseed_runtime/registry.py")  # no src/ parent
        self.assertFalse(registry._looks_dev(here, Path("/home/u")))

    def test_dev_home_is_repo_data_dev(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("SPECSEED_HOME", None)
            self.assertEqual(registry.specseed_home(), registry.dev_root() / "data-dev")

    def test_installed_home_is_dot_specseed(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("SPECSEED_HOME", None)
            with mock.patch.object(registry, "is_dev", return_value=False):
                self.assertEqual(registry.specseed_home(), (Path.home() / ".specseed").resolve())

    def test_env_override_wins_over_dev(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"SPECSEED_HOME": tmp}):
                self.assertEqual(registry.specseed_home(), Path(tmp).resolve())


if __name__ == "__main__":
    unittest.main()
