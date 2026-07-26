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


def _is_ignored(repo_root: Path, entry: str) -> bool:
    """True if ``entry`` is already excluded by a .gitignore."""
    return _run(repo_root, ["check-ignore", "-q", "--", entry]).returncode == 0


def _exclude_pathspecs(
    repo_root: Path, exclude_paths: Optional[list[str | Path]]
) -> list[str]:
    """Build ``:(exclude)`` pathspecs, DROPPING any path git already ignores.

    A path that is also gitignored never reaches the index via ``git add -A``, so it
    needs no exclude pathspec. Worse, passing ``:(exclude)<ignored>`` makes ``git add``
    abort with "paths are ignored" - which silently broke every implement commit
    (specseed_dir is always gitignored). So only exclude paths that git would
    otherwise stage.
    """
    out: list[str] = []
    for raw in exclude_paths or []:
        entry = Path(raw).as_posix().strip("/")
        if entry and entry != "." and not _is_ignored(repo_root, entry):
            out.append(":(exclude){0}".format(entry))
    return out


def _slug(text: str) -> str:
    s = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return s[:_MAX_SLUG].strip("-")


def branch_name(entity: Any) -> str:
    """Deterministic, stable per-issue branch name: ``<human-id>-<slug>-<post_id>``.

    Parses a leading human id (``FEAT-0001``) out of the title and slugs the rest,
    then folds the post id onto the END so two posts that share a human id (or carry
    no human id at all) can NEVER collide onto one branch - the post id is unique per
    entity. Stable across runs so an issue bounced back from review reuses its branch.
    """
    title = str(getattr(entity, "title", "") or "")
    post_id = _slug(str(getattr(entity, "post_id", "") or ""))
    m = _HUMAN_ID_RE.match(title)
    if m:
        head = m.group(1).lower()
        slug = _slug(m.group(2))
        head = f"{head}-{slug}" if slug else head
    else:
        slug = _slug(title)
        head = f"issue-{slug}" if slug else "issue"
    name = f"{head}-{post_id}" if post_id else head
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


def has_staged_changes(repo_root: str | Path) -> bool:
    """True when the index has something to commit."""
    out = _run(Path(repo_root), ["diff", "--cached", "--quiet"])
    return out.returncode == 1


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


def commit_all(
    repo_root: str | Path,
    message: str,
    exclude_paths: Optional[list[str | Path]] = None,
) -> GitResult:
    """Stage everything except excluded dirs and commit. No-op when nothing stages."""
    repo_root = Path(repo_root)
    if not has_changes(repo_root):
        return GitResult(ok=True, detail="nothing to commit")
    add = _run(repo_root, ["add", "-A", "--", ".", *_exclude_pathspecs(repo_root, exclude_paths)])
    if add.returncode != 0:
        return GitResult(ok=False, error=add.stderr.strip() or "git add failed")
    if not has_staged_changes(repo_root):
        return GitResult(ok=True, detail="nothing to commit after exclusions")
    commit = _run(repo_root, [*_GIT_ENV_ARGS, "commit", "-m", message])
    if commit.returncode != 0:
        return GitResult(ok=False, error=commit.stderr.strip() or "git commit failed")
    return GitResult(ok=True, actions=["commit"], detail="committed")


@dataclass
class MergeResult:
    ok: bool
    conflicted: bool = False
    files: list[str] = field(default_factory=list)
    error: Optional[str] = None
    actions: list[str] = field(default_factory=list)


def unmerged_files(repo_root: str | Path) -> list[str]:
    """Paths with unresolved merge conflicts (git diff-filter=U)."""
    out = _run(Path(repo_root), ["diff", "--name-only", "--diff-filter=U"])
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def merge(repo_root: str | Path, branch: str, primary: str, message: Optional[str] = None) -> MergeResult:
    """Merge ``branch`` into ``primary``. Clean -> ok. Conflict -> conflicted+files
    (the half-merged state is LEFT in place for a resolver; caller completes or
    aborts). Any other failure -> ok=False with an error."""
    repo_root = Path(repo_root)
    co = _run(repo_root, ["checkout", primary])
    if co.returncode != 0:
        return MergeResult(ok=False, error=co.stderr.strip() or f"checkout {primary} failed")
    msg = message or f"specseed: merge {branch} into {primary}"
    out = _run(repo_root, [*_GIT_ENV_ARGS, "merge", "--no-ff", "-m", msg, branch])
    if out.returncode == 0:
        return MergeResult(ok=True, actions=[f"merge {branch} -> {primary}"])
    files = unmerged_files(repo_root)
    if files:
        return MergeResult(ok=False, conflicted=True, files=files)
    # non-conflict failure (e.g. unknown branch); leave nothing half-done.
    _run(repo_root, ["merge", "--abort"])
    return MergeResult(ok=False, error=out.stderr.strip() or "merge failed")


