"""specseed command router behavior - targets storage, never copies code."""

from __future__ import annotations

import importlib.util
import json
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


class ResolvePathsTest(unittest.TestCase):
    def test_relative_specseed_dir_joins_under_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            got_target, storage = specseed.resolve_paths(str(target), ".specseed")
            self.assertEqual(got_target, target)
            self.assertEqual(storage, target / ".specseed" / "storage")

    def test_absolute_specseed_dir_respected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            target.mkdir()
            abs_dir = Path(tmp) / "elsewhere" / "seed"
            _t, storage = specseed.resolve_paths(str(target), str(abs_dir))
            self.assertEqual(storage, abs_dir.resolve() / "storage")


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
            target = Path(tmp).resolve()
            with mock.patch.object(specseed.run_mod, "main", return_value=0) as run_main:
                rc = specseed.main(
                    ["run", "--target", str(target), "--once", "--interval", "15"]
                )

            self.assertEqual(rc, 0)
            argv = run_main.call_args.args[0]
            storage = str(target / ".specseed" / "storage")
            self.assertIn("--storage", argv)
            self.assertEqual(argv[argv.index("--storage") + 1], storage)
            self.assertIn("--repo-root", argv)
            self.assertEqual(argv[argv.index("--repo-root") + 1], str(target))
            self.assertIn("--once", argv)
            self.assertEqual(argv[argv.index("--interval") + 1], "15.0")
            # storage got created; no engine code landed in the target
            self.assertTrue((target / ".specseed" / "storage").is_dir())
            self.assertFalse((target / ".specseed" / "specseed_runtime").exists())
            self.assertFalse((target / ".specseed" / "skills").exists())

    def test_configure_defaults_writes_config_and_remote_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            rc = specseed.main(["configure", "--target", str(target), "--defaults"])

            self.assertEqual(rc, 0)
            storage = target / ".specseed" / "storage"
            self.assertTrue((storage / "configuration.json").is_file())
            self.assertTrue((storage / "remote.json").is_file())
            self.assertFalse((target / ".specseed" / "specseed_runtime").exists())

    def test_configure_defaults_overwrite_resets_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            self.assertEqual(
                specseed.main(["configure", "--target", str(target), "--defaults"]),
                0,
            )
            storage = target / ".specseed" / "storage"
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

    def test_configure_defaults_allows_absolute_specseed_dir_outside_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            target.mkdir()
            specseed_dir = Path(tmp) / "state"
            rc = specseed.main(
                [
                    "configure",
                    "--target",
                    str(target),
                    "--specseed-dir",
                    str(specseed_dir),
                    "--defaults",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertTrue((specseed_dir / "storage" / "configuration.json").is_file())
            self.assertFalse((target / ".gitignore").exists())

    def test_configure_uses_config_and_remote_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            target.mkdir()
            config_path = Path(tmp) / "configuration.json"
            remote_path = Path(tmp) / "remote.json"
            config_path.write_text(
                json.dumps({"poll_interval_seconds": 12, "dev_branch": "develop"}) + "\n",
                encoding="utf-8",
            )
            remote_path.write_text(
                json.dumps({"enabled": False, "repo": "local/example"}) + "\n",
                encoding="utf-8",
            )

            rc = specseed.main(
                [
                    "configure",
                    "--target",
                    str(target),
                    "--use-config-file",
                    str(config_path),
                    "--use-remote-file",
                    str(remote_path),
                    "--no-gitignore",
                ]
            )

            self.assertEqual(rc, 0)
            storage = target / ".specseed" / "storage"
            cfg = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
            remote = json.loads((storage / "remote.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["poll_interval_seconds"], 12)
            self.assertEqual(cfg["dev_branch"], "develop")
            self.assertEqual(remote["repo"], "local/example")
            self.assertFalse((target / ".gitignore").exists())

    def test_remote_local_requires_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            with mock.patch.object(specseed, "_run_remote_local_ui") as ui_main:
                rc = specseed.main(["remote_local", "--target", str(target), "--ui"])

            self.assertEqual(rc, 1)
            ui_main.assert_not_called()

    def test_remote_local_ui_uses_target_storage_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            self.assertEqual(specseed.main(["configure", "--target", str(target), "--defaults"]), 0)

            with mock.patch.object(specseed, "_run_remote_local_ui") as ui_main:
                rc = specseed.main(["remote_local", "--target", str(target), "--ui"])

            self.assertEqual(rc, 0)
            argv = ui_main.call_args.args[0]
            self.assertEqual(
                argv[argv.index("--db") + 1],
                str(target / ".specseed" / "storage" / "tracking_remote_local.db"),
            )


if __name__ == "__main__":
    unittest.main()
