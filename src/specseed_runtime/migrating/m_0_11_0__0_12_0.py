"""
m_0_11_0__0_12_0.py - rename the dev-branch config keys to "primary".

0.12.0 renames the integration-branch vocabulary so "dev branch" stops reading as
a literal `dev` branch. Three config keys move (value preserved):

1. ``dev_branch``                      -> ``specseed_primary_branch``
2. ``permissions.git.merge_to_dev_branch``  -> ``permissions.git.merge_to_primary``
3. ``permissions.remote.push_dev_branch``   -> ``permissions.remote.push_primary``

Storage-only edit of ``configuration.json``. Idempotent: a config already on the
new keys (or missing entirely) is a no-op; an old key is only moved when the new
key is absent, so a hand-set new value is never clobbered. Only Python stdlib.
"""

from __future__ import annotations

import json
from pathlib import Path

FROM = "0.11.0"
TO = "0.12.0"

_CONFIG = "configuration.json"


def _rename(block: dict, old: str, new: str) -> bool:
    if not isinstance(block, dict) or old not in block:
        return False
    block.setdefault(new, block[old])
    block.pop(old, None)
    return True


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

    dirty = _rename(cfg, "dev_branch", "specseed_primary_branch")

    perms = cfg.get("permissions")
    if isinstance(perms, dict):
        dirty |= _rename(perms.get("git"), "merge_to_dev_branch", "merge_to_primary")
        dirty |= _rename(perms.get("remote"), "push_dev_branch", "push_primary")

    if dirty:
        config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        changed.append(config_path)
    return changed
