"""specseed command router behavior - targets storage, never copies code."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_launcher():
    """Load the runtime command module by path."""
    path = Path(__file__).resolve().parents[3] / "src" / "specseed_runtime" / "specseed.py"
    spec = importlib.util.spec_from_file_location("specseed_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


specseed = _load_launcher()


class DevPortTest(unittest.TestCase):
    """Dev checkout serves on 5051; installed on 5050."""

    def test_dev_checkout_uses_dev_port(self) -> None:
        self.assertTrue(specseed.registry.is_dev())  # this repo IS a dev checkout
        self.assertEqual(specseed.default_port(), specseed.DEV_PORT)
        self.assertEqual(specseed.DEV_PORT, 5051)

    def test_installed_uses_default_port(self) -> None:
        with mock.patch.object(specseed.registry, "is_dev", return_value=False):
            self.assertEqual(specseed.default_port(), specseed.DEFAULT_PORT)
        self.assertEqual(specseed.DEFAULT_PORT, 5050)

    def test_web_server_env_payload_reports_dev(self) -> None:
        server = specseed._load_web_server()
        payload = server.env_payload()
        self.assertTrue(payload["dev"])
        self.assertIn("home", payload)


class ResolvePathsTest(unittest.TestCase):
    def test_returns_home_data_root_not_in_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp).resolve() / "repo"
            target.mkdir()
            with mock.patch.dict(os.environ, {"SPECSEED_HOME": str(home)}):
                got_target, data_root = specseed.resolve_paths(str(target), ".specseed")
                self.assertEqual(got_target, target)
                self.assertEqual(data_root, specseed.registry.data_root_for(target))
                # data root is in the home, NOT inside the target
                self.assertTrue(str(data_root).startswith(str(home.resolve())))
                self.assertFalse((target / ".specseed").exists())


class ParseArgsTest(unittest.TestCase):
    def test_configure_defaults(self) -> None:
        args = specseed.parse_args(["configure", "--defaults"])
        self.assertEqual(args.command, "configure")
        self.assertIsNone(args.target)
        self.assertEqual(args.specseed_dir, ".specseed")
        self.assertTrue(args.defaults)

    def test_run_flags(self) -> None:
        args = specseed.parse_args(["run", "--target", "/some/repo", "--once", "--interval", "30"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.target, "/some/repo")
        self.assertEqual(args.specseed_dir, ".specseed")
        self.assertTrue(args.once)
        self.assertEqual(args.interval, 30.0)


class MainTest(unittest.TestCase):
    def test_missing_target_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            rc = specseed.main(["run", "--target", str(missing)])
            self.assertEqual(rc, 1)

    def test_delegates_to_run_with_storage_and_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp).resolve() / "repo"
            target.mkdir()
            with mock.patch.dict(os.environ, {"SPECSEED_HOME": str(home)}):
                data_root = specseed.registry.data_root_for(target)
                with mock.patch.object(specseed.run_mod, "main", return_value=0) as run_main:
                    rc = specseed.main(
                        ["run", "--target", str(target), "--once", "--interval", "15"]
                    )

                self.assertEqual(rc, 0)
                argv = run_main.call_args.args[0]
                self.assertIn("--storage", argv)
                self.assertEqual(argv[argv.index("--storage") + 1], str(data_root))
                self.assertIn("--repo-root", argv)
                self.assertEqual(argv[argv.index("--repo-root") + 1], str(target))
                self.assertIn("--once", argv)
                self.assertEqual(argv[argv.index("--interval") + 1], "15.0")
                # data root got created in home; nothing landed in the target
                self.assertTrue(data_root.is_dir())
                self.assertFalse((target / ".specseed").exists())

    def test_configure_defaults_writes_config_and_remote_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp).resolve() / "repo"
            target.mkdir()
            with mock.patch.dict(os.environ, {"SPECSEED_HOME": str(home)}):
                rc = specseed.main(["configure", "--target", str(target), "--defaults"])
                self.assertEqual(rc, 0)
                dr = specseed.registry.data_root_for(target)
                self.assertTrue((dr / "config" / "configuration.json").is_file())
                self.assertTrue((dr / "config" / "remote.json").is_file())
                self.assertFalse((target / ".specseed").exists())

    def test_configure_defaults_overwrite_resets_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp).resolve() / "repo"
            target.mkdir()
            self._home = mock.patch.dict(os.environ, {"SPECSEED_HOME": str(home)})
            self._home.start()
            self.addCleanup(self._home.stop)
            self.assertEqual(
                specseed.main(["configure", "--target", str(target), "--defaults"]),
                0,
            )
            storage = specseed.registry.data_root_for(target) / "config"
            cfg_path = storage / "configuration.json"
            remote_path = storage / "remote.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["poll_interval_seconds"] = 99
            cfg_path.write_text(json.dumps(cfg) + "\n", encoding="utf-8")
            remote = json.loads(remote_path.read_text(encoding="utf-8"))
            remote["repo"] = "kept/repo"
            remote_path.write_text(json.dumps(remote) + "\n", encoding="utf-8")

            self.assertEqual(
                specseed.main(["configure", "--target", str(target), "--defaults"]),
                0,
            )
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            remote = json.loads(remote_path.read_text(encoding="utf-8"))
            self.assertEqual(cfg["poll_interval_seconds"], 99)
            self.assertEqual(remote["repo"], "kept/repo")

            self.assertEqual(
                specseed.main(["configure", "--target", str(target), "--defaults-overwrite"]),
                0,
            )
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            remote = json.loads(remote_path.read_text(encoding="utf-8"))
            self.assertEqual(cfg["poll_interval_seconds"], specseed.configure.DEFAULT_POLL_INTERVAL)
            self.assertIsNone(remote["repo"])

    def test_configure_keeps_target_clean_no_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "repo"
            target.mkdir()
            with mock.patch.dict(os.environ, {"SPECSEED_HOME": str(home)}):
                rc = specseed.main(["configure", "--target", str(target), "--defaults"])
                self.assertEqual(rc, 0)
                dr = specseed.registry.data_root_for(target)
                self.assertTrue((dr / "config" / "configuration.json").is_file())
                # target stays clean: no .specseed, no .gitignore line
                self.assertFalse((target / ".specseed").exists())
                self.assertFalse((target / ".gitignore").exists())

    def test_configure_uses_config_and_remote_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "repo"
            target.mkdir()
            config_path = Path(tmp) / "configuration.json"
            remote_path = Path(tmp) / "remote.json"
            config_path.write_text(
                json.dumps({"poll_interval_seconds": 12, "specseed_primary_branch": "develop"}) + "\n",
                encoding="utf-8",
            )
            remote_path.write_text(
                json.dumps({"enabled": False, "repo": "local/example"}) + "\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"SPECSEED_HOME": str(home)}):
                rc = specseed.main(
                    [
                        "configure",
                        "--target",
                        str(target),
                        "--use-config-file",
                        str(config_path),
                        "--use-remote-file",
                        str(remote_path),
                    ]
                )

                self.assertEqual(rc, 0)
                storage = specseed.registry.data_root_for(target) / "config"
                cfg = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
                remote = json.loads((storage / "remote.json").read_text(encoding="utf-8"))
                self.assertEqual(cfg["poll_interval_seconds"], 12)
                self.assertEqual(cfg["specseed_primary_branch"], "develop")
                self.assertEqual(remote["repo"], "local/example")
                # target stays clean - no gitignore line is added anymore
                self.assertFalse((target / ".gitignore").exists())

class ManagementTest(unittest.TestCase):
    """add / list / start / pause over the global registry (isolated home)."""

    def _env(self, home: Path):
        return mock.patch.dict(os.environ, {"SPECSEED_HOME": str(home)})

    def test_add_local_registers_and_configures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "repo"
            target.mkdir()
            with self._env(home):
                rc = specseed.main(["add", "--target", str(target), "--provider", "local"])
                self.assertEqual(rc, 0)
                record = specseed.registry.get_repo(str(target))
                self.assertIsNotNone(record)
                self.assertEqual(record["provider"], "local")
                storage = specseed.registry.data_root_for(target) / "config"
                self.assertTrue((storage / "configuration.json").is_file())
                remote = json.loads((storage / "remote.json").read_text(encoding="utf-8"))
                self.assertFalse(remote["enabled"])
                self.assertFalse((target / ".specseed").exists())

    def test_add_github_requires_repo_and_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "repo"
            target.mkdir()
            with self._env(home):
                rc = specseed.main(["add", "--target", str(target), "--provider", "github"])
            self.assertEqual(rc, 2)

    def test_start_spawns_runner_for_configured_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "repo"
            target.mkdir()
            with self._env(home):
                self.assertEqual(
                    specseed.main(["add", "--target", str(target), "--provider", "local"]), 0
                )
                with mock.patch.object(
                    specseed.runner_control, "start_runner", return_value={"alive": True, "pid": 4242}
                ) as spawn:
                    rc = specseed.main(["start", "--target", str(target)])
            self.assertEqual(rc, 0)
            self.assertEqual(spawn.call_args.args[0]["provider"], "local")

    def test_pause_writes_control_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "repo"
            target.mkdir()
            with self._env(home):
                self.assertEqual(
                    specseed.main(["add", "--target", str(target), "--provider", "local"]), 0
                )
                self.assertEqual(specseed.main(["pause", "--target", str(target)]), 0)
                storage = specseed.registry.data_root_for(target)
                self.assertEqual(
                    specseed.runner_control.read_desired(storage), specseed.runner_control.PAUSED
                )

    def test_list_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "repo"
            target.mkdir()
            with self._env(home):
                self.assertEqual(
                    specseed.main(["add", "--target", str(target), "--provider", "local"]), 0
                )
                self.assertEqual(specseed.main(["list"]), 0)


if __name__ == "__main__":
    unittest.main()
