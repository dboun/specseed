"""configure.py path-selection behavior."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specseed_runtime.configuring import configure


def _config_file(storage):
    return configure.config_file(storage)


def _write_cfg(storage, data):
    cf = _config_file(storage)
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(data), "utf-8")


def _local_answers(*, primary_branch="", write=""):
    """Answer sequence for the local-only interactive flow (all defaults).

    0.21: the specseed-dir prompt is gone (data lives in the home data root), so the
    sequence starts at the local-only question.
    """
    answers = [
        "",          # local only (yes)
    ]
    # runner: per function, primary spec = provider/model/effort/data_dir (4) +
    # "add a fallback?" (defaults no) = 5 prompts, all accept-default.
    answers += [""] * (5 * len(configure.RUNNER_FUNCTIONS))
    answers += [
        primary_branch,  # primary branch
        "",          # merge_to_primary off (git is mandatory: no enable prompt)
        "",          # auto_assign_agent (yes)
        "",          # auto_implement_issue (yes)
        "",          # auto_proceed_to_next_sprint (no)
    ]
    answers += [""] * len(configure.AGENT_CATEGORIES)  # agent gate levels (defaults)
    answers += [
        "",          # no approvers
        "",          # platform username (blank = prefix detection only)
        "",          # default interval
        write,       # write config
    ]
    return iter(answers)


class ConfigureCleanTargetTest(unittest.TestCase):
    """0.21: config lands in the data root's config/ subdir; the target stays clean."""

    def test_interactive_writes_config_into_data_root_no_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = root / "home" / "repos" / "x"  # the data root passed in
            answers = _local_answers()
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(configure, "_input", side_effect=lambda _prompt: next(answers)):
                    rc = configure.run_interactive(storage, explicit_storage=True)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(rc, 0)
            cfg = json.loads(_config_file(storage).read_text(encoding="utf-8"))
            self.assertEqual(cfg["specseed_dir"], configure.DEFAULT_SPECSEED_DIR)
            self.assertFalse((root / ".gitignore").exists())

    def test_defaults_path_writes_config_no_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = root / "home" / "repos" / "x"
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                rc = configure.run_defaults(storage)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(rc, 0)
            cfg = json.loads(_config_file(storage).read_text(encoding="utf-8"))
            self.assertNotIn("gitignore_specseed_dir", cfg)
            self.assertFalse((root / ".gitignore").exists())

    def test_abort_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = root / "home" / "repos" / "x"
            answers = _local_answers(write="n")
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(configure, "_input", side_effect=lambda _prompt: next(answers)):
                    rc = configure.run_interactive(storage, explicit_storage=True)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(rc, 1)
            self.assertFalse((root / ".gitignore").exists())
            self.assertFalse(_config_file(storage).exists())


