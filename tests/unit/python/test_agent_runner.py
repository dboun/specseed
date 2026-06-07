"""test_agent_runner.py - per-function runner chains + provider_data_dir env wiring.

No agent, no tokens: FakeAgentRunner drives the chain logic; the CLI runners are
only inspected for the env they would set. The failure-tail tests run a plain
``python -c`` child, never a real agent.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from specseed_runtime.executing import platform_log
from specseed_runtime.executing.agent_runner import (
    AgentResult,
    CLAUDE_MODELS,
    ClaudeAgentRunner,
    CodexAgentRunner,
    FakeAgentRunner,
    RUNNER_FUNCTIONS,
    RunnerChains,
    STDOUT_TAIL_CHARS,
    SubprocessAgentRunner,
    _config_dir_env,
    build_runner,
    build_runner_chains,
    default_model,
    default_runner_chains,
    list_codex_model_slugs,
    model_presets,
    runner_from_spec,
    stdout_tail,
)


class ModelPresetsTest(unittest.TestCase):
    def _cache(self, models) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / "models_cache.json"
        p.write_text(json.dumps({"models": models}), encoding="utf-8")
        return p

    def test_claude_presets_are_the_tier_tags(self) -> None:
        self.assertEqual(model_presets("claude"), list(CLAUDE_MODELS))
        self.assertEqual(model_presets("CLAUDE"), list(CLAUDE_MODELS))

    def test_claude_default_is_opus(self) -> None:
        self.assertEqual(default_model("claude"), "opus")

    def test_codex_slugs_read_listed_models_in_order_deduped(self) -> None:
        cache = self._cache([
            {"slug": "gpt-5.4", "visibility": "list"},
            {"slug": "gpt-5.4-mini", "visibility": "list"},
            {"slug": "gpt-5.4", "visibility": "list"},  # dup dropped
            {"slug": "hidden", "visibility": "hidden"},  # not listed
            {"slug": "", "visibility": "list"},  # blank skipped
        ])
        self.assertEqual(list_codex_model_slugs(cache), ["gpt-5.4", "gpt-5.4-mini"])

    def test_codex_missing_cache_is_empty(self) -> None:
        self.assertEqual(list_codex_model_slugs(Path("/no/such/file.json")), [])

    def test_codex_bad_json_is_empty(self) -> None:
        d = Path(tempfile.mkdtemp())
        p = d / "models_cache.json"
        p.write_text("not json", encoding="utf-8")
        self.assertEqual(list_codex_model_slugs(p), [])

    def test_codex_default_is_first_slug_or_blank(self) -> None:
        # No machine cache assumption: default_model reads the real cache, so we
        # only assert the documented contract via list_codex_model_slugs shape.
        slugs = list_codex_model_slugs(self._cache([{"slug": "a", "visibility": "list"}]))
        self.assertEqual(slugs[0], "a")
        self.assertEqual(list_codex_model_slugs(self._cache([])), [])


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
        # absent functions ride the configured implementation chain
        review = chains.chain_for("review")
        self.assertEqual(len(review), 2)
        self.assertIsInstance(review[0], ClaudeAgentRunner)
        self.assertIsInstance(review[1], CodexAgentRunner)

    def test_unconfigured_function_rides_the_implementation_chain(self) -> None:
        # A target configured for codex must not get a surprise default-claude
        # run when the engine grows a new function.
        config = {"runner": {"implementation": [{"provider": "codex", "model": "m"}]}}
        chains = build_runner_chains(config)
        resolver = chains.chain_for("resolve_platform_errors")[0]
        self.assertIsInstance(resolver, CodexAgentRunner)

    def test_no_chains_at_all_falls_back_to_default_claude(self) -> None:
        resolver = build_runner_chains({}).chain_for("resolve_platform_errors")[0]
        self.assertIsInstance(resolver, ClaudeAgentRunner)

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


class StdoutTailTest(unittest.TestCase):
    def test_short_output_passes_through_stripped(self) -> None:
        self.assertEqual(stdout_tail("  boom \n"), "boom")

    def test_empty_and_none_are_empty(self) -> None:
        self.assertEqual(stdout_tail(""), "")
        self.assertEqual(stdout_tail(None), "")

    def test_long_output_keeps_tail_and_marks_truncation(self) -> None:
        text = "x" * (STDOUT_TAIL_CHARS * 2) + "the actual error"
        tail = stdout_tail(text)
        self.assertTrue(tail.startswith("...[truncated]"))
        self.assertTrue(tail.endswith("the actual error"))
        self.assertEqual(len(tail), len("...[truncated]") + STDOUT_TAIL_CHARS)


class _EchoRunner(SubprocessAgentRunner):
    """Plain ``python -c`` child: echo a line, exit with ``code``. NOT an agent."""

    def __init__(self, code: int) -> None:
        super().__init__(poll_interval=0.05)
        self.code = code

    def build_command(self, prompt: str, cwd) -> list[str]:
        return [
            sys.executable, "-c",
            "import sys; sys.stdin.read(); print('boom output'); sys.exit({0})".format(self.code),
        ]


class _ReportRunner(SubprocessAgentRunner):
    """``python -c`` child that writes the JSON result file then exits 0."""

    def __init__(self, payload_json: str) -> None:
        super().__init__(poll_interval=0.05)
        self.payload_json = payload_json

    def build_command(self, prompt: str, cwd) -> list[str]:
        code = (
            "import os,sys;sys.stdin.read();"
            "open(os.environ['SPECSEED_RESULT_FILE'],'w').write({0!r});"
            "print('done')".format(self.payload_json)
        )
        return [sys.executable, "-c", code]


class ResultFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_valid_report_is_parsed_for_intent(self) -> None:
        payload = json.dumps({"verdict": "approve", "confidence": 0.9, "summary": "ok"})
        result = _ReportRunner(payload).run("p", cwd=self._tmp.name, timeout_s=30, intent="review")
        self.assertTrue(result.ok)
        self.assertEqual(result.report["verdict"], "approve")
        self.assertIsNone(result.report_error)

    def test_missing_report_records_error(self) -> None:
        # writes nothing to the result file -> empty file -> report_error set.
        result = _EchoRunner(0).run("p", cwd=self._tmp.name, timeout_s=30, intent="implement")
        self.assertTrue(result.ok)  # rc==0; the gate lives in dispatch, not here
        self.assertIsNone(result.report)
        self.assertIsNotNone(result.report_error)

    def test_no_intent_skips_parsing(self) -> None:
        result = _EchoRunner(0).run("p", cwd=self._tmp.name, timeout_s=30)
        self.assertIsNone(result.report)
        self.assertIsNone(result.report_error)


class ChainQuotaTest(unittest.TestCase):
    def test_all_quota_failures_mark_exhausted(self) -> None:
        q = AgentResult(ok=False, returncode=1, error="You've hit your usage limit. try again in 5 minutes")
        c1 = FakeAgentRunner(result=q)
        c2 = FakeAgentRunner(result=q)
        chains = RunnerChains({"implementation": [c1, c2]})
        result = chains.run("p", function="implementation", cwd="/tmp", intent="implement")
        self.assertFalse(result.ok)
        self.assertTrue(result.quota_exhausted)

    def test_one_nonquota_failure_is_not_quota(self) -> None:
        q = AgentResult(ok=False, returncode=1, error="usage limit")
        plain = AgentResult(ok=False, returncode=1, error="boom")
        chains = RunnerChains({"implementation": [FakeAgentRunner(result=q), FakeAgentRunner(result=plain)]})
        result = chains.run("p", function="implementation", cwd="/tmp", intent="implement")
        self.assertFalse(getattr(result, "quota_exhausted", False))

    def test_success_is_never_quota(self) -> None:
        chains = RunnerChains({"implementation": [FakeAgentRunner(result=AgentResult(ok=True, returncode=0))]})
        result = chains.run("p", function="implementation", cwd="/tmp", intent="implement")
        self.assertFalse(result.quota_exhausted)


class SubprocessFailureTailTest(unittest.TestCase):
    """Failed runs log a stdout tail in agent_subprocess_complete; ok runs don't."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        platform_log.configure(self._tmp.name)

    def _complete_event(self) -> dict:
        log = Path(self._tmp.name) / "platform.log"
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [e for e in events if e["event"] == "agent_subprocess_complete"][-1]

    def test_failure_logs_stdout_tail(self) -> None:
        result = _EchoRunner(1).run("p", cwd=self._tmp.name, timeout_s=30)
        self.assertFalse(result.ok)
        self.assertIn("boom output", result.stdout)
        self.assertIn("boom output", self._complete_event()["stdout_tail"])

    def test_success_logs_no_tail(self) -> None:
        result = _EchoRunner(0).run("p", cwd=self._tmp.name, timeout_s=30)
        self.assertTrue(result.ok)
        self.assertIsNone(self._complete_event()["stdout_tail"])


if __name__ == "__main__":
    unittest.main()
