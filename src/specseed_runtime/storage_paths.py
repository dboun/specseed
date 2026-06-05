"""
storage_paths.py - the single seam for where generated runtime data lives.

Everything specseed generates at runtime (sqlite databases, config files, the
storage version marker, logs) belongs in ``<specseed_dir>/storage/`` - flat, no
subdirs. The installer wipes and re-copies the runtime dirs on every re-run and
preserves ONLY ``storage/``, so a data file defaulting anywhere else is a data
file that dies on upgrade. Modules derive their default paths from here.

Only Python stdlib is used.
"""

from __future__ import annotations

from pathlib import Path


def default_specseed_dir() -> Path:
    """The dir holding specseed_target_src/ + skills/ + storage/.

    ``<repo>/.specseed`` when installed; ``src/target_facing`` in the dev repo.
    """
    # specseed_target_src/ -> specseed dir
    return Path(__file__).resolve().parents[1]


def default_storage_dir() -> Path:
    return default_specseed_dir() / "storage"


def storage_db_path(name: str, storage: str | Path | None = None) -> Path:
    """Default path for a named sqlite file: flat inside the storage dir."""
    base = Path(storage) if storage else default_storage_dir()
    return base / name


def version_marker_file(storage: str | Path | None = None) -> Path:
    """storage/version.txt - what version last shaped this storage dir."""
    base = Path(storage) if storage else default_storage_dir()
    return base / "version.txt"


def skill_version_file(specseed_dir: str | Path | None = None) -> Path:
    """skills/specseed/version.txt - the version of the code that is running."""
    base = Path(specseed_dir) if specseed_dir else default_specseed_dir()
    return base / "skills" / "specseed" / "version.txt"
