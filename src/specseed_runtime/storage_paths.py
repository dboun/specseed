"""
storage_paths.py - the single seam for where generated runtime data lives.

Everything specseed generates at runtime (sqlite databases, config files, the
storage version marker, logs) belongs in ``<specseed_dir>/storage/`` - flat, no
subdirs. Code never lives there: a run reads/writes only the target's
``<specseed_dir>/`` (storage, spec, version marker) and never copies the engine
in. Modules derive their default paths from here.

``default_specseed_dir`` is the dev default only: this code repo's root, where
``storage/`` and ``skills/`` sit during development. Real runs against a target
pass storage in explicitly (``<target>/<specseed_dir>/storage``).

Only Python stdlib is used.
"""

from __future__ import annotations

from pathlib import Path


def default_specseed_dir() -> Path:
    """Dev default: this code repo's root (holds skills/ + dev storage/).

    storage_paths.py -> specseed_runtime/ -> src/ -> repo root.
    """
    return Path(__file__).resolve().parents[2]


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
