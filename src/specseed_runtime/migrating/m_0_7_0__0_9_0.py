"""
m_0_7_0__0_9_0.py - git is mandatory + stricter review default.

0.9.0 makes two config-shape changes:

1. ``permissions.git.enabled`` is GONE. Git is now mandatory (the target is
   git-initialized at configure/startup), so the switch is meaningless. Drop the
   key; ``merge_to_dev_branch`` stays.
2. ``review.confidence_threshold`` default rose 0.75 -> 0.95. Bump it ONLY when the
   stored value is the old default (0.75) - a user's custom threshold is preserved.

Storage-only edit of ``configuration.json``. The identity guardrails (instruction
files + CLAUDE.md/AGENTS.md router) need the repo root, so they are (re)written on
every startup by ``executing/run.py`` rather than here.

Idempotent: a missing/already-migrated config is a no-op. Only Python stdlib.
"""

from __future__ import annotations

import json
from pathlib import Path

FROM = "0.7.0"
TO = "0.9.0"

_CONFIG = "configuration.json"
_OLD_DEFAULT_CONFIDENCE = 0.75
_NEW_DEFAULT_CONFIDENCE = 0.95


def run(storage: str | Path, specseed_dir: str | Path) -> list[Path]:
    """Apply the hop. Returns the paths it changed."""
    storage = Path(storage)
    config_path = storage / _CONFIG
    changed: list[Path] = []
    try:
        text = config_path.read_text(encoding="utf-8")
        cfg = json.loads(text)
    except (OSError, ValueError):
        return changed
    if not isinstance(cfg, dict):
        return changed

    dirty = False

    perms = cfg.get("permissions")
    if isinstance(perms, dict) and isinstance(perms.get("git"), dict):
        if "enabled" in perms["git"]:
            perms["git"].pop("enabled", None)
            dirty = True

    review = cfg.get("review")
    if isinstance(review, dict):
        try:
            current = float(review.get("confidence_threshold"))
        except (TypeError, ValueError):
            current = None
        if current is not None and abs(current - _OLD_DEFAULT_CONFIDENCE) < 1e-9:
            review["confidence_threshold"] = _NEW_DEFAULT_CONFIDENCE
            dirty = True

    if dirty:
        config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        changed.append(config_path)
    return changed
