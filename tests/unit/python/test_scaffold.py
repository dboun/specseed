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

    def test_seeds_four_route_stubs_empty(self) -> None:
        written = scaffold.write_instruction_files(self.root, ".specseed")
        names = {Path(p).name for p in written}
        self.assertEqual(
            names,
            {"AGENTS_INSTRUCTIONS_IMPL.md", "AGENTS_INSTRUCTIONS_SPEC.md",
             "AGENTS_INSTRUCTIONS_REVIEW.md", "AGENTS_INSTRUCTIONS_ASK.md"},
        )
        # empty user-owned stub: just an HTML comment, no injected guidance
        body = (self.root / ".specseed" / "AGENTS_INSTRUCTIONS_IMPL.md").read_text(encoding="utf-8")
        self.assertIn("<!-- specseed:", body)
        self.assertNotIn("OFF-LIMITS", body)
        self.assertNotIn("vision.md", body)

    def test_never_overwrites_user_content(self) -> None:
        d = self.root / ".specseed"
        d.mkdir(parents=True)
        mine = d / "AGENTS_INSTRUCTIONS_IMPL.md"
        mine.write_text("my repo notes\n", encoding="utf-8")
        written = scaffold.write_instruction_files(self.root, ".specseed")
        self.assertEqual(mine.read_text(encoding="utf-8"), "my repo notes\n")
        self.assertNotIn(str(mine), [str(p) for p in written])

    def test_no_router_files_written_to_repo_root(self) -> None:
        scaffold.write_instruction_files(self.root, ".specseed")
        # the only thing specseed adds to a target is <specseed_dir>/
        self.assertFalse((self.root / "CLAUDE.md").exists())
        self.assertFalse((self.root / "AGENTS.md").exists())


class CustomInstructionStubsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_seeds_five_files(self) -> None:
        written = scaffold.write_custom_instruction_stubs(self.root, ".specseed")
        names = {Path(p).name for p in written}
        self.assertEqual(names, {
            "CUSTOM_INSTRUCTIONS.md", "CUSTOM_INSTRUCTIONS_IMPL.md",
            "CUSTOM_INSTRUCTIONS_SPEC.md", "CUSTOM_INSTRUCTIONS_REVIEW.md",
            "CUSTOM_INSTRUCTIONS_ASK.md",
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
    def test_scaffold_target_always_ignores(self) -> None:
        result = scaffold.scaffold_target(self.root, ".specseed", "main")
        self.assertEqual(result["gitignore"], str(self.root / ".gitignore"))
        self.assertIn(".specseed/", (self.root / ".gitignore").read_text(encoding="utf-8"))

    @unittest.skipUnless(_HAS_GIT, "git not available")
    def test_scaffold_target_commits_gitignore_without_sweeping_other_files(self) -> None:
        (self.root / "note.txt").write_text("user work\n", encoding="utf-8")

        result = scaffold.scaffold_target(self.root, ".specseed", "main")

        self.assertIn("commit .gitignore", result["gitignore_commit"])
        show = subprocess.run(
            ["git", "show", "HEAD:.gitignore"],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(show.returncode, 0)
        self.assertIn(".specseed/", show.stdout)
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=self.root, capture_output=True, text=True,
        ).stdout.splitlines()
        self.assertIn(".gitignore", tracked)
        self.assertNotIn("note.txt", tracked)

    def test_absolute_dir_inside_repo_uses_relative_entry(self) -> None:
        # An absolute specseed dir under the repo is ignored by its repo-relative path.
        p = scaffold.ensure_repo_gitignored(self.root, self.root / "state")
        self.assertEqual(p, self.root / ".gitignore")
        self.assertIn("state/", p.read_text(encoding="utf-8"))

    def test_dir_outside_repo_is_noop(self) -> None:
        # A dir outside the repo is already excluded - nothing in-tree to ignore.
        outside = self.root.parent / "elsewhere-storage"
        self.assertIsNone(scaffold.ensure_repo_gitignored(self.root, outside))
        self.assertFalse((self.root / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
