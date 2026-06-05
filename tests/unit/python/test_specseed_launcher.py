"""src/specseed.py launcher behavior - runs the engine against a target, never copies code."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_launcher():
    """Load src/specseed.py as a module (it sits beside the package, not in it)."""
    path = Path(__file__).resolve().parents[3] / "src" / "specseed.py"
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
    def test_defaults(self) -> None:
        args = specseed.parse_args(["/some/repo"])
        self.assertEqual(args.target_repo, "/some/repo")
        self.assertEqual(args.specseed_dir, ".specseed")
        self.assertFalse(args.once)
        self.assertIsNone(args.interval)

    def test_positional_dir_and_flags(self) -> None:
        args = specseed.parse_args(["/some/repo", "seedmeta", "--once", "--interval", "30"])
        self.assertEqual(args.specseed_dir, "seedmeta")
        self.assertTrue(args.once)
        self.assertEqual(args.interval, 30.0)


class MainTest(unittest.TestCase):
    def test_missing_target_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            rc = specseed.main([str(missing)])
            self.assertEqual(rc, 1)

    def test_delegates_to_run_with_storage_and_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            with mock.patch.object(specseed.run_mod, "main", return_value=0) as run_main:
                rc = specseed.main([str(target), ".specseed", "--once", "--interval", "15"])

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


if __name__ == "__main__":
    unittest.main()
