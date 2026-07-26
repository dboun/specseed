"""agent_sessions.py - remember a provider conversation id per post (continuity).

The agent no longer re-reads the tracker every turn; the runtime injects the post
thread into the prompt. To ALSO let the agent keep its own working memory across
turns, each run records the provider's session/conversation id keyed by the post id.
The next turn on that post passes it as ``--resume`` so the agent continues the same
conversation; if no id exists (first turn, or a different provider that can't resume)
the injected thread seeds full context instead.

Stored as a small JSON map in ``runtime/sessions.json`` under the data root. Worker
threads on different posts can run concurrently, so the read-modify-write in
``remember`` is guarded by an flock. Only Python stdlib is used.
"""

from __future__ import annotations

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from specseed_runtime.storage_paths import sessions_file


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def resume_id_for(storage: str | Path, post_id: object, provider: Optional[str] = None) -> Optional[str]:
    """The stored session id for ``post_id``, or None. When ``provider`` is given,
    only return an id recorded for that SAME provider (a resume id is provider-bound).
    """
    if post_id in (None, ""):
        return None
    rec = _load(sessions_file(storage)).get(str(post_id))
    if not isinstance(rec, dict):
        return None
    if provider and rec.get("provider") and rec.get("provider") != provider:
        return None
    sid = rec.get("session_id")
    return str(sid) if sid else None


def remember(
    storage: str | Path, post_id: object, session_id: Optional[str], provider: Optional[str] = None
) -> None:
    """Record (or clear) the session id for ``post_id``. Atomic under an flock."""
    if post_id in (None, ""):
        return
    path = sessions_file(storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    with open(path, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(handle.read() or "{}")
                if not isinstance(data, dict):
                    data = {}
            except ValueError:
                data = {}
            if session_id:
                data[str(post_id)] = {
                    "session_id": str(session_id),
                    "provider": provider,
                    "updated_at": _now(),
                }
            else:
                data.pop(str(post_id), None)
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(data, indent=2) + "\n")
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