def prepare_merge(repo_root: str | Path, branch: str, primary: str) -> MergeResult:
    """Bring ``primary`` INTO ``branch`` so a later ``branch``->``primary`` merge is clean.

    Checks out ``branch`` and merges ``primary`` onto it. Clean -> ok, repo left back
    on ``primary`` with the branch now containing primary (ready to merge). Conflict ->
    ``conflicted``+files, LEFT mid-merge on ``branch`` for a resolver (the caller runs
    the merge-conflicts agent then ``complete_merge``/``abort_merge``). Any other failure
    -> ok=False with an error, nothing left half-done.

    This is the readiness step: a gate is only opened once a branch prepares clean, so
    the human never approves a merge that cannot run.
    """
    repo_root = Path(repo_root)
    co = _run(repo_root, ["checkout", branch])
    if co.returncode != 0:
        return MergeResult(ok=False, error=co.stderr.strip() or f"checkout {branch} failed")
    msg = f"specseed: merge {primary} into {branch} (prepare)"
    out = _run(repo_root, [*_GIT_ENV_ARGS, "merge", "--no-ff", "-m", msg, primary])
    if out.returncode == 0:
        _run(repo_root, ["checkout", primary])  # leave repo on primary, branch ready
        return MergeResult(ok=True, actions=[f"prepare {primary} -> {branch}"])
    files = unmerged_files(repo_root)
    if files:
        return MergeResult(ok=False, conflicted=True, files=files)
    _run(repo_root, ["merge", "--abort"])
    return MergeResult(ok=False, error=out.stderr.strip() or "prepare merge failed")


def _has_conflict_markers(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "<<<<<<<" in text and ">>>>>>>" in text


def complete_merge(
    repo_root: str | Path,
    message: Optional[str] = None,
    exclude_paths: Optional[list[str | Path]] = None,
) -> GitResult:
    """Finish an in-progress merge after conflicts were resolved in the worktree.

    A resolver agent edits files but never runs git, so conflicted paths stay in
    the index's unmerged state until we ``git add`` them - which is why we judge
    "still conflicted" by scanning those files for leftover markers, not by the
    index state. Fails (without committing) if any marker remains; otherwise stages
    everything and commits the merge."""
    repo_root = Path(repo_root)
    for rel in unmerged_files(repo_root):
        if _has_conflict_markers(repo_root / rel):
            return GitResult(ok=False, error="unresolved conflicts remain in {0}".format(rel))
    add = _run(repo_root, ["add", "-A", "--", ".", *_exclude_pathspecs(repo_root, exclude_paths)])
    if add.returncode != 0:
        return GitResult(ok=False, error=add.stderr.strip() or "git add failed")
    if not has_staged_changes(repo_root):
        return GitResult(ok=False, error="merge resolved but nothing staged")
    args = [*_GIT_ENV_ARGS, "commit", "--no-edit"]
    if message:
        args = [*_GIT_ENV_ARGS, "commit", "-m", message]
    commit = _run(repo_root, args)
    if commit.returncode != 0:
        return GitResult(ok=False, error=commit.stderr.strip() or "git commit failed")
    return GitResult(ok=True, actions=["complete merge"], detail="merged")


def abort_merge(repo_root: str | Path) -> GitResult:
    out = _run(Path(repo_root), ["merge", "--abort"])
    if out.returncode != 0:
        return GitResult(ok=False, error=out.stderr.strip() or "merge --abort failed")
    return GitResult(ok=True, actions=["merge --abort"])
