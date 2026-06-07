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
        # repo orientation: read the spec first, vision before sad
        self.assertIn(".specseed/spec/vision.md", body)
        self.assertIn(".specseed/spec/sad.md", body)
        self.assertLess(body.index("vision.md"), body.index("sad.md"))


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


class CustomInstructionStubsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_seeds_four_files(self) -> None:
        written = scaffold.write_custom_instruction_stubs(self.root, ".specseed")
        names = {Path(p).name for p in written}
        self.assertEqual(names, {
            "CUSTOM_INSTRUCTIONS.md", "CUSTOM_INSTRUCTIONS_IMPL.md",
            "CUSTOM_INSTRUCTIONS_SPEC.md", "CUSTOM_INSTRUCTIONS_REVIEW.md",
        })

    def test_never_overwrites_user_content(self) -> None:
        d = self.root / ".specseed"
        d.mkdir(parents=True)
        mine = d / "CUSTOM_INSTRUCTIONS_IMPL.md"
        mine.write_text("my rules\n", encoding="utf-8")
        written = scaffold.write_custom_instruction_stubs(self.root, ".specseed")
        # the existing file is untouched and not reported as written
        self.assertEqual(mine.read_text(encoding="utf-8"), "my rules\n")
        self.assertNotIn(str(mine), written)


class RepoGitignoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_adds_entry_and_is_idempotent(self) -> None:
        p = scaffold.ensure_repo_gitignored(self.root, ".specseed")
        self.assertEqual(p, self.root / ".gitignore")
        self.assertIn(".specseed/", p.read_text(encoding="utf-8"))
        # twice = no duplicate line
        scaffold.ensure_repo_gitignored(self.root, ".specseed")
        self.assertEqual(p.read_text(encoding="utf-8").count(".specseed/"), 1)

    def test_preserves_existing_gitignore_content(self) -> None:
        gi = self.root / ".gitignore"
        gi.write_text("*.pyc\n", encoding="utf-8")
        scaffold.ensure_repo_gitignored(self.root, "seedmeta")
        text = gi.read_text(encoding="utf-8")
        self.assertIn("*.pyc", text)
        self.assertIn("seedmeta/", text)

    @unittest.skipUnless(_HAS_GIT, "git not available")
    def test_scaffold_target_ignores_by_default(self) -> None:
        result = scaffold.scaffold_target(self.root, ".specseed", "main")
        self.assertEqual(result["gitignore"], str(self.root / ".gitignore"))
        self.assertIn(".specseed/", (self.root / ".gitignore").read_text(encoding="utf-8"))

    @unittest.skipUnless(_HAS_GIT, "git not available")
    def test_scaffold_target_skips_ignore_when_off(self) -> None:
        result = scaffold.scaffold_target(self.root, ".specseed", "main", ignore_specseed=False)
        self.assertIsNone(result["gitignore"])
        self.assertFalse((self.root / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
