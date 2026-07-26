"""
m_0_12_0__0_13_0.py - drop the dropped ``make_prs`` remote permission.

0.13.0 removes the ``permissions.remote.make_prs`` switch: specseed does not open
pull/merge requests yet, so the switch was dead and confusing. Merging is an
issue-level, approval-gated concern now (see the merge gate), not a config toggle.

Storage-only edit of ``configuration.json``. Idempotent: a config without the key
(or no config) is a no-op. Only the dropped key is removed; nothing else is touched.
Only Python stdlib.
"""

from __future__ import annotations

import json
from pathlib import Path

FROM = "0.12.0"
TO = "0.13.0"

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

    perms = cfg.get("permissions")
    remote = perms.get("remote") if isinstance(perms, dict) else None
    if isinstance(remote, dict) and "make_prs" in remote:
        remote.pop("make_prs", None)
        config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        changed.append(config_path)
    return changed
