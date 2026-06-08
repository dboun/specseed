#!/usr/bin/env python3
"""
migrate.py - bring storage/ up to the running code's version, one hop at a time.

Storage shape is versioned. ``storage/version.txt`` records what version last
shaped the storage dir (missing marker = 0.3.0, the first storage-versioned
release; anything older is unsupported). The running code's version is
``<specseed_dir>/skills/specseed/version.txt``. When storage trails code, the
registered hops whose [FROM, TO) span covers the stored version run in order,
each writing the marker forward. A z-bump with no hop just fast-forwards the
marker.

Each hop lives in its own module ``m_<from>__<to>.py`` with FROM/TO constants
and an idempotent ``run(storage, specseed_dir)``. A hop spans consecutive
migration-bearing versions only - never restates older hops; the chain runs
them one by one.

Runnable directly against a target's storage:

    python3 src/specseed_runtime/migrating/migrate.py --storage <target>/.specseed/storage

Only Python stdlib is used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional


def _add_package_parent_to_path() -> None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "specseed_runtime").is_dir():
            sys.path.insert(0, str(parent / "src"))
            return
        if (parent / "specseed_runtime").exists():
            sys.path.insert(0, str(parent))
            return


_add_package_parent_to_path()

from specseed_runtime.migrating import m_0_3_0__0_3_1
from specseed_runtime.migrating import m_0_3_1__0_4_0
from specseed_runtime.migrating import m_0_4_0__0_5_0
from specseed_runtime.migrating import m_0_5_0__0_7_0
from specseed_runtime.migrating import m_0_7_0__0_9_0
from specseed_runtime.migrating import m_0_9_0__0_11_0
from specseed_runtime.migrating import m_0_11_0__0_12_0
from specseed_runtime.migrating import m_0_12_0__0_13_0
from specseed_runtime.migrating import m_0_13_0__0_14_0
from specseed_runtime.migrating import m_0_14_0__0_16_0
from specseed_runtime.migrating import m_0_16_0__0_18_0
from specseed_runtime.migrating import m_0_18_0__0_19_0
from specseed_runtime.storage_paths import (
    default_specseed_dir,
    default_storage_dir,
    skill_version_file,
    version_marker_file,
)


# Oldest storage shape we migrate FROM. Pre-0.3.0 trees are unsupported.
BASELINE_VERSION = "0.3.0"

# Ordered hop chain, oldest first. Append new hops here; never edit shipped ones.
MIGRATIONS = (m_0_3_0__0_3_1, m_0_3_1__0_4_0, m_0_4_0__0_5_0, m_0_5_0__0_7_0, m_0_7_0__0_9_0, m_0_9_0__0_11_0, m_0_11_0__0_12_0, m_0_12_0__0_13_0, m_0_13_0__0_14_0, m_0_14_0__0_16_0, m_0_16_0__0_18_0, m_0_18_0__0_19_0)


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.strip().split("."))


def code_version(specseed_dir: Optional[str | Path] = None) -> str:
    """The version of the running engine: ``<engine>/skills/specseed/version.txt``.

    ``specseed_dir`` is accepted for call compatibility but ignored. The engine is
    never copied into a target, so a target's leftover copied ``skills/`` (from an
    old installer) must not shadow the real running version - it would pin the
    chain below the deletion hop and the stale code would never get cleaned up.
    """
    path = skill_version_file()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise FileNotFoundError(f"no engine version file at {path}") from exc


def storage_version(storage: Optional[str | Path] = None) -> str:
    """What version last shaped this storage dir. Missing marker = baseline."""
    try:
        text = version_marker_file(storage).read_text(encoding="utf-8").strip()
    except OSError:
        return BASELINE_VERSION
    return text or BASELINE_VERSION


def write_storage_version(version: str, storage: Optional[str | Path] = None) -> Path:
    marker = version_marker_file(storage)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(version + "\n", encoding="utf-8")
    return marker


def _specseed_dir_for_storage(storage: Path) -> Path:
    """The tree a storage dir belongs to (where a hop hunts for old files)."""
    return storage.parent if storage.name == "storage" else default_specseed_dir()


def run_migrations(
    storage: Optional[str | Path] = None,
    specseed_dir: Optional[str | Path] = None,
) -> list[str]:
    """Apply pending hops, oldest first. Returns the applied hop module names.

    Idempotent; cheap when storage is current. Never rewrites the marker
    backwards when storage was shaped by newer code.
    """
    storage = Path(storage) if storage else default_storage_dir()
    specseed_dir = Path(specseed_dir) if specseed_dir else _specseed_dir_for_storage(storage)
    target = code_version(specseed_dir)
    current = storage_version(storage)
    if parse_version(current) > parse_version(target):
        return []

    applied: list[str] = []
    for hop in MIGRATIONS:
        # Never migrate past the running engine. Hops are ordered, so once one
        # lands beyond the target, every later hop does too.
        if parse_version(hop.TO) > parse_version(target):
            break
        if parse_version(current) >= parse_version(hop.TO):
            continue
        if parse_version(current) < parse_version(hop.FROM):
            raise RuntimeError(
                f"storage at {current} predates the migration chain (oldest hop starts at {hop.FROM})"
            )
        hop.run(storage, specseed_dir)
        current = hop.TO
        write_storage_version(current, storage)
        applied.append(Path(hop.__file__).stem)

    if parse_version(current) < parse_version(target):
        # z-bump with no hop: fast-forward the marker.
        write_storage_version(target, storage)
    return applied


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run pending specseed storage migrations.")
    ap.add_argument("--storage", default=None, help="override the storage dir")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    applied = run_migrations(storage=args.storage)
    now = storage_version(args.storage)
    if applied:
        print(f"storage migrated to {now} (applied: {', '.join(applied)})")
    else:
        print(f"storage at {now} (no migrations needed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
