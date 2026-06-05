"""test_agent_runner.py - per-function runner chains + provider_data_dir env wiring.

No subprocess, no tokens: FakeAgentRunner drives the chain logic; the CLI runners
are only inspected for the env they would set.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from specseed_runtime.executing.agent_runner import (
    AgentResult,
    ClaudeAgentRunner,
    CodexAgentRunner,
    FakeAgentRunner,
    RUNNER_FUNCTIONS,
    RunnerChains,
    _config_dir_env,
    build_runner,
    build_runner_chains,
    default_runner_chains,
    runner_from_spec,
)


class ConfigDirEnvTest(unittest.TestCase):
    def test_claude_maps_to_claude_config_dir_expanded(self) -> None:
        env = _config_dir_env("claude", "~/.claude")
        self.assertEqual(env, {"CLAUDE_CONFIG_DIR": str(Path("~/.claude").expanduser())})

    def test_codex_maps_to_codex_home(self) -> None:
        env = _config_dir_env("codex", "/tmp/codexhome")
        self.assertEqual(env, {"CODEX_HOME": "/tmp/codexhome"})

    def test_blank_dir_is_no_override(self) -> None:
        self.assertEqual(_config_dir_env("claude", None), {})
        self.assertEqual(_config_dir_env("claude", ""), {})

    def test_unknown_provider_is_no_override(self) -> None:
        self.assertEqual(_config_dir_env("mystery", "/x"), {})


class RunnerFromSpecTest(unittest.TestCase):
    def test_claude_spec_sets_model_and_config_dir_env(self) -> None:
        runner = runner_from_spec(
            {"provider": "claude", "model": "opus", "provider_data_dir": "~/.claude"}
        )
        self.assertIsInstance(runner, ClaudeAgentRunner)
        self.assertEqual(runner.model, "opus")
        self.assertEqual(
            runner.env_overrides, {"CLAUDE_CONFIG_DIR": str(Path("~/.claude").expanduser())}
        )

    def test_codex_spec_sets_effort_and_codex_home(self) -> None:
        runner = runner_from_spec(
            {"provider": "codex", "model": "gpt-5.4", "effort": "high", "provider_data_dir": "/d"}
        )
        self.assertIsInstance(runner, CodexAgentRunner)
        self.assertEqual(runner.effort, "high")
        self.assertEqual(runner.env_overrides, {"CODEX_HOME": "/d"})

    def test_codex_command_skips_git_repo_check(self) -> None:
        # A target need not be a git repo; without --skip-git-repo-check codex
        # exec refuses to start and exits 1 instantly. Pin the flag so it stays.
        argv = CodexAgentRunner().build_command("prompt", "/cwd")
        self.assertIn("--skip-git-repo-check", argv)
        self.assertEqual(argv[:2], ["codex", "exec"])
        self.assertEqual(argv[-1], "-")

    def test_no_data_dir_means_no_env_override(self) -> None:
        runner = runner_from_spec({"provider": "claude", "model": "opus"})
        self.assertEqual(runner.env_overrides, {})

    def test_unsupported_provider_raises(self) -> None:
        with self.assertRaises(ValueError):
            runner_from_spec({"provider": "nope"})

    def test_child_env_merges_parent_environ(self) -> None:
        runner = ClaudeAgentRunner(config_dir="/conf")
        env = runner._child_env()
        self.assertIsNotNone(env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/conf")
        # parent env is preserved alongside the override
        some_key = next(iter(os.environ), None)
        if some_key is not None:
            self.assertIn(some_key, env)

    def test_child_env_always_puts_engine_on_pythonpath(self) -> None:
        # Even with no provider overrides, the agent must be able to import the
        # engine (not in the target) - so src/ is always on PYTHONPATH.
        env = ClaudeAgentRunner()._child_env()
        self.assertIsNotNone(env)
        engine_src = str(Path(__file__).resolve().parents[3] / "src")
        self.assertIn(engine_src, env["PYTHONPATH"].split(os.pathsep))


class BuildRunnerChainsTest(unittest.TestCase):
    def test_default_is_one_claude_spec_per_function(self) -> None:
        chains = build_runner_chains({})
        self.assertIsInstance(chains, RunnerChains)
        for fn in RUNNER_FUNCTIONS:
            chain = chains.chain_for(fn)
            self.assertEqual(len(chain), 1)
            self.assertIsInstance(chain[0], ClaudeAgentRunner)

    def test_config_chain_with_fallback_is_built_in_order(self) -> None:
        cfg = {
            "runner": {
                "implementation": [
                    {"provider": "claude", "model": "opus", "provider_data_dir": "~/.claude"},
                    {"provider": "codex", "model": "gpt-5.4", "effort": "high", "provider_data_dir": "/d"},
                ]
            }
        }
        chains = build_runner_chains(cfg)
        impl = chains.chain_for("implementation")
        self.assertEqual(len(impl), 2)
        self.assertIsInstance(impl[0], ClaudeAgentRunner)
        self.assertIsInstance(impl[1], CodexAgentRunner)
        # absent functions fall back to the default single claude spec
        self.assertEqual(len(chains.chain_for("review")), 1)

    def test_default_runner_chains_shape(self) -> None:
        chains = default_runner_chains()
        self.assertEqual(set(chains), set(RUNNER_FUNCTIONS))
        for fn in RUNNER_FUNCTIONS:
            self.assertEqual(len(chains[fn]), 1)
            self.assertEqual(chains[fn][0]["provider"], "claude")

    def test_build_runner_backcompat_returns_primary_impl(self) -> None:
        runner = build_runner({})
        self.assertIsInstance(runner, ClaudeAgentRunner)


class RunnerChainsRunTest(unittest.TestCase):
    def test_primary_success_skips_fallback(self) -> None:
        primary = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        fallback = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        chains = RunnerChains({"implementation": [primary, fallback]})
        result = chains.run("p", function="implementation", cwd="/tmp")
        self.assertTrue(result.ok)
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 0)

    def test_failure_falls_through_to_next(self) -> None:
        primary = FakeAgentRunner(result=AgentResult(ok=False, returncode=1, error="boom"))
        fallback = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        chains = RunnerChains({"implementation": [primary, fallback]})
        result = chains.run("p", function="implementation", cwd="/tmp")
        self.assertTrue(result.ok)
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 1)

    def test_all_fail_returns_last_result(self) -> None:
        primary = FakeAgentRunner(result=AgentResult(ok=False, returncode=1))
        fallback = FakeAgentRunner(result=AgentResult(ok=False, returncode=2))
        chains = RunnerChains({"implementation": [primary, fallback]})
        result = chains.run("p", function="implementation", cwd="/tmp")
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 2)

    def test_killed_does_not_fall_through(self) -> None:
        primary = FakeAgentRunner(result=AgentResult(ok=False, killed=True))
        fallback = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        chains = RunnerChains({"implementation": [primary, fallback]})
        result = chains.run("p", function="implementation", cwd="/tmp")
        self.assertTrue(result.killed)
        self.assertEqual(len(fallback.calls), 0)

    def test_unknown_function_falls_back_to_implementation_chain(self) -> None:
        impl = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        chains = RunnerChains({"implementation": [impl]})
        result = chains.run("p", function="merge_conflicts", cwd="/tmp")
        self.assertTrue(result.ok)
        self.assertEqual(len(impl.calls), 1)

    def test_single_wraps_one_runner_for_every_function(self) -> None:
        fake = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        chains = RunnerChains.single(fake)
        for fn in RUNNER_FUNCTIONS:
            self.assertEqual(chains.chain_for(fn), [fake])


if __name__ == "__main__":
    unittest.main()
