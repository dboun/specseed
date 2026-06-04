#!/usr/bin/env python3
"""run.py - launch the specseed scheduler.

This is the documented way to "start things" after ``configure.py``. The main
thread only builds the Scheduler, installs signal handlers, starts the background
loop, and waits for a stop signal; agents run on their own threads, so Ctrl-C (or
``SIGTERM``, or a STOP command on the CONTROL post) stops the loop promptly and
cleanly.

Usage (run from the target repo root):

    python3 -m src.target_facing.specseed_target_src.executing.run
    python3 -m src.target_facing.specseed_target_src.executing.run --once
    python3 -m src.target_facing.specseed_target_src.executing.run --interval 30

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

from src.target_facing.specseed_target_src.db.database import Database
from src.target_facing.specseed_target_src.executing.agent_runner import (
    AgentRunner,
    ClaudeAgentRunner,
)
from src.target_facing.specseed_target_src.executing.scheduler import Scheduler
from src.target_facing.specseed_target_src.tracking.resolve_remote import (
    default_storage_dir,
    load_config,
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
    config = load_config(storage_dir)
    return Scheduler(
        db=db or Database.instance(),
        runner=runner or ClaudeAgentRunner(),
        config=config,
        storage=storage_dir,
        repo_root=Path(repo_root) if repo_root else Path.cwd(),
        poll_interval=interval,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the specseed scheduler.")
    parser.add_argument("--storage", default=None, help="override the storage dir")
    parser.add_argument("--repo-root", default=None, help="repo root the agent works in (default: cwd)")
    parser.add_argument("--interval", type=float, default=None, help="poll interval seconds")
    parser.add_argument("--once", action="store_true", help="run one control+sync+drain pass and exit")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    scheduler = build_scheduler(
        storage=args.storage,
        repo_root=args.repo_root,
        interval=args.interval,
    )

    if args.once:
        summary = scheduler.run_once()
        print(json.dumps(summary, indent=2))
        return 0

    def _on_signal(signum, _frame):  # noqa: ANN001 - signal handler signature
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
        scheduler.request_stop()
    scheduler.stop(timeout=30)
    print("specseed scheduler stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
