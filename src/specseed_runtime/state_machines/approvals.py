"""approvals.py - the APR-NNNN approval token: format + parsing (pure, stdlib).

specseed's worker is NOT finished until it asks for explicit human approval. It
asks by stamping a stable ``APR-NNNN`` token onto a request post (an
"approval request") and parking that post ``awaiting_approval``. A human then
approves the token, either by commenting ``approve APR-0001`` or by reacting 👍
to the post; the deterministic approval system (``executing/advance`` +
``state_machines/base``) resolves the gate, with no agent run.

This module owns only the token VOCABULARY so both the worker-facing helpers
(``executing/approvals``) and the state machine can share it without a circular
import (``state_machines`` must never import ``executing``). Runtime concerns
(allocating the next id from a counter file) live in ``executing/approvals``.

Token shape: ``APR-`` + a zero-padded, >=4-digit, monotonically allocated number,
e.g. ``APR-0001``. Case-insensitive on read, upper-cased on write.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from specseed_runtime.platform_identity import platform_comment

# A bare token anywhere in text, e.g. in `approve APR-0007` or in a marker.
APR_RE = re.compile(r"\bAPR-(\d{4,})\b", re.IGNORECASE)

# The hidden marker the worker stamps on an approval-request comment so the
# requested token can be recovered from the conversation without a side channel.
APPROVAL_REQUEST_MARKER = "specseed:approval-request"
_REQUEST_MARKER_RE = re.compile(
    r"<!--\s*" + re.escape(APPROVAL_REQUEST_MARKER) + r"\s+(APR-\d{4,})\s*-->",
    re.IGNORECASE,
)


def format_apr(n: int) -> str:
    """Render an integer as a canonical ``APR-NNNN`` token (>=4 digits)."""
    return f"APR-{int(n):04d}"


def apr_number(token: str) -> int | None:
    """Return the integer in an ``APR-NNNN`` token, or None if it is not one."""
    match = APR_RE.fullmatch(token.strip()) if token else None
    return int(match.group(1)) if match else None


def apr_ids_in_text(text: str | None) -> list[str]:
    """Return every ``APR-NNNN`` token in ``text``, canonicalised + de-duped."""
    if not text:
        return []
    seen: list[str] = []
    for match in APR_RE.finditer(text):
        token = f"APR-{int(match.group(1)):04d}"
        if token not in seen:
            seen.append(token)
    return seen


def _body(item: Any) -> str | None:
    if isinstance(item, dict):
        return item.get("body")
    return getattr(item, "body", None)


def requested_apr_ids(conversation: Iterable[Any] | None) -> list[str]:
    """Tokens the worker stamped as approval requests across the conversation.

    Only ids carried by an ``<!-- specseed:approval-request APR-NNNN -->`` marker
    count, so an id merely mentioned in prose is not treated as a live gate.
    """
    ids: list[str] = []
    for item in conversation or []:
        body = _body(item)
        if not body:
            continue
        for match in _REQUEST_MARKER_RE.finditer(str(body)):
            token = match.group(1).upper()
            if token not in ids:
                ids.append(token)
    return ids


def approval_request_comment(apr_id: str, summary: str, config: dict[str, Any] | None = None) -> str:
    """The standard approval-request comment body the worker posts.

    Carries the marker (so the token is recoverable) and tells the human the two
    ways to approve. Kept deterministic so it reads the same every run. ``config``
    drives the ``specseed: `` prefix (omitted for a distinct bot account).
    """
    apr_id = apr_id.strip().upper()
    return platform_comment(
        f"**Approval required: `{apr_id}`**\n\n"
        f"{summary.strip()}\n\n"
        f"This work is **not started** until a human approves. To approve, either:\n"
        f"- comment `approve {apr_id}` on this post, or\n"
        f"- react 👍 (thumbs up) to this post.\n\n"
        f"To reject, comment `reject {apr_id}` or react 👎. Until then this request "
        f"stays `spec-change:status:awaiting_approval`.\n\n"
        f"<!-- {APPROVAL_REQUEST_MARKER} {apr_id} -->",
        config,
    )
