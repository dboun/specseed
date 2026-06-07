"""git_ops.py - the runtime runs git, never the agent.

The old design told the agent to branch/commit/merge in its prompt. Agents did it
unreliably (work left uncommitted on the primary branch, no isolation between
issues). So the runtime owns the whole git lifecycle around a work run:

* implement run: check out the issue's branch (created off the primary branch the
  first time, reused after), let the agent edit, then commit the result - even a
  partial WIP on interruption, so nothing is lost.
* review run: check out the issue's branch so the reviewer sees the work, then
  return to the primary branch when done.
* always return to the primary branch after a run, leaving each issue branch in
  place for merge (runtime-driven, see the merge helpers).

The agent only edits files. Branch/commit/checkout/merge are mechanical and live
here. Best-effort + loud: every helper returns a small result the caller logs.

Only Python stdlib is used.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Git identity used for runtime-made commits (the agent's edits, committed by us).
_GIT_ENV_ARGS = ["-c", "user.email=specseed@local", "-c", "user.name=specseed"]

_HUMAN_ID_RE = re.compile(r"^\s*([A-Za-z]+-\d+)\b\s*(.*)$", re.DOTALL)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_SLUG = 48


@dataclass
class GitResult:
    ok: bool
    actions: list[str] = field(default_factory=list)
    error: Optional[str] = None
    detail: Optional[str] = None


def _run(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo_root), capture_output=True, text=True
    )


def _slug(text: str) -> str:
    s = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return s[:_MAX_SLUG].strip("-")


def branch_name(entity: Any) -> str:
    """Deterministic, stable per-issue branch name: ``<human-id>-<slug>``.

    Parses a leading human id (``FEAT-0001``) out of the title and slugs the rest;
    falls back to ``issue-<post_id>`` when no human id is present. Stable across
    runs so an issue bounced back from review reuses its branch.
    """
    title = str(getattr(entity, "title", "") or "")
    post_id = str(getattr(entity, "post_id", "") or "").strip()
    m = _HUMAN_ID_RE.match(title)
    if m:
        human = m.group(1).lower()
        slug = _slug(m.group(2))
    else:
        human = f"issue-{post_id}" if post_id else "issue"
        slug = _slug(title)
    name = f"{human}-{slug}" if slug else human
    return name.strip("-") or (f"issue-{post_id}" if post_id else "specseed-work")


def current_branch(repo_root: str | Path) -> Optional[str]:
    out = _run(Path(repo_root), ["rev-parse", "--abbrev-ref", "HEAD"])
    if out.returncode != 0:
        return None
    name = out.stdout.strip()
    return name or None


def _branch_exists(repo_root: Path, branch: str) -> bool:
    return _run(repo_root, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0


def has_changes(repo_root: str | Path) -> bool:
    """True if the working tree has staged or unstaged changes (incl. untracked)."""
    out = _run(Path(repo_root), ["status", "--porcelain"])
    return bool(out.stdout.strip())


def checkout(repo_root: str | Path, ref: str) -> GitResult:
    repo_root = Path(repo_root)
    out = _run(repo_root, ["checkout", ref])
    if out.returncode != 0:
        return GitResult(ok=False, error=out.stderr.strip() or f"checkout {ref} failed")
    return GitResult(ok=True, actions=[f"checkout {ref}"])


def ensure_on_branch(repo_root: str | Path, branch: str, base: str) -> GitResult:
    """Check out ``branch``, creating it from ``base`` the first time.

    Idempotent: an existing branch is just checked out (its history kept, so a
    re-run after a review bounce continues where it left off).
    """
    repo_root = Path(repo_root)
    actions: list[str] = []
    if _branch_exists(repo_root, branch):
        out = _run(repo_root, ["checkout", branch])
        if out.returncode != 0:
            return GitResult(ok=False, error=out.stderr.strip() or f"checkout {branch} failed")
        actions.append(f"checkout {branch}")
        return GitResult(ok=True, actions=actions)
    # create from base. base may be unborn-safe: only pass it if it exists.
    args = ["checkout", "-b", branch]
    if _branch_exists(repo_root, base):
        args.append(base)
    out = _run(repo_root, args)
    if out.returncode != 0:
        return GitResult(ok=False, error=out.stderr.strip() or f"create {branch} failed")
    actions.append(f"branch {branch} from {base}")
    return GitResult(ok=True, actions=actions)


def commit_all(repo_root: str | Path, message: str) -> GitResult:
    """Stage everything and commit. No-op (ok) when the tree is clean."""
    repo_root = Path(repo_root)
    if not has_changes(repo_root):
        return GitResult(ok=True, detail="nothing to commit")
    add = _run(repo_root, ["add", "-A"])
    if add.returncode != 0:
        return GitResult(ok=False, error=add.stderr.strip() or "git add failed")
    commit = _run(repo_root, [*_GIT_ENV_ARGS, "commit", "-m", message])
    if commit.returncode != 0:
        return GitResult(ok=False, error=commit.stderr.strip() or "git commit failed")
    return GitResult(ok=True, actions=["commit"], detail="committed")
