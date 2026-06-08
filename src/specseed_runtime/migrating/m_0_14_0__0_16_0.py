"""
m_0_14_0__0_16_0.py - add the ``awaiting_merge`` work status (re-seed labels).

0.16.0 makes ``done`` mean MERGED to primary: an accepted-but-unmerged issue now
parks ``awaiting_merge`` (new status) instead of being closed ``done`` with the
branch off primary. The dependency gate keys on ``done``, so this keeps a dependent
held until its dep actually lands.

Storage touch: delete ``seed_state.json`` so the next startup re-seeds labels and the
new ``<tier>:status:awaiting_merge`` labels land on already-seeded targets (prune=False;
user-custom labels untouched).

Idempotent: a missing marker is a no-op. Only Python stdlib.
"""

from __future__ import annotations

from pathlib import Path

FROM = "0.14.0"
TO = "0.16.0"

_SEED_MARKER = "seed_state.json"


def run(storage: str | Path, specseed_dir: str | Path) -> list[Path]:
    """Apply the hop. Returns the paths it changed."""
    storage = Path(storage)
    changed: list[Path] = []
    marker = storage / _SEED_MARKER
    if marker.exists():
        marker.unlink()
        changed.append(marker)
    return changed
