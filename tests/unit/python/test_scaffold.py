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
    """Stubs land in the home DATA ROOT under instructions/<route>/, never the target."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)  # the data root

    def test_seeds_four_route_repo_stubs_empty(self) -> None:
        written = scaffold.write_instruction_files(self.root)
        rels = {Path(p).relative_to(self.root).as_posix() for p in written}
        self.assertEqual(rels, {
            "instructions/impl/repo.md", "instructions/spec/repo.md",
            "instructions/review/repo.md", "instructions/ask/repo.md",
        })
        body = (self.root / "instructions" / "impl" / "repo.md").read_text(encoding="utf-8")
        self.assertIn("<!-- specseed:", body)
        self.assertNotIn("OFF-LIMITS", body)
        self.assertNotIn("vision.md", body)

    def test_never_overwrites_user_content(self) -> None:
        mine = self.root / "instructions" / "impl" / "repo.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("my repo notes\n", encoding="utf-8")
        written = scaffold.write_instruction_files(self.root)
        self.assertEqual(mine.read_text(encoding="utf-8"), "my repo notes\n")
        self.assertNotIn(str(mine), [str(p) for p in written])


class CustomInstructionStubsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_seeds_global_plus_per_route(self) -> None:
        written = scaffold.write_custom_instruction_stubs(self.root)
        rels = {Path(p).relative_to(self.root).as_posix() for p in written}
        self.assertEqual(rels, {
            "instructions/custom.md",
            "instructions/impl/custom.md", "instructions/spec/custom.md",
            "instructions/review/custom.md", "instructions/ask/custom.md",
        })

    def test_never_overwrites_user_content(self) -> None:
        mine = self.root / "instructions" / "impl" / "custom.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("my rules\n", encoding="utf-8")
        written = scaffold.write_custom_instruction_stubs(self.root)
        self.assertEqual(mine.read_text(encoding="utf-8"), "my rules\n")
        self.assertNotIn(str(mine), [str(p) for p in written])


class ScaffoldTargetTest(unittest.TestCase):
    """The target gets ONLY git; data + stubs live in the home data root."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "target"
        self.target.mkdir()
        self.data_root = Path(self.tmp.name) / "home" / "repos" / "x"

    @unittest.skipUnless(_HAS_GIT, "git not available")
    def test_writes_nothing_into_target_but_git(self) -> None:
        (self.target / "note.txt").write_text("user work\n", encoding="utf-8")
        result = scaffold.scaffold_target(self.target, self.data_root, "main")
        # no .gitignore, no .specseed, no router files in the target
        self.assertFalse((self.target / ".gitignore").exists())
        self.assertFalse((self.target / ".specseed").exists())
        self.assertFalse((self.target / "CLAUDE.md").exists())
        # stubs landed in the data root
        self.assertTrue((self.data_root / "instructions" / "impl" / "repo.md").exists())
        self.assertNotIn("gitignore", result)
        self.assertTrue(scaffold.is_git_repo(self.target))


if __name__ == "__main__":
    unittest.main()
