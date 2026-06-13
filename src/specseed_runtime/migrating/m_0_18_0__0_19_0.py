"""0.19.0: (historically) harden the target gitignore on existing storage.

This hop once added ``<specseed_dir>/`` to the target ``.gitignore`` so in-target
runtime state wouldn't be tracked. 0.21.0 moves ALL data OUT of the target (no
in-target dir, no gitignore line), so this step is OBSOLETE - the 0.21 relocation
strips the old gitignore line itself. Kept as a no-op so the migration chain still
spans this version. Pre-0.21 targets reaching here flat are handled by relocation.
"""

from __future__ import annotations

from pathlib import Path


FROM = "0.18.0"
TO = "0.19.0"


def run(storage: str | Path, specseed_dir: str | Path) -> None:
    # No-op: in-target gitignore is obsolete (relocation handles it in 0.21).
    return None
