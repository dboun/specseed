#!/usr/bin/env python3
"""run.py - launch the specseed scheduler.

This is the documented way to "start things" after ``configure.py``. The main
thread only builds the Scheduler, installs signal handlers, starts the background
loop, and waits for a stop signal; agents run on their own threads, so Ctrl-C (or
``SIGTERM``, or a STOP command on the CONTROL post) stops the loop promptly and
cleanly.

Usage (run from the target repo root):

    python3 -m specseed_runtime.executing.run
    python3 -m specseed_runtime.executing.run --once
    python3 -m specseed_runtime.executing.run --interval 30

Only Python stdlib is used.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional


def _add_package_parent_to_path() -> None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "specseed_runtime").is_dir():
            sys.path.insert(0, str(parent / "src"))
            return
        if (parent / "specseed_runtime").exists():
            sys.path.insert(0, str(parent))
            return


_add_package_parent_to_path()

from specseed_runtime.db.database import Database
from specseed_runtime.executing.agent_runner import (
    AgentRunner,
    RunnerChains,
    build_runner_chains,
)
from specseed_runtime.executing import inflight
from specseed_runtime.executing import platform_log
from specseed_runtime.executing import runner_control
from specseed_runtime.executing.scheduler import Scheduler
from specseed_runtime.migrating.migrate import run_migrations
from specseed_runtime.storage_paths import SPECSEED_STORAGE_ENV, storage_db_path
from specseed_runtime.tracking.populate_defaults import populate_defaults
from specseed_runtime.tracking.resolve_remote import (
    default_storage_dir,
    load_config,
    load_remote_state,
    resolve_remote,
)


def build_scheduler(
    *,
    storage: Optional[str | Path] = None,
    repo_root: Optional[str | Path] = None,
    interval: Optional[float] = None,
    runner: Optional[AgentRunner] = None,
    db: Optional[Database] = None,
) -> Scheduler:
    storage_dir = Path(storage) if storage else default_storage_dir()
    platform_log.configure(storage_dir)
    # Storage migrates BEFORE anything reads it (config, queue db, trackers).
    applied = run_migrations(storage=storage_dir)
    if applied:
        platform_log.log_event("storage_migrated", storage=str(storage_dir), applied=applied)
    config = load_config(storage_dir)
    # Git is mandatory + the engine is off-limits: repair a non-git target and
    # refresh the identity guardrails before the loop. Skipped for the engine's
    # own checkout (target == dev repo) so we never rewrite its CLAUDE.md.
    _ensure_target_ready(Path(repo_root) if repo_root else Path.cwd(), config)
    platform_log.log_event(
        "scheduler_build",
        storage=str(storage_dir),
        repo_root=str(Path(repo_root) if repo_root else Path.cwd()),
        poll_interval=interval,
        runner_injected=runner is not None,
        db_injected=db is not None,
    )
    # A bare AgentRunner (e.g. a test double) is wrapped as a single-spec chain
    # for every function; otherwise build the configured per-function chains.
    runner_injected = runner is not None
    if runner is None:
        runner = build_runner_chains(config)
    elif not isinstance(runner, RunnerChains):
        runner = RunnerChains.single(runner)
    # Exactly one runner per storage. A live owner means THIS start is the
    # mistake (busy runner misread as dead) - refuse, or the reclaim below
    # would kill the live owner's in-flight agent.
    owner = runner_control.read_runner_status(storage_dir)
    owner_pid = owner.get("pid")
    if (
        owner_pid
        and int(owner_pid) != os.getpid()
        and runner_control.pid_is_runner(owner_pid)
    ):
        platform_log.log_event(
            "run_refused_storage_owned", owner_pid=owner_pid, storage=str(storage_dir)
        )
        raise RuntimeError(
            f"another runner (pid {owner_pid}) already owns {storage_dir}; "
            "stop it first (specseed stop) or wait for it to finish"
        )
    db = db or Database.instance(storage_db_path("specseed.db", storage_dir))
    # A prior runner may have died mid-task: kill its orphaned children and
    # return its in_progress rows to pending BEFORE the loop starts draining.
    inflight.reclaim(storage_dir, db)
    return Scheduler(
        db=db,
        runner=runner,
        config=config,
        storage=storage_dir,
        repo_root=Path(repo_root) if repo_root else Path.cwd(),
        poll_interval=interval,
        control_file=runner_control.control_file(storage_dir),
        status_file=runner_control.status_file(storage_dir),
        # Resume re-reads config (edits are gated on a paused runner). Only when
        # the runner came from config - never swap out an injected double.
        config_loader=None if runner_injected else (lambda: load_config(storage_dir)),
    )


def _ensure_target_ready(repo_root: Path, config: dict) -> None:
    """Best-effort startup repair: git-init + identity guardrails for the target.

    Never touches the engine's own checkout (target == dev repo). Failures are
    logged, never fatal - a scaffold hiccup must not stop the runner."""
    try:
        from specseed_runtime import registry

        if registry.is_dev() and repo_root.resolve() == registry.dev_root().resolve():
            return
    except Exception:
        pass
    try:
        from specseed_runtime.configuring import scaffold

        specseed_dir = config.get("specseed_dir") or ".specseed"
        primary_branch = config.get("specseed_primary_branch") or "main"
        ignore_specseed = config.get("gitignore_specseed_dir", True)
        result = scaffold.scaffold_target(
            repo_root, specseed_dir, primary_branch, ignore_specseed=ignore_specseed
        )
        if result.get("git"):
            platform_log.log_event(
                "target_git_initialized", repo_root=str(repo_root), actions=result["git"]
            )
    except Exception as exc:
        platform_log.log_event("target_scaffold_error", repo_root=str(repo_root), error=repr(exc))


def _backend_kind(remote_state: dict) -> str:
    """Map the configured remote (remote.json) to a ``populate_defaults`` kind."""
    remote_state = remote_state or {}
    if not remote_state.get("enabled"):
        return "remote_local"
    provider = remote_state.get("provider")
    if provider == "github":
        return "remote_github"
    if provider == "gitlab":
        return "remote_gitlab"
    raise ValueError(f"unsupported remote provider: {provider!r}")


def _seed_marker_file(storage: Path) -> Path:
    return Path(storage) / "seed_state.json"


def ensure_remote_seeded(storage: str | Path, remote_state: Optional[dict] = None) -> Optional[dict]:
    """Seed the configured remote once so the scheduler has something to poll.

    First run on a repo finds an empty remote (the local stand-in is an empty
    sqlite db), so a poll diffs nothing and no work is ever queued. This seeds
    the default labels and permanent posts (incl. the draft ``spec-change:adapt``
    post) by reusing ``populate_defaults`` against the resolved remote.

    Idempotent and non-destructive (``prune=False`` never deletes user content).
    A marker file keyed to ``(kind, repo)`` skips re-seeding on later launches and
    re-seeds when the remote changes. Returns the populate summary, or ``None``
    when seeding was skipped because the remote was already seeded.
    """
    storage = Path(storage)
    if remote_state is None:
        remote_state = load_remote_state(storage)
    kind = _backend_kind(remote_state)
    repo = (remote_state or {}).get("repo")
    desired = {"kind": kind, "repo": repo}

    marker = _seed_marker_file(storage)
    try:
        if json.loads(marker.read_text(encoding="utf-8")) == desired:
            platform_log.log_event("remote_seed_skipped", storage=str(storage), **desired)
            return None
    except (OSError, json.JSONDecodeError):
        pass

    platform_log.log_event("remote_seed_start", storage=str(storage), **desired)
    remote = resolve_remote(storage)
    summary = populate_defaults(kind, tracker=remote, prune=False)

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(desired, indent=2) + "\n", encoding="utf-8")
    posts = summary.get("default_posts", {})
    created = [title for title, info in posts.items() if info.get("created")]
    platform_log.log_event(
        "remote_seed_complete",
        storage=str(storage),
        backend=summary.get("backend"),
        ensured_labels=len(summary.get("ensured_labels", [])),
        default_posts=len(posts),
        created_posts=created,
    )
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the specseed scheduler.")
    parser.add_argument("--storage", default=None, help="override the storage dir")
    parser.add_argument("--repo-root", default=None, help="repo root the agent works in (default: cwd)")
    parser.add_argument("--interval", type=float, default=None, help="poll interval seconds")
    parser.add_argument("--once", action="store_true", help="run one control+sync+drain pass and exit")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    storage_dir = Path(args.storage) if args.storage else default_storage_dir()
    # Export the target's storage for every child process: agent runs and
    # generated apply.py call default_storage_dir() and must land HERE, not in
    # the engine repo's dev storage.
    os.environ[SPECSEED_STORAGE_ENV] = str(storage_dir.resolve())
    platform_log.configure(storage_dir)
    platform_log.log_event(
        "run_start",
        storage=str(storage_dir),
        repo_root=str(Path(args.repo_root) if args.repo_root else Path.cwd()),
        interval=args.interval,
        once=args.once,
    )
    try:
        applied = run_migrations(storage=storage_dir)
        if applied:
            platform_log.log_event("storage_migrated", storage=str(storage_dir), applied=applied)
            print(f"storage migrated (applied: {', '.join(applied)})")
        seeded = ensure_remote_seeded(storage_dir, load_remote_state(storage_dir))
        if seeded is not None:
            posts = seeded.get("default_posts", {})
            created = [title for title, info in posts.items() if info.get("created")]
            print(
                f"seeded {seeded['backend']}: {len(seeded.get('ensured_labels', []))} labels, "
                f"{len(posts)} permanent posts"
                + (f" (created: {', '.join(created)})" if created else " (already present)")
            )

        scheduler = build_scheduler(
            storage=storage_dir,
            repo_root=args.repo_root,
            interval=args.interval,
        )

        if args.once:
            summary = scheduler.run_once()
            platform_log.log_event("run_once_complete", summary=summary)
            print(json.dumps(summary, indent=2))
            return 0

        def _on_signal(signum, _frame):  # noqa: ANN001 - signal handler signature
            platform_log.log_event("signal_received", signum=signum)
            scheduler.request_stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _on_signal)
            except (ValueError, OSError):  # not the main thread / unsupported
                pass

        # A fresh foreground run overrides any stale pause/stop flag a prior
        # runner left behind, so the operator's intent (running) is honored.
        runner_control.resume_runner(storage_dir)
        scheduler.start()
        print(f"specseed scheduler started (interval={scheduler.poll_interval}s). Ctrl-C to stop.")
        try:
            while scheduler.is_running():
                time.sleep(0.5)
        except KeyboardInterrupt:
            platform_log.log_event("keyboard_interrupt")
            scheduler.request_stop()
        scheduler.stop(timeout=30)
        platform_log.log_event("run_stop", state=scheduler.state)
        print("specseed scheduler stopped.")
        return 0
    except Exception as exc:
        platform_log.log_event("run_error", error=repr(exc))
        raise


if __name__ == "__main__":
    sys.exit(main())
