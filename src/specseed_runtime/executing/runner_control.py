"""runner_control.py - drive a separate runner process without owning it.

Each repo's runner is its own ``specseed run`` process (see registry.py). Nobody
holds its handle: the CLI and the web service command it by writing a small
control file in the repo's storage, and observe it by reading a heartbeat file
the running scheduler stamps each tick. Liveness = the pid is alive AND the
heartbeat is fresh.

Two files in ``<storage>/``:

    control.json   desired state the operator wants: running / paused / stopped
    runner.json    heartbeat the scheduler writes: pid, state, queue counts, ...

Only Python stdlib is used.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

RUNNING = "running"
PAUSED = "paused"
STOPPED = "stopped"
DESIRED = (RUNNING, PAUSED, STOPPED)

# A heartbeat older than this (seconds) means the runner is gone even if a stale
# pid happens to be alive. Comfortably above the scheduler heartbeat interval.
STALE_AFTER_S = 30.0


def control_file(storage: str | Path) -> Path:
    return Path(storage) / "control.json"


def status_file(storage: str | Path) -> Path:
    return Path(storage) / "runner.json"


def runner_log_file(storage: str | Path) -> Path:
    return Path(storage) / "runner.out"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
# control file (operator -> runner)
# --------------------------------------------------------------------------- #
def read_desired(storage: str | Path) -> Optional[str]:
    doc = _read_json(control_file(storage))
    desired = (doc or {}).get("desired")
    return desired if desired in DESIRED else None


def write_command(storage: str | Path, desired: str) -> Path:
    if desired not in DESIRED:
        raise ValueError(f"desired must be one of {DESIRED}, got {desired!r}")
    path = control_file(storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = _read_json(path) or {}
    seq = int(prev.get("seq", 0)) + 1
    path.write_text(
        json.dumps({"desired": desired, "seq": seq, "at": _now()}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# heartbeat (runner -> operator)
# --------------------------------------------------------------------------- #
def write_runner_status(storage: str | Path, status: dict[str, Any]) -> None:
    path = status_file(storage)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"pid": os.getpid(), "updated_at": _now(), **status}
        path.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    except OSError:
        pass


def is_pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def pid_is_runner(pid: Optional[int]) -> bool:
    """Alive AND its command line looks like a specseed runner (not a recycled pid)."""
    if not is_pid_alive(pid):
        return False
    try:
        out = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return True  # cannot inspect: assume it is ours, never start over it
    command = (out.stdout or "").strip().lower()
    return "specseed" in command if command else True


def _heartbeat_fresh(updated_at: Optional[str]) -> bool:
    if not updated_at:
        return False
    try:
        stamp = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    return age <= STALE_AFTER_S


def read_runner_status(storage: str | Path) -> dict[str, Any]:
    """Return the heartbeat augmented with liveness, or a stopped placeholder."""
    doc = _read_json(status_file(storage))
    desired = read_desired(storage)
    if doc is None:
        return {"state": STOPPED, "alive": False, "pid": None, "desired": desired}
    pid = doc.get("pid")
    alive = is_pid_alive(pid) and _heartbeat_fresh(doc.get("updated_at"))
    out = dict(doc)
    out["alive"] = alive
    out["desired"] = desired
    if not alive and out.get("state") not in (STOPPED,):
        # process vanished without a clean stop
        out["state"] = STOPPED
    return out


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #
def _launcher_path() -> Path:
    # executing/ -> specseed_runtime/ -> specseed.py
    return Path(__file__).resolve().parents[1] / "specseed.py"


def start_runner(
    record: dict[str, Any],
    *,
    interval: Optional[float] = None,
    python: Optional[str] = None,
) -> dict[str, Any]:
    """Spawn a detached ``specseed run`` for a registry record.

    No-op (returns current status) if a live runner already exists. The child is
    fully detached (own session) so it outlives the CLI/web process that started
    it; its stdout/stderr go to ``runner.out``.
    """
    storage = record["storage"]
    current = read_runner_status(storage)
    if current.get("alive"):
        return current
    # A stale heartbeat with a LIVE runner pid means busy, not dead (e.g. a long
    # agent run before heartbeats kept up). Starting a second runner over the
    # same storage kills the live agent at startup reclaim - never do it.
    if pid_is_runner(current.get("pid")):
        busy = dict(current)
        busy["state"] = "busy"
        busy["alive"] = True
        return busy

    Path(storage).mkdir(parents=True, exist_ok=True)
    write_command(storage, RUNNING)

    argv = [
        python or sys.executable,
        str(_launcher_path()),
        "run",
        "--target",
        record["target"],
        "--specseed-dir",
        record.get("specseed_dir", ".specseed"),
    ]
    if interval is not None:
        argv += ["--interval", str(interval)]

    log = runner_log_file(storage).open("a", encoding="utf-8")
    log.write(f"\n--- runner start {_now()} ---\n")
    log.flush()
    kwargs: dict[str, Any] = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL}
    if hasattr(os, "setsid"):
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)  # noqa: S603 - trusted launcher argv
    return {"state": RUNNING, "alive": True, "pid": proc.pid, "desired": RUNNING}


def pause_runner(storage: str | Path) -> Path:
    return write_command(storage, PAUSED)


def resume_runner(storage: str | Path) -> Path:
    return write_command(storage, RUNNING)


def stop_runner(storage: str | Path, *, signal_pid: bool = True) -> dict[str, Any]:
    """Ask the runner to stop. Writes the control flag and, optionally, SIGTERMs
    the pid for a prompt exit. Returns the last known status."""
    write_command(storage, STOPPED)
    status = read_runner_status(storage)
    if signal_pid and status.get("alive") and status.get("pid"):
        try:
            os.kill(int(status["pid"]), 15)
        except (ProcessLookupError, PermissionError, OSError, ValueError):
            pass
    return status
