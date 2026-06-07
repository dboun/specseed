"""test_scaffold.py - git enforcement + identity guardrails for a target.

Runs real ``git`` against a tmp dir (no network, no agent). Skips git asserts if
git is unavailable on the box.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from specseed_runtime.configuring import scaffold

_HAS_GIT = shutil.which("git") is not None


class GitEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    @unittest.skipUnless(_HAS_GIT, "git not available")
    def test_inits_non_git_target_with_primary_branch_and_head(self) -> None:
        actions = scaffold.ensure_git_repo(self.root, "develop")
        self.assertTrue(scaffold.is_git_repo(self.root))
        self.assertIn("git init", actions)
        # branch is develop and there is a HEAD commit to fork from
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.root, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(branch, "develop")
        self.assertEqual(
            subprocess.run(["git", "rev-parse", "--verify", "HEAD"],
                           cwd=self.root, capture_output=True, text=True).returncode,
            0,
        )

    @unittest.skipUnless(_HAS_GIT, "git not available")
    def test_idempotent_on_existing_repo(self) -> None:
        scaffold.ensure_git_repo(self.root, "main")
        self.assertEqual(scaffold.ensure_git_repo(self.root, "main"), [])


class InstructionFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_writes_three_route_files(self) -> None:
        written = scaffold.write_instruction_files(self.root, ".specseed")
        names = {Path(p).name for p in written}
        self.assertEqual(
            names,
            {"AGENTS_INSTRUCTIONS_IMPL.md", "AGENTS_INSTRUCTIONS_SPEC.md", "AGENTS_INSTRUCTIONS_REVIEW.md"},
        )
        body = (self.root / ".specseed" / "AGENTS_INSTRUCTIONS_IMPL.md").read_text(encoding="utf-8")
        self.assertIn("OFF-LIMITS", body)


class RouterBlockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_prepends_block_preserving_user_content(self) -> None:
        claude = self.root / "CLAUDE.md"
        claude.write_text("# My project\n\nuser notes\n", encoding="utf-8")
        scaffold.ensure_router_block(self.root, ".specseed")
        text = claude.read_text(encoding="utf-8")
        self.assertIn(scaffold._ROUTER_START, text)
        self.assertIn("# My project", text)  # user content kept
        self.assertIn("user notes", text)

    def test_refresh_replaces_only_block(self) -> None:
        claude = self.root / "CLAUDE.md"
        claude.write_text("keep me\n", encoding="utf-8")
        scaffold.ensure_router_block(self.root, ".specseed")
        scaffold.ensure_router_block(self.root, ".specseed")  # twice
        text = claude.read_text(encoding="utf-8")
        self.assertEqual(text.count(scaffold._ROUTER_START), 1)
        self.assertIn("keep me", text)

    def test_creates_agents_md_when_absent(self) -> None:
        scaffold.ensure_router_block(self.root, ".specseed")
        self.assertTrue((self.root / "AGENTS.md").exists())
        self.assertTrue((self.root / "CLAUDE.md").exists())


if __name__ == "__main__":
    unittest.main()
