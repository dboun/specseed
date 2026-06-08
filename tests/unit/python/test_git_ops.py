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


@unittest.skipUnless(_HAS_GIT, "git not available")
class MergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._git("init")
        self._git("symbolic-ref", "HEAD", "refs/heads/main")
        (self.root / "base.txt").write_text("base\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "root")

    def _git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.root),
                              capture_output=True, text=True)

    def _commit_on(self, branch, filename, content):
        git_ops.ensure_on_branch(self.root, branch, "main")
        (self.root / filename).write_text(content, encoding="utf-8")
        git_ops.commit_all(self.root, f"add {filename}")
        git_ops.checkout(self.root, "main")

    def test_clean_merge(self) -> None:
        self._commit_on("feat-1-a", "a.txt", "a\n")
        res = git_ops.merge(self.root, "feat-1-a", "main")
        self.assertTrue(res.ok)
        self.assertEqual(git_ops.current_branch(self.root), "main")
        self.assertTrue((self.root / "a.txt").exists())

    def test_conflict_left_in_place_then_completed(self) -> None:
        # main and branch both edit base.txt differently -> conflict
        git_ops.ensure_on_branch(self.root, "feat-1-c", "main")
        (self.root / "base.txt").write_text("branch side\n", encoding="utf-8")
        git_ops.commit_all(self.root, "branch edit")
        git_ops.checkout(self.root, "main")
        (self.root / "base.txt").write_text("main side\n", encoding="utf-8")
        git_ops.commit_all(self.root, "main edit")

        res = git_ops.merge(self.root, "feat-1-c", "main")
        self.assertFalse(res.ok)
        self.assertTrue(res.conflicted)
        self.assertIn("base.txt", res.files)
        self.assertTrue(git_ops.unmerged_files(self.root))
        # resolver fixes the file, runtime completes
        (self.root / "base.txt").write_text("resolved\n", encoding="utf-8")
        done = git_ops.complete_merge(self.root)
        self.assertTrue(done.ok)
        self.assertFalse(git_ops.unmerged_files(self.root))

    def test_complete_refuses_with_markers(self) -> None:
        git_ops.ensure_on_branch(self.root, "feat-1-d", "main")
        (self.root / "base.txt").write_text("branch\n", encoding="utf-8")
        git_ops.commit_all(self.root, "branch edit")
        git_ops.checkout(self.root, "main")
        (self.root / "base.txt").write_text("main\n", encoding="utf-8")
        git_ops.commit_all(self.root, "main edit")
        git_ops.merge(self.root, "feat-1-d", "main")
        # leave the conflict unresolved
        res = git_ops.complete_merge(self.root)
        self.assertFalse(res.ok)
        # abort cleans up
        self.assertTrue(git_ops.abort_merge(self.root).ok)
        self.assertFalse(git_ops.unmerged_files(self.root))


@unittest.skipUnless(_HAS_GIT, "git not available")
class PrepareMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._git("init")
        self._git("symbolic-ref", "HEAD", "refs/heads/main")
        (self.root / "base.txt").write_text("base\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "root")

    def _git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.root),
                              capture_output=True, text=True)

    def test_prepare_clean_then_branch_merges_clean(self) -> None:
        # branch touches a.txt; primary moves on a DIFFERENT file -> no conflict.
        git_ops.ensure_on_branch(self.root, "feat-1-a", "main")
        (self.root / "a.txt").write_text("a\n", encoding="utf-8")
        git_ops.commit_all(self.root, "branch add a")
        git_ops.checkout(self.root, "main")
        (self.root / "b.txt").write_text("b\n", encoding="utf-8")
        git_ops.commit_all(self.root, "main add b")

        prep = git_ops.prepare_merge(self.root, "feat-1-a", "main")
        self.assertTrue(prep.ok)
        self.assertFalse(prep.conflicted)
        # left on primary, branch now carries b.txt (primary merged in)
        self.assertEqual(git_ops.current_branch(self.root), "main")
        git_ops.checkout(self.root, "feat-1-a")
        self.assertTrue((self.root / "b.txt").exists())
        git_ops.checkout(self.root, "main")
        # the real merge into primary is now clean
        res = git_ops.merge(self.root, "feat-1-a", "main")
        self.assertTrue(res.ok)

    def test_prepare_conflict_left_on_branch_then_resolved(self) -> None:
        git_ops.ensure_on_branch(self.root, "feat-1-c", "main")
        (self.root / "base.txt").write_text("branch side\n", encoding="utf-8")
        git_ops.commit_all(self.root, "branch edit")
        git_ops.checkout(self.root, "main")
        (self.root / "base.txt").write_text("main side\n", encoding="utf-8")
        git_ops.commit_all(self.root, "main edit")

        prep = git_ops.prepare_merge(self.root, "feat-1-c", "main")
        self.assertFalse(prep.ok)
        self.assertTrue(prep.conflicted)
        self.assertIn("base.txt", prep.files)
        # mid-merge on the branch, ready for a resolver
        self.assertEqual(git_ops.current_branch(self.root), "feat-1-c")
        (self.root / "base.txt").write_text("resolved\n", encoding="utf-8")
        done = git_ops.complete_merge(self.root)
        self.assertTrue(done.ok)
        self.assertFalse(git_ops.unmerged_files(self.root))

    def test_prepare_already_current_is_noop_ok(self) -> None:
        git_ops.ensure_on_branch(self.root, "feat-1-x", "main")
        (self.root / "x.txt").write_text("x\n", encoding="utf-8")
        git_ops.commit_all(self.root, "branch add x")
        git_ops.checkout(self.root, "main")
        # primary has not moved past the branch base -> nothing to bring in
        prep = git_ops.prepare_merge(self.root, "feat-1-x", "main")
        self.assertTrue(prep.ok)
        self.assertEqual(git_ops.current_branch(self.root), "main")


if __name__ == "__main__":
    unittest.main()
