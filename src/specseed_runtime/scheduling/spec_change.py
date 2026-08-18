"""
spec_change.py - queue spec-change plan application.

Spec-change workers stage spec edits and write ``plan.json`` under
``storage/spec-change/<id>/``. They never enqueue or run anything. The runtime
uses these helpers to queue either a proposal post or the JSON plan executor.

Only Python stdlib is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from specseed_runtime.db.database import Database
from specseed_runtime.storage_paths import spec_change_root as _spec_change_root

# The queue action a *plan-first proposal* is enqueued under. Deterministic, no
# agent, no script: the executor (``executing/dispatch.propose_spec_change``)
# reads the request's ``plan.json``, posts the human-readable ``plan_summary`` +
# the ``APR-NNNN`` approval request on the request post, and parks it
# ``spec-change:status:awaiting_approval``. NO remote work posts are created -
# that happens only when approval enqueues ``apply_spec_change_plan``.
SPEC_CHANGE_PROPOSE_ACTION = "propose_spec_change"

# The queue action for applying ``plan.json`` remote mutations in code.
SPEC_CHANGE_PLAN_ACTION = "apply_spec_change_plan"


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
    + ``apr``). The executor posts the summary + approval request and parks the
    request. ``post_id`` is the request so sync teardown/supersession can find the task.
    """
    db = db or Database.instance()
    return db.enqueue(
        SPEC_CHANGE_PROPOSE_ACTION,
        post_id=request_id,
        payload={"request_id": str(request_id), "route": route},
    )
