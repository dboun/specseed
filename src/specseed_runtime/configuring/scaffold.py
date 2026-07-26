"""scaffold.py - make a target git-ready and seed user-owned instructions.

Two environment fixes the run history demanded:

1. **Git is mandatory.** A target with no commits made the whole branch / diff /
   isolation policy degrade silently (reviewer judged the whole tree, concurrent
   issues clobbered each other). So we ``git init`` a non-git target and give the
   primary branch a root commit to branch from.
2. **The engine is off-limits.** Agents kept concluding the "app" was specseed
   itself and editing the engine. We seed empty per-route instruction stubs (in the
   home DATA ROOT, not the target); the skill bundle (``generate_prompt_from_skill``)
   tells the agent which to read each run.

Nothing else is written into the target - no ``.specseed/``, no repo-root
CLAUDE.md/AGENTS.md, no .gitignore line. ALL data lives in the home data root, so the
target stays clean. Both fixes are idempotent and best-effort (a git hiccup never
aborts configure). Reused by ``configuring/configure.py`` (setup) and
``executing/run.py`` (startup repair).

The instruction stubs (``instructions/<route>/repo.md`` for repo context +
``instructions/custom.md`` / ``instructions/<route>/custom.md`` for user overrides)
are USER-OWNED and empty by default: seeded create-if-absent, NEVER overwritten on
refresh, so edits survive.

Only Python stdlib is used.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from specseed_runtime.storage_paths import instructions_dir

# Per-route repo-context, one per skill route that runs in the target
# (impl / spec / review / ask). Lives at ``instructions/<route>/repo.md`` in the
# DATA ROOT (home, not the target). Empty by default; the user fills in repo-level
# structure + quick context. The skill bundle tells the agent which to read.
_ROUTE_KINDS = {
    "impl": "implementing a work issue",
    "spec": "running a spec-change",
    "review": "reviewing completed work",
    "ask": "answering a question",
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


def _instruction_body(kind: str) -> str:
    """An EMPTY repo-context stub. The generic guidance (spec-read order, target/engine
    boundary, git policy) now lives in the skill, injected every run; this file is the
    user's place for repo-level structure + quick context, and is empty until they fill
    it. Only an HTML comment so the agent reads "nothing to honor" by default."""
    return (
        f"<!-- specseed: repo-context for {kind}. USER-OWNED, safe to edit; the agent reads "
        "this every run for this route. Put repo-level structure + quick context here (where "
        "things live, build/test commands, gotchas). Empty by default. Do NOT restate what "
        "specseed already injects (target/engine boundary, git policy, action gates, the "
        "spec-read order) - the skill handles those. -->\n"
    )


def write_instruction_files(data_root: str | Path) -> list[Path]:
    """Seed the per-route repo-context stubs at ``instructions/<route>/repo.md``.

    Lives in the DATA ROOT (home), not the target. USER-OWNED: an existing file is
    never overwritten, so a refresh on every startup is safe. Returns paths newly
    created.
    """
    base = instructions_dir(data_root)
    written: list[Path] = []
    for route, kind in _ROUTE_KINDS.items():
        route_dir = base / route
        try:
            route_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        path = route_dir / "repo.md"
        if path.exists():
            continue
        try:
            path.write_text(_instruction_body(kind), encoding="utf-8")
            written.append(path)
        except OSError:
            pass
    return written


# User-owned custom-instruction routes ("" = global, applied to every route). The
# runtime only seeds an empty stub when absent and NEVER overwrites. The skill bundle
# tells the agent to read the matching file each run. Global -> instructions/custom.md;
# per route -> instructions/<route>/custom.md.
CUSTOM_INSTRUCTION_ROUTES = ("", "impl", "spec", "review", "ask")

_CUSTOM_STUB_HEADER = (
    "<!-- specseed: user-owned. Safe to edit. The agent reads this file every run for "
    "the {scope} route(s) and honors it. -->\n\n"
    "# Custom instructions ({scope})\n\n"
    "Put project-specific guidance for the agent here (conventions, extra steps, "
    "files to keep in sync, etc.). Leave empty for none.\n\n"
    "Do NOT restate things specseed already handles - target/engine boundary, git "
    "branch/commit policy, action gates, the scaffold, or reading the spec. Those "
    "are injected automatically; duplicating them only adds noise.\n"
)


def custom_instruction_file(data_root: str | Path, scope: str) -> Path:
    """``instructions/custom.md`` (global) or ``instructions/<route>/custom.md``."""
    base = instructions_dir(data_root)
    return (base / "custom.md") if not scope else (base / scope / "custom.md")


def write_custom_instruction_stubs(data_root: str | Path) -> list[Path]:
    """Seed empty user-owned custom-instruction files, create-if-absent.

    Never overwrites: an existing file is left alone. Returns paths newly created.
    """
    written: list[Path] = []
    for scope in CUSTOM_INSTRUCTION_ROUTES:
        path = custom_instruction_file(data_root, scope)
        if path.exists():
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _CUSTOM_STUB_HEADER.format(scope=scope or "all steps"), encoding="utf-8"
            )
            written.append(path)
        except OSError:
            pass
    return written


def scaffold_target(
    repo_root: str | Path,
    data_root: str | Path,
    primary_branch: str = "main",
) -> dict:
    """Make a target git-ready and seed the user-owned instruction stubs.

    The target itself gets ONLY a git repo (init + root commit if needed) - NOTHING
    else is written into it (no ``.specseed/``, no .gitignore line; all data lives in
    the home DATA ROOT). The instruction stubs land under ``instructions/<route>/`` in
    the data root. Runs on EVERY entry path (configure / add / startup repair).
    Best-effort throughout.
    """
    git_actions = ensure_git_repo(repo_root, primary_branch)
    return {
        "git": git_actions,
        "instructions": [str(p) for p in write_instruction_files(data_root)],
        "custom_instructions": [str(p) for p in write_custom_instruction_stubs(data_root)],
    }
