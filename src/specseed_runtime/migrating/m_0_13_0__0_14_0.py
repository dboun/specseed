"""
m_0_13_0__0_14_0.py - drop the dropped ``gitignore_specseed_dir`` toggle.

0.14.0 makes gitignoring the specseed dir unconditional: storage (dbs, tokens,
logs) tracked in the target leaves the tree perpetually dirty and breaks every
merge-gate checkout, so there is no longer an opt-out. The config toggle is gone.

Storage-only edit of ``configuration.json``. Idempotent: a config without the key
(or no config) is a no-op. Only the dropped key is removed; nothing else is touched.
Only Python stdlib.
"""

from __future__ import annotations

import json
from pathlib import Path

FROM = "0.13.0"
TO = "0.14.0"

_CONFIG = "configuration.json"


def run(storage: str | Path, specseed_dir: str | Path) -> list[Path]:
    """Apply the hop. Returns the paths it changed."""
    storage = Path(storage)
    config_path = storage / _CONFIG
    changed: list[Path] = []
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return changed
    if not isinstance(cfg, dict):
        return changed

    if "gitignore_specseed_dir" in cfg:
        cfg.pop("gitignore_specseed_dir", None)
        config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        changed.append(config_path)
    return changed
