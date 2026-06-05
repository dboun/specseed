"""
m_0_3_1__0_4_0.py - delete engine code copied into the target specseed dir.

Through 0.3.x the installer copied the engine (``specseed_runtime/`` - then
``specseed_target_src/`` - and ``skills/``) into ``<target>/<specseed_dir>/``.
0.4.0 drops that model: the engine runs from its own repo against a target and
the target holds only ``storage/`` + ``spec/`` + the version marker. This hop
removes the stale copied trees so nothing in the target shadows the real engine.

Idempotent: a missing tree is a no-op; ``storage/`` and ``spec/`` are never
touched. Only Python stdlib is used.
"""

from __future__ import annotations

import shutil
from pathlib import Path

FROM = "0.3.1"
TO = "0.4.0"

# Copied engine trees, relative to the specseed dir. Both old and new package
# names, since a target could have been installed under either.
_COPIED_CODE_DIRS = ("specseed_runtime", "specseed_target_src", "skills")


def run(storage: str | Path, specseed_dir: str | Path) -> list[Path]:
    """Apply the hop. Returns the code dirs it deleted."""
    specseed_dir = Path(specseed_dir)
    deleted: list[Path] = []
    for name in _COPIED_CODE_DIRS:
        target = specseed_dir / name
        if target.is_dir():
            shutil.rmtree(target)
            deleted.append(target)
    return deleted
