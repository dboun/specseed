"""test_prompts.py - the runtime wires the skill bundle into every agent prompt.

Phase-1 wiring: each builder PREPENDS the assembled skill bundle
(generate_prompt_from_skill) and then adds only per-request execution facts; the skill
text is never re-stated runtime-side. The `spec-change:<sub>` label suffix maps to skill
route `spec` + that subroute. No agent, no tokens, no network.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from specseed_runtime.executing import prompts
from specseed_runtime.executing.permissions import Permissions


class _Ctx:
    def __init__(self, config=None, storage=None):
        self.config = config or {"specseed_dir": ".specseed"}
        self.permissions = Permissions(self.config)
        if storage is not None:
            self.storage = storage


class _Entity:
    post_id = "42"
    title = "Add the thing"


_BUNDLE_HEADER = "Specseed skill (v"
_BUNDLE_END = "END OF SPECSEED SKILL FILES"


class SkillBundleWiringTest(unittest.TestCase):
    def test_spec_change_prompt_prepends_spec_bundle_for_subroute(self):
        out = prompts.build_spec_change_prompt("adapt", "42", _Entity(), _Ctx())
        self.assertTrue(out.startswith(_BUNDLE_HEADER))
        self.assertIn("Route: spec. Subroute: adapt.", out)
        self.assertIn("===== routes/spec.md =====", out)
        self.assertIn("===== spec_subroutes/adapt.md =====", out)
        # per-request execution facts follow the bundle
        self.assertIn("spec-change request 42", out)
        self.assertLess(out.index(_BUNDLE_END), out.index("spec-change request 42"))
        # contract unchanged: still plan-first / staged / stop
        self.assertIn("write plan.json", out)
        # the resolved render mode is stated outright (local -> structured envelope), so
        # the worker never infers the form from an absent provider and drops to chat.
        self.assertIn("Reply render mode:", out)
        self.assertIn("STRUCTURED JSON envelope", out)
        # permission/action-gate policy is passed too
        self.assertIn("Action gates", out)

    def test_spec_change_prompt_states_external_form_for_remote_provider(self):
        import json, tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from specseed_runtime.storage_paths import remote_file
        storage = Path(tmp.name)
        rf = remote_file(storage)
        rf.parent.mkdir(parents=True, exist_ok=True)
        rf.write_text(
            json.dumps({"enabled": True, "provider": "github", "repo": "o/r"}),
            encoding="utf-8",
        )
        out = prompts.build_spec_change_prompt("adapt", "42", _Entity(), _Ctx(storage=str(storage)))
        self.assertIn("Reply render mode:", out)
        self.assertIn("NATURAL markdown prose", out)
        self.assertNotIn("STRUCTURED JSON envelope", out)

    def test_implement_prompt_prepends_impl_bundle_and_keeps_runtime_facts(self):
        out = prompts.build_implement_prompt(_Entity(), _Ctx())
        self.assertTrue(out.startswith(_BUNDLE_HEADER))
        self.assertIn("Route: impl.", out)
        self.assertIn("===== routes/impl.md =====", out)
        # git policy + action gates stay runtime-side (config-derived)
        self.assertIn("Git rules", out)
        self.assertIn("Action gates", out)

    def test_review_prompt_prepends_review_bundle(self):
        out = prompts.build_review_prompt(_Entity(), _Ctx())
        self.assertTrue(out.startswith(_BUNDLE_HEADER))
        self.assertIn("Route: review.", out)
        self.assertIn("===== routes/review.md =====", out)

    def test_ask_prompt_prepends_ask_bundle_and_is_read_only(self):
        out = prompts.build_ask_prompt(_Entity(), _Ctx())
        self.assertTrue(out.startswith(_BUNDLE_HEADER))
        self.assertIn("Route: ask.", out)
        self.assertIn("===== routes/ask.md =====", out)
        self.assertIn("READ-ONLY", out)
        self.assertIn("answer", out)  # the result-file schema

    def test_bundle_carries_instruction_file_reads(self):
        out = prompts.build_implement_prompt(_Entity(), _Ctx(storage="/tmp/dataroot"))
        self.assertIn("/tmp/dataroot/instructions/impl/repo.md", out)
        self.assertIn("/tmp/dataroot/instructions/impl/custom.md", out)
        self.assertIn("/tmp/dataroot/instructions/custom.md", out)

    def test_impl_grants_spec_not_tracker_and_injects_thread(self):
        ent = _Entity()
        ent.body = "do the thing"
        out = prompts.build_implement_prompt(ent, _Ctx(storage="/tmp/dataroot"), [])
        self.assertIn("/tmp/dataroot/spec", out)           # spec grant
        self.assertNotIn("/tmp/dataroot/tracker", out)     # impl gets NO tracker dir
        self.assertNotIn("/tmp/dataroot/config", out)      # never config/token
        self.assertIn("do the thing", out)                 # body injected
        self.assertIn("never query a tracker db", out)


class _Post:
    def __init__(self, body="stub\n\n<!-- specseed:platform-error task=9 -->"):
        self.id = "7"
        self.title = "Platform error: handle_label_removed"
        self.body = body
        self.comments = []


class MergeConflictAndPlatformErrorBundleTest(unittest.TestCase):
    """Phase 4: the two runtime-internal builders run through the skill bundle too."""

    def test_merge_conflict_prompt_prepends_merge_conflicts_bundle(self):
        out = prompts.build_merge_conflict_prompt(
            _Entity(), "feat-0042", "main", ["a.py", "b.py"], _Ctx(), direction="merge"
        )
        self.assertTrue(out.startswith(_BUNDLE_HEADER))
        self.assertIn("Route: merge-conflicts.", out)
        self.assertIn("===== routes/merge-conflicts.md =====", out)
        # per-run facts follow the bundle
        self.assertIn("MERGE CONFLICTS", out)
        self.assertIn("a.py", out)
        self.assertLess(out.index(_BUNDLE_END), out.index("MERGE CONFLICTS"))
        # the no-git / marker rules now live in the route doc (bundled)
        self.assertIn("<<<<<<<", out)
        # result-file schema still rendered
        self.assertIn("status", out)

    def test_merge_conflict_prepare_direction_names_primary_into_branch(self):
        out = prompts.build_merge_conflict_prompt(
            _Entity(), "feat-0042", "main", ["a.py"], _Ctx(), direction="prepare"
        )
        self.assertIn("INTO this issue's branch", out)

    def test_platform_error_prompt_prepends_bundle_keeps_marker_and_sentinel(self):
        out = prompts.build_platform_error_prompt(
            _Post(), {"origin_task_id": 9, "origin_action": "handle_label_removed",
                      "origin_post_id": "5"}, "new", _Ctx(),
        )
        self.assertTrue(out.startswith(_BUNDLE_HEADER))
        self.assertIn("Route: platform-error.", out)
        self.assertIn("===== routes/platform-error.md =====", out)
        # critical literals survive: the body marker (embedded) + the sentinel (route doc)
        self.assertIn("specseed:platform-error task=9", out)
        self.assertIn("PLATFORM_ERROR_REPORTED", out)
        # per-engagement facts + write-back follow the bundle
        self.assertIn("platform-error resolver", out)
        self.assertIn("WHY THIS RUN: new", out)
        self.assertLess(out.index(_BUNDLE_END), out.index("WHY THIS RUN"))

    def test_neither_internal_route_emits_instruction_file_reads(self):
        # merge-conflicts / platform-error own NO per-route guardrail/custom files.
        mc = prompts.build_merge_conflict_prompt(
            _Entity(), "b", "main", ["a.py"], _Ctx(), direction="merge"
        )
        self.assertNotIn("AGENTS_INSTRUCTIONS_MERGE", mc)
        self.assertNotIn("TARGET INSTRUCTION FILES", mc)
        pe = prompts.build_platform_error_prompt(_Post(), {}, "new", _Ctx())
        self.assertNotIn("AGENTS_INSTRUCTIONS_PLATFORM", pe)
        self.assertNotIn("TARGET INSTRUCTION FILES", pe)


class SkillModeTest(unittest.TestCase):
    def _storage(self, remote: dict | None) -> str:
        from specseed_runtime.storage_paths import remote_file
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        storage = Path(tmp.name)
        if remote is not None:
            rf = remote_file(storage)
            rf.parent.mkdir(parents=True, exist_ok=True)
            rf.write_text(json.dumps(remote), encoding="utf-8")
        return str(storage)

    def test_no_storage_defaults_specseed_ui(self):
        self.assertEqual(prompts._skill_mode(_Ctx()), "specseed-ui")

    def test_disabled_remote_is_specseed_ui(self):
        ctx = _Ctx(storage=self._storage({"enabled": False}))
        self.assertEqual(prompts._skill_mode(ctx), "specseed-ui")

    def test_missing_remote_json_is_specseed_ui(self):
        ctx = _Ctx(storage=self._storage(None))
        self.assertEqual(prompts._skill_mode(ctx), "specseed-ui")

    def test_github_provider_passes_through(self):
        ctx = _Ctx(storage=self._storage({"enabled": True, "provider": "github", "repo": "o/r"}))
        self.assertEqual(prompts._skill_mode(ctx), "github")

    def test_gitlab_provider_passes_through(self):
        ctx = _Ctx(storage=self._storage({"enabled": True, "provider": "gitlab", "repo": "o/r"}))
        self.assertEqual(prompts._skill_mode(ctx), "gitlab")

    def test_unknown_provider_falls_back_specseed_ui(self):
        ctx = _Ctx(storage=self._storage({"enabled": True, "provider": "bitbucket"}))
        self.assertEqual(prompts._skill_mode(ctx), "specseed-ui")


if __name__ == "__main__":
    unittest.main()
