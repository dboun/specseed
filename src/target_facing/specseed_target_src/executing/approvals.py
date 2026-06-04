"""approvals.py - worker-facing approval helpers (runtime: id allocation).

The token VOCABULARY (``APR-NNNN`` format, marker, request-comment text, parsing)
lives in ``state_machines/approvals`` so the state machine can share it without a
circular import. This module adds the one runtime concern that belongs to the
executing/worker side: allocating the next monotonic ``APR`` id from a small
counter file under storage, so two requests never collide on a token.

The specseed worker calls :func:`next_apr_id` while it builds an approval request,
then embeds the returned token in its approval-request comment (see
``state_machines.approvals.approval_request_comment``).

Only Python stdlib is used.
"""

from __future__ import annotations

import fcntl
from pathlib import Path

from specseed_target_src.state_machines.approvals import (  # re-export for the worker
    APPROVAL_REQUEST_MARKER,
    APR_RE,
    apr_ids_in_text,
    apr_number,
    approval_request_comment,
    format_apr,
    requested_apr_ids,
)

__all__ = [
    "APPROVAL_REQUEST_MARKER",
    "APR_RE",
    "apr_ids_in_text",
    "apr_number",
    "approval_request_comment",
    "format_apr",
    "requested_apr_ids",
    "next_apr_id",
    "counter_path",
]

_COUNTER_NAME = ".apr_counter"


def counter_path(storage: str | Path) -> Path:
    """The APR counter file under ``<storage>/spec-change/``."""
    return Path(storage) / "spec-change" / _COUNTER_NAME


def next_apr_id(storage: str | Path) -> str:
    """Allocate and persist the next ``APR-NNNN`` token.

    Increments a counter file under storage atomically (flock), so concurrent
    workers never hand out the same token. The first allocation returns
    ``APR-0001``.
    """
    path = counter_path(storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    # r+ needs the file to exist; create it empty first if missing.
    if not path.exists():
        path.write_text("0", encoding="utf-8")
    with open(path, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            raw = handle.read().strip()
            try:
                current = int(raw)
            except ValueError:
                current = 0
            nxt = current + 1
            handle.seek(0)
            handle.truncate()
            handle.write(str(nxt))
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return format_apr(nxt)
