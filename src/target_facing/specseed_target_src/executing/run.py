#!/usr/bin/env python3
"""run.py - launch the specseed scheduler.

This is the documented way to "start things" after ``configure.py``. The main
thread only builds the Scheduler, installs signal handlers, starts the background
loop, and waits for a stop signal; agents run on their own threads, so Ctrl-C (or
``SIGTERM``, or a STOP command on the CONTROL post) stops the loop promptly and
cleanly.

Usage (run from the target repo root):

    python3 -m specseed_target_src.executing.run
    python3 -m specseed_target_src.executing.run --once
    python3 -m specseed_target_src.executing.run --interval 30

Only Python stdlib is used.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Optional


def _add_package_parent_to_path() -> None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "target_facing").exists():
            sys.path.insert(0, str(parent / "src" / "target_facing"))
            return
        if (parent / "specseed_target_src").exists():
            sys.path.insert(0, str(parent))
            return


_add_package_parent_to_path()

from specseed_target_src.db.database import Database
from specseed_target_src.executing.agent_runner import (
    AgentRunner,
    build_runner,
)
from specseed_target_src.executing import platform_log
from specseed_target_src.executing.scheduler import Scheduler
from specseed_target_src.tracking.populate_defaults import populate_defaults
from specseed_target_src.tracking.resolve_remote import (
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
    config = load_config(storage_dir)
    platform_log.log_event(
        "scheduler_build",
        storage=str(storage_dir),
        repo_root=str(Path(repo_root) if repo_root else Path.cwd()),
        poll_interval=interval,
        runner_injected=runner is not None,
        db_injected=db is not None,
    )
    return Scheduler(
        db=db or Database.instance(),
        runner=runner or build_runner(config),
        config=config,
        storage=storage_dir,
        repo_root=Path(repo_root) if repo_root else Path.cwd(),
        poll_interval=interval,
    )


def _backend_kind(config: dict) -> str:
    """Map the configured backend to a ``populate_defaults`` backend kind."""
    backend = config.get("backend") or {}
    if not backend.get("enabled"):
        return "remote_local"
    provider = backend.get("provider")
    if provider == "github":
        return "remote_github"
    if provider == "gitlab":
        return "remote_gitlab"
    raise ValueError(f"unsupported backend provider: {provider!r}")


def _seed_marker_file(storage: Path) -> Path:
    return Path(storage) / "seed_state.json"


def ensure_remote_seeded(storage: str | Path, config: dict) -> Optional[dict]:
    """Seed the configured remote once so the scheduler has something to poll.

    First run on a repo finds an empty remote (the local stand-in is an empty
    sqlite db), so a poll diffs nothing and no work is ever queued. This seeds
    the default labels and permanent posts (incl. the draft ``spec-change:adapt``
    post) by reusing ``populate_defaults`` against the resolved remote.

    Idempotent and non-destructive (``prune=False`` never deletes user content).
    A marker file keyed to ``(kind, repo)`` skips re-seeding on later launches and
    re-seeds when the backend changes. Returns the populate summary, or ``None``
    when seeding was skipped because the backend was already seeded.
    """
    storage = Path(storage)
    kind = _backend_kind(config)
    repo = (load_remote_state(storage) or {}).get("repo")
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
    platform_log.configure(storage_dir)
    platform_log.log_event(
        "run_start",
        storage=str(storage_dir),
        repo_root=str(Path(args.repo_root) if args.repo_root else Path.cwd()),
        interval=args.interval,
        once=args.once,
    )
    try:
        seeded = ensure_remote_seeded(storage_dir, load_config(storage_dir))
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
