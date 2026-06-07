"""test_git_ops.py - runtime-owned git lifecycle.

Runs real ``git`` against a tmp repo (no network, no agent). Skips if git is
unavailable on the box.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from specseed_runtime.executing import git_ops

_HAS_GIT = shutil.which("git") is not None


class _Entity:
    def __init__(self, post_id="7", title="FEAT-0001 Implement the CLI entry point"):
        self.post_id = post_id
        self.title = title


class BranchNameTest(unittest.TestCase):
    def test_human_id_and_slug(self) -> None:
        self.assertEqual(
            git_ops.branch_name(_Entity(title="FEAT-0001 Implement the CLI entry point")),
            "feat-0001-implement-the-cli-entry-point",
        )

    def test_falls_back_to_post_id_without_human_id(self) -> None:
        self.assertEqual(
            git_ops.branch_name(_Entity(post_id="42", title="just a title")),
            "issue-42-just-a-title",
        )

    def test_empty_title_uses_post_id(self) -> None:
        self.assertEqual(git_ops.branch_name(_Entity(post_id="9", title="")), "issue-9")


@unittest.skipUnless(_HAS_GIT, "git not available")
class GitLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._git("init")
        self._git("symbolic-ref", "HEAD", "refs/heads/main")
        self._git("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "--allow-empty", "-m", "root")

    def _git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.root),
                              capture_output=True, text=True)

    def test_ensure_on_branch_creates_then_reuses(self) -> None:
        res = git_ops.ensure_on_branch(self.root, "feat-0001-x", "main")
        self.assertTrue(res.ok)
        self.assertEqual(git_ops.current_branch(self.root), "feat-0001-x")
        # go back to main, then ensure again -> checkout existing, no recreate
        git_ops.checkout(self.root, "main")
        res2 = git_ops.ensure_on_branch(self.root, "feat-0001-x", "main")
        self.assertTrue(res2.ok)
        self.assertEqual(git_ops.current_branch(self.root), "feat-0001-x")

    def test_commit_all_commits_changes_then_noop(self) -> None:
        git_ops.ensure_on_branch(self.root, "feat-0001-x", "main")
        (self.root / "file.txt").write_text("hi\n", encoding="utf-8")
        self.assertTrue(git_ops.has_changes(self.root))
        c = git_ops.commit_all(self.root, "specseed: work")
        self.assertTrue(c.ok)
        self.assertFalse(git_ops.has_changes(self.root))
        # clean tree -> ok no-op
        c2 = git_ops.commit_all(self.root, "specseed: work")
        self.assertTrue(c2.ok)
        self.assertEqual(c2.detail, "nothing to commit")

    def test_work_isolated_on_branch_not_on_primary(self) -> None:
        # simulate a full implement cycle: branch, edit, commit, return to main
        branch = "feat-0001-x"
        git_ops.ensure_on_branch(self.root, branch, "main")
        (self.root / "new.py").write_text("print(1)\n", encoding="utf-8")
        git_ops.commit_all(self.root, "specseed: add new.py")
        git_ops.checkout(self.root, "main")
        # main is clean of the new file; the branch carries it
        self.assertFalse((self.root / "new.py").exists())
        self.assertEqual(git_ops.current_branch(self.root), "main")
        git_ops.checkout(self.root, branch)
        self.assertTrue((self.root / "new.py").exists())


if __name__ == "__main__":
    unittest.main()