class ConfigureDevBranchTest(unittest.TestCase):
    def test_primary_branch_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage_hint = root / "bootstrap" / "storage"
            answers = _local_answers(primary_branch="develop")

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(configure, "_input", side_effect=lambda _prompt: next(answers)):
                    rc = configure.run_interactive(storage_hint, explicit_storage=False)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(rc, 0)
            cfg = json.loads(_config_file(storage_hint).read_text("utf-8"))
            self.assertEqual(cfg["specseed_primary_branch"], "develop")

    def test_default_config_has_primary_branch(self) -> None:
        self.assertEqual(
            configure.default_config()["specseed_primary_branch"],
            configure.DEFAULT_PRIMARY_BRANCH,
        )

    def test_detect_prefers_main_then_master(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # non-git dir -> default.
            self.assertEqual(configure.detect_default_branch(root), configure.DEFAULT_PRIMARY_BRANCH)


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
        return storage_hint

    def test_new_shape_written_and_remote_json_always_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = self._run_local_flow(Path(tmp))

            cfg = json.loads(_config_file(storage).read_text("utf-8"))
            self.assertNotIn("backend", cfg)
            perms = cfg["permissions"]
            self.assertEqual(set(perms), {"git", "remote", "platform", "agents"})
            self.assertEqual(perms["git"], {"merge_to_primary": False})
            self.assertEqual(perms["remote"], {
                "post_control": False, "push_branches": False,
                "push_primary": False,
            })
            self.assertEqual(perms["platform"], {
                "auto_assign_agent": True,
                "auto_implement_issue": True,
                "auto_proceed_to_next_sprint_if_available": False,
            })
            # local-only default is the fast poll interval
            self.assertEqual(cfg["poll_interval_seconds"], configure.DEFAULT_POLL_INTERVAL_LOCAL)
            self.assertEqual(perms["agents"], configure.default_agent_gates())
            self.assertNotIn("require_human_approval", cfg["review"])

            # remote.json now ALWAYS written; holds the local-vs-remote choice.
            remote = json.loads(configure.remote_file(storage).read_text("utf-8"))
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
            _write_cfg(storage, legacy)

            cfg = configure.load_config(storage)
            self.assertNotIn("backend", cfg)
            self.assertNotIn("require_human_approval", cfg["review"])
            self.assertTrue(cfg["review"]["enabled"])               # kept
            self.assertNotIn("enabled", cfg["permissions"]["git"])  # legacy git switch dropped
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

    def test_default_config_has_no_version_key(self) -> None:
        self.assertNotIn("version", configure.default_config())

    def test_load_config_drops_legacy_version_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            storage.mkdir(parents=True)
            _write_cfg(storage, {"version": 1, "dev_branch": "custom"})
            cfg = configure.load_config(storage)
            self.assertNotIn("version", cfg)
            # legacy dev_branch translated to specseed_primary_branch
            self.assertNotIn("dev_branch", cfg)
            self.assertEqual(cfg["specseed_primary_branch"], "custom")

    def test_main_migrates_storage_before_show(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            storage.mkdir(parents=True)
            # pre-0.21 storage is FLAT; the migration chain (incl. the 0.21 reshape)
            # renames dev_branch and relocates the file into config/.
            (storage / "configuration.json").write_text(
                json.dumps({"version": 1, "dev_branch": "custom"}), "utf-8"
            )

            with mock.patch("sys.stdout", new=io.StringIO()):
                rc = configure.main(["--storage", str(storage), "--show"])

            self.assertEqual(rc, 0)
            on_disk = json.loads(_config_file(storage).read_text("utf-8"))
            self.assertNotIn("version", on_disk)
            # the 0.12.0 hop renamed dev_branch on disk
            self.assertNotIn("dev_branch", on_disk)
            self.assertEqual(on_disk["specseed_primary_branch"], "custom")
            # marker fast-forwarded to the running code's version
            marker = (storage / "config" / "version.txt").read_text("utf-8").strip()
            self.assertRegex(marker, r"^\d+\.\d+\.\d+$")

    def test_load_config_migrates_legacy_runner_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            storage.mkdir(parents=True)
            legacy = {"version": 1, "runner": {"provider": "claude", "model": "sonnet", "effort": "medium"}}
            _write_cfg(storage, legacy)
            cfg = configure.load_config(storage)
            self.assertEqual(set(cfg["runner"]), set(configure.RUNNER_FUNCTIONS))
            self.assertEqual(cfg["runner"]["implementation"][0]["model"], "sonnet")
            # functions not in legacy fall back to the default claude spec
            self.assertEqual(cfg["runner"]["review"][0]["model"], "sonnet")


class RunnerVocabularySingleSourceTest(unittest.TestCase):
    """configure re-exports agent_runner's vocabulary - a mirrored copy once
    drifted and hid resolve_platform_errors from configure + the UI schema."""

    def test_runner_functions_are_agent_runners(self) -> None:
        from specseed_runtime.executing import agent_runner

        self.assertIs(configure.RUNNER_FUNCTIONS, agent_runner.RUNNER_FUNCTIONS)
        self.assertIn("resolve_platform_errors", configure.RUNNER_FUNCTIONS)

    def test_default_chains_cover_every_function(self) -> None:
        self.assertEqual(
            set(configure.default_runner_chains()), set(configure.RUNNER_FUNCTIONS)
        )


class IdentityDefaultsTest(unittest.TestCase):
    def test_local_defaults_user_and_specseed(self) -> None:
        cfg = configure.default_config()
        configure.apply_identity_defaults(cfg, {"enabled": False})
        self.assertEqual(cfg["approvals"]["approver_usernames"], ["user"])
        self.assertEqual(cfg["platform_username"], "specseed")

    def test_remote_infers_owner_for_both(self) -> None:
        cfg = configure.default_config()
        configure.apply_identity_defaults(
            cfg, {"enabled": True, "provider": "github", "repo": "https://github.com/dboun/x"}
        )
        self.assertEqual(cfg["approvals"]["approver_usernames"], ["dboun"])
        self.assertEqual(cfg["platform_username"], "dboun")

    def test_remote_uninferrable_stays_blank(self) -> None:
        cfg = configure.default_config()
        configure.apply_identity_defaults(cfg, {"enabled": True, "provider": "github", "repo": ""})
        self.assertEqual(cfg["approvals"]["approver_usernames"], [])
        self.assertEqual(cfg["platform_username"], "")

    def test_never_clobbers_existing(self) -> None:
        cfg = configure.default_config()
        cfg["approvals"]["approver_usernames"] = ["alice"]
        cfg["platform_username"] = "bot"
        configure.apply_identity_defaults(cfg, {"enabled": False})
        self.assertEqual(cfg["approvals"]["approver_usernames"], ["alice"])
        self.assertEqual(cfg["platform_username"], "bot")


class AskModelTest(unittest.TestCase):
    """The model picker: presets + 'custom' -> free text."""

    def _ask(self, provider, current, answers):
        ans = iter(answers)
        with mock.patch.object(configure, "_input", side_effect=lambda _p: next(ans)):
            return configure._ask_model(provider, current)

    def test_claude_blank_enter_defaults_to_opus(self) -> None:
        with mock.patch.object(configure, "model_presets", return_value=["opus", "sonnet", "haiku"]):
            self.assertEqual(self._ask("claude", None, [""]), "opus")

    def test_claude_pick_preset(self) -> None:
        with mock.patch.object(configure, "model_presets", return_value=["opus", "sonnet", "haiku"]):
            self.assertEqual(self._ask("claude", None, ["sonnet"]), "sonnet")

    def test_claude_custom_then_free_text(self) -> None:
        with mock.patch.object(configure, "model_presets", return_value=["opus", "sonnet", "haiku"]):
            self.assertEqual(self._ask("claude", None, ["custom", "claude-opus-4-8"]), "claude-opus-4-8")

    def test_current_outside_presets_defaults_to_custom_prefilled(self) -> None:
        with mock.patch.object(configure, "model_presets", return_value=["opus", "sonnet", "haiku"]):
            # blank enter at the choice -> default 'custom'; blank enter at the
            # free-text -> the pre-filled current value.
            self.assertEqual(self._ask("claude", "weird-model", ["", ""]), "weird-model")

    def test_codex_default_is_first_slug(self) -> None:
        with mock.patch.object(configure, "model_presets", return_value=["gpt-5.4", "gpt-5.4-mini"]):
            with mock.patch.object(configure, "default_model", return_value="gpt-5.4"):
                self.assertEqual(self._ask("codex", None, [""]), "gpt-5.4")

    def test_no_presets_falls_back_to_free_text(self) -> None:
        with mock.patch.object(configure, "model_presets", return_value=[]):
            with mock.patch.object(configure, "default_model", return_value=""):
                self.assertEqual(self._ask("codex", None, ["my-slug"]), "my-slug")


class PollIntervalDefaultTest(unittest.TestCase):
    """Local polls fast (snappy UI); enabling a real remote slows it to stay clear
    of rate limits - but only the untouched default is bumped."""

    def test_fresh_config_is_local_default(self) -> None:
        cfg = configure.default_config()
        self.assertEqual(cfg["poll_interval_seconds"], configure.DEFAULT_POLL_INTERVAL_LOCAL)

    def test_local_stays_fast(self) -> None:
        cfg = configure.default_config()
        configure.apply_identity_defaults(cfg, {"enabled": False})
        self.assertEqual(cfg["poll_interval_seconds"], configure.DEFAULT_POLL_INTERVAL_LOCAL)

    def test_enabling_remote_bumps_untouched_default(self) -> None:
        cfg = configure.default_config()
        configure.apply_identity_defaults(cfg, {"enabled": True, "repo": "dboun/x"})
        self.assertEqual(cfg["poll_interval_seconds"], configure.DEFAULT_POLL_INTERVAL_REMOTE)

    def test_remote_leaves_user_chosen_interval_alone(self) -> None:
        cfg = configure.default_config()
        cfg["poll_interval_seconds"] = 12  # the human picked this
        configure.apply_identity_defaults(cfg, {"enabled": True, "repo": "dboun/x"})
        self.assertEqual(cfg["poll_interval_seconds"], 12)


if __name__ == "__main__":
    unittest.main()
