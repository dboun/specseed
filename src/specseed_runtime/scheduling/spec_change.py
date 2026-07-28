"""
spec_change.py - queue spec-change plan/script application.

When the specseed skill finishes a spec-change route (adopt / adapt / tweak /
inject / plan-next-sprint) it has produced two things:

  1. optional edits to the local spec docs under ``<data_root>/spec/``, and
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

from specseed_runtime.db.database import Database
from specseed_runtime.storage_paths import spec_change_root as _spec_change_root

# The queue action a spec-change run is enqueued under. The scheduler
# (``executing/scheduler.py``) claims tasks with this action and hands them to
# ``executing/dispatch.run_spec_change_script``, which runs
# payload["dir"]/payload["script"] as a gated subprocess (PYTHONPATH=repo root so
# it can import ``src.*``), honoring config.permissions.remote.* and cooperative
# cancellation, then complete()/requeue()s the task.
SPEC_CHANGE_ACTION = "run_spec_change_script"

# The queue action a *plan-first proposal* is enqueued under. Deterministic, no
# agent, no script: the executor (``executing/dispatch.propose_spec_change``)
# reads the request's ``plan.json``, posts the human-readable ``plan_summary`` +
# the ``APR-NNNN`` approval request on the request post, and parks it
# ``spec-change:status:awaiting_approval``. NO remote work posts are created -
# that is deferred to ``apply.py``, which the runtime enqueues only once a human
# approves (``advance.resolve_spec_change_request``). This is the gate: nothing
# is created on the tracker before the plan is approved.
SPEC_CHANGE_PROPOSE_ACTION = "propose_spec_change"

# The queue action for applying normal ``plan.json`` remote mutations in code.
# Generated ``apply.py`` remains an explicit escape hatch; ordinary creates / edits /
# labels / comments / closes / deletes should use this action instead.
SPEC_CHANGE_PLAN_ACTION = "apply_spec_change_plan"

# Default script filename the skill may write into each spec-change dir for an
# escape-hatch executor. Normal runs do not need this file.
DEFAULT_SCRIPT_NAME = "apply.py"


def spec_change_root(storage: Optional[str | Path] = None) -> Path:
    """``<data_root>/spec-change`` - parent of every per-request spec-change dir."""
    return _spec_change_root(storage)


def spec_change_dir(request_id: str, storage: Optional[str | Path] = None) -> Path:
    """``storage/spec-change/<request_id>`` - one dir per spec-change request.

    ``request_id`` is normally the id of the remote spec-change post that
    triggered the route, so a request maps to a stable, inspectable folder.
    """
    return spec_change_root(storage) / str(request_id)


def spec_change_spec_dir(request_id: str, storage: Optional[str | Path] = None) -> Path:
    """``storage/spec-change/<request_id>/spec`` - the STAGED spec for a request.

    Plan-first: a spec-change worker never edits live ``<data_root>/spec/`` in
    place. It writes each created/edited doc here, mirroring the same relative path
    it has under ``spec/`` (so ``spec/sad.md`` stages at ``.../spec/sad.md``). The
    runtime promotes these into the live tree ONLY on approval. An unapproved or
    buggy run therefore cannot corrupt the real spec.
    """
    return spec_change_dir(request_id, storage) / "spec"


def staged_spec_files(
    request_id: str, storage: Optional[str | Path] = None
) -> list[Path]:
    """Every staged spec file for a request (recursive), or ``[]`` if none.

    Used by the runtime to (a) classify a run as work/spec-touching - so it must
    be gated - and (b) promote the files into live ``spec/`` on approval.
    """
    staged = spec_change_spec_dir(request_id, storage)
    if not staged.is_dir():
        return []
    return sorted(p for p in staged.rglob("*") if p.is_file())


def enqueue_spec_change_run(
    script_path: str | Path,
    request_id: Optional[str | int] = None,
    route: Optional[str] = None,
    db: Optional[Database] = None,
    storage: Optional[str | Path] = None,
    close_request: bool = False,
) -> int:
    """Enqueue a generated spec-change script for the executor to run.

    ``script_path`` is the script the skill just wrote (absolute, or relative).
    ``request_id`` is the triggering remote post id (also the spec-change dir
    name); ``route`` is the spec-change route name. Returns the queued ``task_id``.

    ``close_request`` marks THIS run as the one that ends the request: the
    approval-path ``apply.py`` (enqueued once, on approval). The executor closes
    the request post itself after a successful run rather than trusting the
    agent-emitted ``plan.json.closes`` to list it. Mechanical runs (clarification
    rounds, sprint shuffles) leave it ``False`` so they never close the request.

    With a ``request_id`` the script's home is ALWAYS ``spec_change_dir(request_id)``;
    only the basename of ``script_path`` is trusted. Agents may pass a path with a
    leading ``spec-change/<id>/apply.py`` segment which, joined naively under the
    spec-change dir, doubles the prefix and enqueues a nonexistent script.
    So: try the literal join, snap to ``<dir>/<basename>`` when the join is missing,
    and fail loud here (not at run time, where it would retry forever) if the
    script still does not exist.

    The payload carries ``dir``/``script`` as absolute strings so the executor
    needs no path context of its own, plus ``route`` and ``request_id`` for
    bookkeeping. ``post_id`` on the task is the triggering spec-change post, so
    teardown/supersession in ``sync_to_db`` can find it.
    """
    script = Path(script_path)
    if not script.is_absolute():
        if request_id is None:
            raise ValueError("relative script_path requires request_id to locate its dir")
        home = spec_change_dir(request_id, storage)
        candidate = home / script
        if not candidate.exists():
            candidate = home / script.name
        script = candidate
    elif not script.exists() and request_id is not None:
        # Absolute but wrong (e.g. a doubled prefix): snap to the canonical dir.
        snapped = spec_change_dir(request_id, storage) / script.name
        if snapped.exists():
            script = snapped
    script = script.resolve()
    if not script.is_file():
        raise ValueError(
            "spec-change script not found: {0} (request_id={1}). Write apply.py into "
            "its spec-change dir before enqueueing.".format(script, request_id)
        )

    payload = {
        "dir": str(script.parent),
        "script": script.name,
        "route": route,
        "request_id": None if request_id is None else str(request_id),
        "close_request": bool(close_request),
    }
    db = db or Database.instance()
    return db.enqueue(
        SPEC_CHANGE_ACTION,
        post_id=request_id,
        payload=payload,
    )


def enqueue_spec_change_plan(
    request_id: str | int,
    route: Optional[str] = None,
    db: Optional[Database] = None,
    close_request: bool = False,
) -> int:
    """Enqueue the runtime JSON executor for ``plan.json``.

    This is the normal spec-change apply path. The executor reads
    ``storage/spec-change/<request_id>/plan.json`` and applies its supported
    remote mutations through the tracking contract. ``close_request`` marks the
    approval-path apply that should close the request after success.
    """
    db = db or Database.instance()
    return db.enqueue(
        SPEC_CHANGE_PLAN_ACTION,
        post_id=request_id,
        payload={
            "request_id": str(request_id),
            "route": route,
            "close_request": bool(close_request),
        },
    )


def enqueue_spec_change_propose(
    request_id: str | int,
    route: Optional[str] = None,
    db: Optional[Database] = None,
) -> int:
    """Enqueue a plan-first PROPOSAL for a spec-change request. Returns the task id.

    A work-creating / spec-settling run emits ``plan.json`` (with ``plan_summary``
    + ``apr``) and ``apply.py``, then calls THIS instead of running ``apply.py``.
    The executor posts the summary + approval request and parks the request; the
    deferred ``apply.py`` runs only on approval. ``post_id`` is the request so
    sync teardown/supersession can find the task.
    """
    db = db or Database.instance()
    return db.enqueue(
        SPEC_CHANGE_PROPOSE_ACTION,
        post_id=request_id,
        payload={"request_id": str(request_id), "route": route},
    )
