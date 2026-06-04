"""
spec_change.py - queue a generated spec-change script for execution.

When the specseed skill finishes a spec-change route (adopt / adapt / tweak /
inject / plan-next-sprint) it has produced two things:

  1. optional edits to the local spec docs under ``<specseed_dir>/spec/``, and
  2. a self-contained Python script under
     ``storage/spec-change/<id>/apply.py`` that mutates the **remote** posts to
     match (create/update/close epics, tickets, issues; labels; comments;
     dashboards). The script talks to whatever remote ``configure.py`` selected,
     via ``tracking/resolve_remote.py``.

The skill does NOT run that script itself. It enqueues a task here describing
*what to run*; a scheduler drains the queue and executes it. This keeps the
remote-mutating side effect behind the same approval/poll machinery as the rest
of the system.

    skill  --writes-->  storage/spec-change/<id>/apply.py
           --enqueues-->  Database task (action = run_spec_change_script)
                              |
                              v
                     executing/ scheduler drains it
                     (dispatch.run_spec_change_script)

The conventions (action name + payload shape) are defined here so the executor
(``executing/dispatch.py``) and the skill agree. Only Python stdlib is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.target_facing.specseed_target_src.db.database import Database
from src.target_facing.specseed_target_src.tracking.resolve_remote import default_storage_dir

# The queue action a spec-change run is enqueued under. The scheduler
# (``executing/scheduler.py``) claims tasks with this action and hands them to
# ``executing/dispatch.run_spec_change_script``, which runs
# payload["dir"]/payload["script"] as a gated subprocess (PYTHONPATH=repo root so
# it can import ``src.*``), honoring config.permissions.remote.* and cooperative
# cancellation, then complete()/requeue()s the task.
SPEC_CHANGE_ACTION = "run_spec_change_script"

# Default script filename the skill writes into each spec-change dir.
DEFAULT_SCRIPT_NAME = "apply.py"


def spec_change_root(storage: Optional[str | Path] = None) -> Path:
    """``storage/spec-change`` - parent of every per-request spec-change dir."""
    base = Path(storage) if storage else default_storage_dir()
    return base / "spec-change"


def spec_change_dir(request_id: str, storage: Optional[str | Path] = None) -> Path:
    """``storage/spec-change/<request_id>`` - one dir per spec-change request.

    ``request_id`` is normally the id of the remote spec-change post that
    triggered the route, so a request maps to a stable, inspectable folder.
    """
    return spec_change_root(storage) / str(request_id)


def enqueue_spec_change_run(
    script_path: str | Path,
    request_id: Optional[str | int] = None,
    route: Optional[str] = None,
    db: Optional[Database] = None,
    storage: Optional[str | Path] = None,
) -> int:
    """Enqueue a generated spec-change script for the executor to run.

    ``script_path`` is the script the skill just wrote (absolute, or relative to
    its spec-change dir). ``request_id`` is the triggering remote post id (also
    the spec-change dir name); ``route`` is the spec-change route name. Returns
    the queued ``task_id``.

    The payload carries ``dir``/``script`` as absolute strings so the executor
    needs no path context of its own, plus ``route`` and ``request_id`` for
    bookkeeping. ``post_id`` on the task is the triggering spec-change post, so
    teardown/supersession in ``sync_to_db`` can find it.
    """
    script = Path(script_path)
    if not script.is_absolute():
        if request_id is None:
            raise ValueError("relative script_path requires request_id to locate its dir")
        script = spec_change_dir(request_id, storage) / script
    script = script.resolve()

    payload = {
        "dir": str(script.parent),
        "script": script.name,
        "route": route,
        "request_id": None if request_id is None else str(request_id),
    }
    db = db or Database.instance()
    return db.enqueue(
        SPEC_CHANGE_ACTION,
        post_id=request_id,
        payload=payload,
    )
