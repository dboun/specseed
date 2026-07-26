"""inflight.py - which child processes a task run owns, durably.

A task run spawns subprocesses (agent CLIs, generated apply.py). If the runner
process dies hard (kill -9, power), two things leak: the task row stays
``in_progress`` forever, and the child - which survives its parent - keeps
mutating the repo with nobody supervising it.

This module is the ledger and the broom:

* ``record``/``clear`` - dispatch notes every spawned child pid (+ binary, for
  kill safety) in ``<storage>/inflight.json``, and removes it when the child is
  reaped normally.
* ``reclaim`` - called once at launcher startup, BEFORE the scheduler runs:
  kills every recorded pid that is still alive AND still runs the recorded
  binary (never a recycled pid), resets orphaned ``in_progress`` tasks to
  ``pending`` so they re-run, and wipes the ledger.

Only Python stdlib is used.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from specseed_runtime.executing import platform_log

INFLIGHT_FILE = "inflight.json"
_KILL_GRACE_S = 5.0

_lock = threading.Lock()


def _path(storage: str | Path) -> Path:
    return Path(storage) / INFLIGHT_FILE


def _load(storage: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(_path(storage).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(storage: str | Path, data: dict[str, Any]) -> None:
    path = _path(storage)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # the ledger is best-effort; never fail a run over it


def record(storage: Optional[str | Path], task_id: Any, pid: int, binary: str) -> None:
    """Note a live child of ``task_id``. Overwrites a prior child of the task."""
    if storage is None:
        return
    with _lock:
        data = _load(storage)
        data[str(task_id)] = {
            "pid": int(pid),
            "binary": str(binary or ""),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _save(storage, data)


def clear(storage: Optional[str | Path], task_id: Any) -> None:
    """Forget the child of ``task_id`` (it was reaped normally)."""
    if storage is None:
        return
    with _lock:
        data = _load(storage)
        if data.pop(str(task_id), None) is not None:
            _save(storage, data)


def _command_of(pid: int) -> Optional[str]:
    """The process's command line, or None when it cannot be read."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    cmd = (out.stdout or "").strip()
    return cmd or None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours to signal
    except OSError:
        return False


def _kill(pid: int) -> bool:
    """SIGTERM, grace, SIGKILL. True when the process is gone afterwards."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not _alive(pid)
    deadline = time.monotonic() + _KILL_GRACE_S
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.1)
    return not _alive(pid)


def reclaim(storage: str | Path, db: Any) -> dict[str, int]:
    """Kill recorded orphan children, requeue orphaned tasks, wipe the ledger.

    Run at launcher startup only - while no scheduler is draining, so every
    ``in_progress`` row is by definition an orphan of a dead runner.
    """
    summary = {"killed": 0, "stale": 0, "requeued": 0}
    with _lock:
        data = _load(storage)
        for task_id, entry in sorted(data.items()):
            pid = int(entry.get("pid", 0) or 0)
            binary = str(entry.get("binary") or "")
            if pid <= 0 or not _alive(pid):
                summary["stale"] += 1
                continue
            command = (_command_of(pid) or "").lower()
            base = os.path.basename(binary).lower()
            # macOS reports python children as ".../Python"; match loosely but
            # case-insensitively, and let "python3.11" match a bare "python".
            if base and base not in command and not (
                base.startswith("python") and "python" in command
            ):
                # pid recycled by an unrelated process - never signal it.
                summary["stale"] += 1
                platform_log.log_event(
                    "inflight_pid_recycled", task_id=task_id, pid=pid, binary=binary
                )
                continue
            killed = _kill(pid)
            summary["killed"] += 1
            platform_log.log_event(
                "inflight_orphan_killed",
                task_id=task_id,
                pid=pid,
                binary=binary,
                gone=killed,
            )
        _save(storage, {})

    for task_id in db.reset_in_progress():
        summary["requeued"] += 1
        platform_log.log_event("inflight_task_requeued", task_id=task_id)

    if any(summary.values()):
        platform_log.log_event("inflight_reclaim", **summary)
    return summary
