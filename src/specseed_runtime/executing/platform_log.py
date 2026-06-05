"""Append-only platform activity log for specseed orchestration.

The scheduler and queue plumbing should be explainable after the fact without
requiring a foreground terminal. This module writes compact JSON lines to
``<specseed_dir>/storage/platform.log`` once configured by ``run.py`` or a
``Scheduler`` created with a storage directory.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


_lock = threading.RLock()
_path: Optional[Path] = None


def configure(storage: str | Path) -> Path:
    """Set the destination log file to ``Path(storage) / "platform.log"``."""
    global _path
    with _lock:
        storage_path = Path(storage)
        _path = storage_path / "platform.log"
        storage_path.mkdir(parents=True, exist_ok=True)
        return _path


def current_path() -> Optional[Path]:
    """Return the configured log path, or ``None`` before configuration."""
    with _lock:
        return _path


def log_event(event: str, **fields: Any) -> None:
    """Append one timestamped event.

    Logging is best-effort by design: platform observability should never make
    an agent run fail.
    """
    with _lock:
        path = _path
    if path is None:
        return

    record = {
        "ts": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "event": event,
        **fields,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    except OSError:
        pass
