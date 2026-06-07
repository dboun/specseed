"""scaffold.py - make a target git-ready and identity-clear.

Two environment fixes the run history demanded:

1. **Git is mandatory.** A target with no commits made the whole branch / diff /
   isolation policy degrade silently (reviewer judged the whole tree, concurrent
   issues clobbered each other). So we ``git init`` a non-git target and give the
   primary branch a root commit to branch from.
2. **The engine is off-limits.** Agents kept concluding the "app" was specseed
   itself and editing the engine. We drop per-route instruction files into the
   target's specseed_dir and prepend a small router block to the repo-root
   ``CLAUDE.md`` / ``AGENTS.md`` pointing at them - preserving any user content.

Both are idempotent and best-effort (a git hiccup never aborts configure). Reused
by ``configuring/configure.py`` (setup) and ``executing/run.py`` (startup repair).

Only Python stdlib is used.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

_ROUTER_START = "<!-- specseed:router:start -->"
_ROUTER_END = "<!-- specseed:router:end -->"

_INSTRUCTION_FILES = {
    "AGENTS_INSTRUCTIONS_IMPL.md": "implementing a work issue",
    "AGENTS_INSTRUCTIONS_SPEC.md": "running a spec-change",
    "AGENTS_INSTRUCTIONS_REVIEW.md": "reviewing completed work",
}


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo_root), capture_output=True, text=True
    )


def is_git_repo(repo_root: str | Path) -> bool:
    try:
        out = _run_git(Path(repo_root), ["rev-parse", "--is-inside-work-tree"])
    except Exception:
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def _has_head(repo_root: Path) -> bool:
    try:
        return _run_git(repo_root, ["rev-parse", "--verify", "HEAD"]).returncode == 0
    except Exception:
        return False


def ensure_git_repo(repo_root: str | Path, primary_branch: str = "main") -> list[str]:
    """Init a non-git target and ensure the primary branch has a root commit.

    Returns a list of human-readable actions taken (empty when already set up).
    An empty root commit (no ``git add``) keeps it safe - never sweeps in storage
    or secrets - while giving the agent a real branch to fork from.
    """
    repo_root = Path(repo_root)
    actions: list[str] = []
    if not is_git_repo(repo_root):
        if _run_git(repo_root, ["init"]).returncode != 0:
            return actions  # git unavailable / unwritable; bail quietly
        actions.append("git init")
        # Name the initial (unborn) branch before the first commit.
        _run_git(repo_root, ["symbolic-ref", "HEAD", f"refs/heads/{primary_branch}"])
    if not _has_head(repo_root):
        res = _run_git(
            repo_root,
            [
                "-c", "user.email=specseed@local",
                "-c", "user.name=specseed",
                "commit", "--allow-empty", "-m", "specseed: initialize repository",
            ],
        )
        if res.returncode == 0:
            actions.append(f"root commit on {primary_branch}")
    return actions


def _instruction_body(repo_root: Path, specseed_dir: str, kind: str) -> str:
    return (
        f"# specseed agent rules ({kind})\n\n"
        "**Your target is THIS repository** (the directory this file's repo root lives in). "
        "Build only what the spec under "
        f"`{specseed_dir}/spec/` describes, here, scoped to the issue you were given.\n\n"
        "**The specseed engine is OFF-LIMITS.** The `specseed_runtime` package is on your "
        "PYTHONPATH only as read-only tooling that drives you. NEVER create, edit, move, or "
        "delete anything under it, the specseed engine checkout, or anywhere outside this "
        "repository. If a task seems to ask you to change the engine, it does not - it means "
        "build the equivalent in THIS repo.\n\n"
        "**An empty or near-empty target at the start is normal.** Do not go looking for an "
        "existing app to modify; create it.\n\n"
        "**Git:** work on a branch forked from the primary branch; never commit straight onto it. "
        "The runtime never runs git for you - you do.\n"
    )


def write_instruction_files(repo_root: str | Path, specseed_dir: str) -> list[Path]:
    """Write/refresh the per-route guardrail files under ``<specseed_dir>/``.

    These are engine-owned, so a refresh overwrites them (idempotent). Returns the
    paths written.
    """
    repo_root = Path(repo_root)
    target_dir = repo_root / specseed_dir
    written: list[Path] = []
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return written
    for fname, kind in _INSTRUCTION_FILES.items():
        path = target_dir / fname
        try:
            path.write_text(_instruction_body(repo_root, specseed_dir, kind), encoding="utf-8")
            written.append(path)
        except OSError:
            pass
    return written


def _router_block(specseed_dir: str) -> str:
    return (
        f"{_ROUTER_START}\n"
        "**IMPORTANT (specseed):** before any specseed work read the matching guardrail file in "
        f"`{specseed_dir}/`: `AGENTS_INSTRUCTIONS_IMPL.md` (implementing), "
        "`AGENTS_INSTRUCTIONS_SPEC.md` (spec-change), `AGENTS_INSTRUCTIONS_REVIEW.md` (review). "
        "Your target is THIS repository; never modify the specseed engine.\n"
        f"{_ROUTER_END}"
    )


def ensure_router_block(repo_root: str | Path, specseed_dir: str) -> list[Path]:
    """Prepend/refresh the router block in repo-root CLAUDE.md + AGENTS.md.

    Preserves any existing user content: only our delimited block is inserted or
    replaced. Returns the files touched.
    """
    repo_root = Path(repo_root)
    block = _router_block(specseed_dir)
    touched: list[Path] = []
    for fname in ("CLAUDE.md", "AGENTS.md"):
        path = repo_root / fname
        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            existing = ""
        new = _splice_block(existing, block)
        if new == existing:
            continue
        try:
            path.write_text(new, encoding="utf-8")
            touched.append(path)
        except OSError:
            pass
    return touched


def _splice_block(existing: str, block: str) -> str:
    if _ROUTER_START in existing and _ROUTER_END in existing:
        head, _, rest = existing.partition(_ROUTER_START)
        _, _, tail = rest.partition(_ROUTER_END)
        return head + block + tail
    if not existing.strip():
        return block + "\n"
    return block + "\n\n" + existing


def scaffold_target(repo_root: str | Path, specseed_dir: str, primary_branch: str = "main") -> dict:
    """Do all three: git, instruction files, router block. Best-effort."""
    return {
        "git": ensure_git_repo(repo_root, primary_branch),
        "instructions": [str(p) for p in write_instruction_files(repo_root, specseed_dir)],
        "router": [str(p) for p in ensure_router_block(repo_root, specseed_dir)],
    }
