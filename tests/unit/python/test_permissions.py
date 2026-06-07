"""test_permissions.py - Permissions switches + agent action-gate policy/rendering.

Pure config-in, bool/str-out. No tracker, no agent, no network.
"""

from __future__ import annotations

import unittest

from specseed_runtime.executing.permissions import (
    AGENT_CATEGORIES,
    DEFAULT_AGENT_GATES,
    Permissions,
)
from specseed_runtime.executing.prompts import (
    build_implement_prompt,
    render_action_gates,
    render_git_policy,
)


def _cfg(perms=None):
    return {"permissions": perms or {}}


class RemoteEnabledTest(unittest.TestCase):
    def test_from_remote_state_not_config(self) -> None:
        # legacy config "backend" is ignored; remote.json state decides.
        p = Permissions({"backend": {"enabled": True}}, {"enabled": False})
        self.assertFalse(p.remote_enabled())
        p = Permissions({}, {"enabled": True, "provider": "github"})
        self.assertTrue(p.remote_enabled())

    def test_no_remote_state_means_disabled(self) -> None:
        self.assertFalse(Permissions({}).remote_enabled())


class GitTest(unittest.TestCase):
    def test_git_always_on(self) -> None:
        # git is mandatory now (no enabled switch); git_enabled is constant True.
        self.assertTrue(Permissions({}).git_enabled())
        self.assertTrue(Permissions(_cfg({"git": {"enabled": False}})).git_enabled())

    def test_merge_to_dev_branch_only_needs_its_switch(self) -> None:
        on = _cfg({"git": {"merge_to_dev_branch": True}})
        off = _cfg({"git": {"merge_to_dev_branch": False}})
        self.assertTrue(Permissions(on).can_merge_to_dev_branch())
        self.assertFalse(Permissions(off).can_merge_to_dev_branch())
        self.assertFalse(Permissions({}).can_merge_to_dev_branch())


class RemoteSwitchesTest(unittest.TestCase):
    def test_issues_and_spec_change_always_allowed(self) -> None:
        # the tracker lives on the remote; no opt-out, even with a real provider.
        p = Permissions(_cfg(), {"enabled": True, "provider": "github"})
        self.assertTrue(p.can_post_issues())
        self.assertTrue(p.can_run_spec_change())

    def test_switches_open_when_remote_disabled(self) -> None:
        # local stand-in: writes have no external effect.
        p = Permissions(_cfg({"remote": {"post_control": False, "push_dev_branch": False}}))
        self.assertTrue(p.can_post_control())
        self.assertTrue(p.can_push_dev_branch())
        self.assertTrue(p.can_push_branches())
        self.assertTrue(p.can_make_prs())

    def test_switches_bite_when_remote_enabled(self) -> None:
        state = {"enabled": True, "provider": "github"}
        p = Permissions(_cfg({"remote": {"post_control": False, "push_branches": True,
                                         "push_dev_branch": False, "make_prs": False}}), state)
        self.assertFalse(p.can_post_control())
        self.assertTrue(p.can_push_branches())
        self.assertFalse(p.can_push_dev_branch())
        self.assertFalse(p.can_make_prs())


class PlatformTest(unittest.TestCase):
    def test_auto_implement_defaults_true(self) -> None:
        self.assertTrue(Permissions({}).auto_implement_issue())

    def test_auto_implement_off(self) -> None:
        p = Permissions(_cfg({"platform": {"auto_implement_issue": False}}))
        self.assertFalse(p.auto_implement_issue())

    def test_auto_next_sprint_defaults_false(self) -> None:
        self.assertFalse(Permissions({}).auto_proceed_to_next_sprint())
        p = Permissions(_cfg({"platform": {"auto_proceed_to_next_sprint_if_available": True}}))
        self.assertTrue(p.auto_proceed_to_next_sprint())


class AgentGatesTest(unittest.TestCase):
    def test_defaults_match_taxonomy(self) -> None:
        p = Permissions({})
        self.assertEqual(p.agents_policy(), DEFAULT_AGENT_GATES)
        self.assertEqual(set(p.agents_policy()), set(AGENT_CATEGORIES))

    def test_config_overrides(self) -> None:
        p = Permissions(_cfg({"agents": {"network": "auto", "deps": "require_human_approval"}}))
        self.assertEqual(p.agent_gate("network"), "auto")
        self.assertEqual(p.agent_gate("deps"), "require_human_approval")
        self.assertEqual(p.agent_gate("container"), "block")  # untouched default

    def test_invalid_level_falls_back_to_default(self) -> None:
        p = Permissions(_cfg({"agents": {"network": "yolo"}}))
        self.assertEqual(p.agent_gate("network"), DEFAULT_AGENT_GATES["network"])

    def test_unknown_category_blocks(self) -> None:
        self.assertEqual(Permissions({}).agent_gate("time_travel"), "block")


class _Ctx:
    def __init__(self, config):
        self.config = config
        self.permissions = Permissions(config)


class _Entity:
    post_id = "7"
    title = "Add the thing"


class RenderActionGatesTest(unittest.TestCase):
    def test_renders_every_category_with_level(self) -> None:
        text = render_action_gates(_Ctx(_cfg({"agents": {"network": "auto"}})))
        for category in AGENT_CATEGORIES:
            self.assertIn(category, text)
        self.assertIn("network", text)
        self.assertIn("just do it", text)            # auto rule
        self.assertIn("do NOT do it", text)          # block rule

    def test_implement_prompt_carries_gates(self) -> None:
        prompt = build_implement_prompt(_Entity(), _Ctx({"specseed_dir": ".specseed"}))
        self.assertIn("Action gates", prompt)
        for category in AGENT_CATEGORIES:
            self.assertIn(category, prompt)


class _GitCtx:
    def __init__(self, config, remote_state=None):
        self.config = config
        self.permissions = Permissions(config, remote_state)


class RenderGitPolicyTest(unittest.TestCase):
    def test_uses_dev_branch_and_forbids_direct_commit(self) -> None:
        text = render_git_policy(_GitCtx({"dev_branch": "develop", "permissions": {}}))
        self.assertIn("`develop`", text)
        self.assertIn("Never commit", text)

    def test_merge_allowed_adds_refresh_rule(self) -> None:
        cfg = {"dev_branch": "main", "permissions": {"git": {"enabled": True, "merge_to_dev_branch": True}}}
        text = render_git_policy(_GitCtx(cfg))
        self.assertIn("merge your branch into `main`", text)
        self.assertIn("Refresh", text)

    def test_merge_disallowed_says_leave_for_human(self) -> None:
        text = render_git_policy(_GitCtx({"dev_branch": "main", "permissions": {}}))
        self.assertIn("Do NOT merge", text)

    def test_local_only_forbids_push_and_pr(self) -> None:
        text = render_git_policy(_GitCtx({"dev_branch": "main", "permissions": {}}))
        self.assertIn("No remote is configured", text)

    def test_remote_on_honours_push_and_pr_switches(self) -> None:
        cfg = {
            "dev_branch": "main",
            "permissions": {"remote": {"push_branches": True, "push_dev_branch": False, "make_prs": False}},
        }
        text = render_git_policy(_GitCtx(cfg, {"enabled": True, "provider": "github"}))
        self.assertIn("push your issue branch", text)
        self.assertIn("Do NOT push `main`", text)
        self.assertIn("Do NOT open pull/merge requests", text)

    def test_implement_prompt_carries_git_policy(self) -> None:
        prompt = build_implement_prompt(_Entity(), _Ctx({"specseed_dir": ".specseed"}))
        self.assertIn("Git rules", prompt)


if __name__ == "__main__":
    unittest.main()
