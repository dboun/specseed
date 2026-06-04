"""configure.py path-selection behavior."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.target_facing.specseed_target_src.configuring import configure


def _local_answers(*, specseed="seedmeta", dev_branch="", write=""):
    """Answer sequence for the local-only interactive flow (all defaults)."""
    answers = [
        specseed,    # specseed dir
        "",          # append to .gitignore
        "",          # local only (yes)
    ]
    # runner: per function, primary spec = provider/model/effort/data_dir (4) +
    # "add a fallback?" (defaults no) = 5 prompts, all accept-default.
    answers += [""] * (5 * len(configure.RUNNER_FUNCTIONS))
    answers += [
        dev_branch,  # dev branch
        "",          # local git on
        "",          # merge_to_dev_branch off
        "",          # auto_implement_issue (yes)
        "",          # auto_proceed_to_next_sprint (no)
    ]
    answers += [""] * len(configure.AGENT_CATEGORIES)  # agent gate levels (defaults)
    answers += [
        "",          # no approvers
        "",          # default interval
        write,       # write config
    ]
    return iter(answers)


class ConfigureSpecseedDirTest(unittest.TestCase):
    def test_custom_specseed_dir_is_saved_and_gitignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage_hint = root / "bootstrap" / "storage"
            answers = _local_answers()

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(configure, "_input", side_effect=lambda _prompt: next(answers)):
                    rc = configure.run_interactive(storage_hint, explicit_storage=False)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(rc, 0)
            config_path = root / "seedmeta" / "storage" / "configuration.json"
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(cfg["specseed_dir"], "seedmeta")
            self.assertIn("seedmeta/", (root / ".gitignore").read_text(encoding="utf-8"))

    def test_abort_does_not_gitignore_specseed_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            answers = _local_answers(write="n")

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(configure, "_input", side_effect=lambda _prompt: next(answers)):
                    rc = configure.run_interactive(root / "bootstrap" / "storage", explicit_storage=False)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(rc, 1)
            self.assertFalse((root / ".gitignore").exists())
            self.assertFalse((root / "seedmeta" / "storage" / "configuration.json").exists())


class ConfigureDevBranchTest(unittest.TestCase):
    def test_dev_branch_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage_hint = root / "bootstrap" / "storage"
            answers = _local_answers(dev_branch="develop")

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(configure, "_input", side_effect=lambda _prompt: next(answers)):
                    rc = configure.run_interactive(storage_hint, explicit_storage=False)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(rc, 0)
            cfg = json.loads((root / "seedmeta" / "storage" / "configuration.json").read_text("utf-8"))
            self.assertEqual(cfg["dev_branch"], "develop")

    def test_default_config_has_dev_branch(self) -> None:
        self.assertEqual(configure.default_config()["dev_branch"], configure.DEFAULT_DEV_BRANCH)

    def test_detect_prefers_main_then_master(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # non-git dir -> default.
            self.assertEqual(configure.detect_default_branch(root), configure.DEFAULT_DEV_BRANCH)


class ConfigurePermissionsShapeTest(unittest.TestCase):
    def _run_local_flow(self, root):
        storage_hint = root / "bootstrap" / "storage"
        answers = _local_answers()
        old_cwd = Path.cwd()
        try:
            os.chdir(root)
            with mock.patch.object(configure, "_input", side_effect=lambda _prompt: next(answers)):
                rc = configure.run_interactive(storage_hint, explicit_storage=False)
        finally:
            os.chdir(old_cwd)
        self.assertEqual(rc, 0)
        return root / "seedmeta" / "storage"

    def test_new_shape_written_and_remote_json_always_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = self._run_local_flow(Path(tmp))

            cfg = json.loads((storage / "configuration.json").read_text("utf-8"))
            self.assertNotIn("backend", cfg)
            perms = cfg["permissions"]
            self.assertEqual(set(perms), {"git", "remote", "platform", "agents"})
            self.assertEqual(perms["git"], {"enabled": True, "merge_to_dev_branch": False})
            self.assertEqual(perms["remote"], {
                "post_control": False, "push_branches": False,
                "push_dev_branch": False, "make_prs": False,
            })
            self.assertEqual(perms["platform"], {
                "auto_implement_issue": True,
                "auto_proceed_to_next_sprint_if_available": False,
            })
            self.assertEqual(perms["agents"], configure.default_agent_gates())
            self.assertNotIn("require_human_approval", cfg["review"])

            # remote.json now ALWAYS written; holds the local-vs-remote choice.
            remote = json.loads((storage / "remote.json").read_text("utf-8"))
            self.assertFalse(remote["enabled"])
            self.assertIsNone(remote["provider"])

    def test_load_config_drops_legacy_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            storage.mkdir(parents=True)
            legacy = {
                "version": 1,
                "backend": {"enabled": True, "provider": "github"},
                "review": {"enabled": True, "require_human_approval": True},
                "permissions": {"git": {"enabled": False}},
            }
            (storage / "configuration.json").write_text(json.dumps(legacy), "utf-8")

            cfg = configure.load_config(storage)
            self.assertNotIn("backend", cfg)
            self.assertNotIn("require_human_approval", cfg["review"])
            self.assertTrue(cfg["review"]["enabled"])               # kept
            self.assertFalse(cfg["permissions"]["git"]["enabled"])  # merged
            # new blocks materialize with defaults
            self.assertIn("platform", cfg["permissions"])
            self.assertEqual(cfg["permissions"]["agents"], configure.default_agent_gates())


class ConfigureRunnerChainsTest(unittest.TestCase):
    def test_default_config_runner_is_per_function_chains(self) -> None:
        runner = configure.default_config()["runner"]
        self.assertEqual(set(runner), set(configure.RUNNER_FUNCTIONS))
        for fn in configure.RUNNER_FUNCTIONS:
            self.assertEqual(len(runner[fn]), 1)
            spec = runner[fn][0]
            self.assertEqual(spec["provider"], "claude")
            self.assertEqual(spec["provider_data_dir"], "~/.claude")
            self.assertEqual(spec["model"], "opus")
            self.assertEqual(spec["effort"], "high")

    def test_coerce_legacy_flat_runner_migrates_to_chains(self) -> None:
        coerced = configure._coerce_runner({"provider": "codex", "model": "gpt-5.4", "effort": "low"})
        self.assertEqual(set(coerced), set(configure.RUNNER_FUNCTIONS))
        spec = coerced["implementation"][0]
        self.assertEqual(spec["provider"], "codex")
        self.assertEqual(spec["provider_data_dir"], "~/.codex")
        self.assertEqual(spec["model"], "gpt-5.4")
        self.assertEqual(spec["effort"], "low")

    def test_coerce_new_shape_keeps_known_functions_only(self) -> None:
        existing = {
            "implementation": [{"provider": "claude", "model": "opus"}],
            "bogus": [{"provider": "claude"}],
        }
        coerced = configure._coerce_runner(existing)
        self.assertIn("implementation", coerced)
        self.assertNotIn("bogus", coerced)

    def test_load_config_migrates_legacy_runner_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            storage.mkdir(parents=True)
            legacy = {"version": 1, "runner": {"provider": "claude", "model": "sonnet", "effort": "medium"}}
            (storage / "configuration.json").write_text(json.dumps(legacy), "utf-8")
            cfg = configure.load_config(storage)
            self.assertEqual(set(cfg["runner"]), set(configure.RUNNER_FUNCTIONS))
            self.assertEqual(cfg["runner"]["implementation"][0]["model"], "sonnet")
            # functions not in legacy fall back to the default claude spec
            self.assertEqual(cfg["runner"]["review"][0]["model"], "sonnet")


if __name__ == "__main__":
    unittest.main()
