"""
storage_paths.py - the single seam for where generated runtime data lives.

Everything specseed generates at runtime (sqlite databases, config files, the
storage version marker, logs) belongs in ``<specseed_dir>/storage/`` - flat, no
subdirs. Code never lives there: a run reads/writes only the target's
``<specseed_dir>/`` (storage, spec, version marker) and never copies the engine
in. Modules derive their default paths from here.

``default_specseed_dir`` is the dev default only: this code repo's root, where
``storage/`` and ``skills/`` sit during development. Real runs against a target
pass storage in explicitly (``<target>/<specseed_dir>/storage``) - or via
``$SPECSEED_STORAGE``, which the runner exports so its subprocesses (agents,
generated apply.py) resolve the TARGET's storage, not the engine's.

Only Python stdlib is used.
"""

from __future__ import annotations

import os
from pathlib import Path

# Exported by the running scheduler (executing/run.py) so child processes that
# call default_storage_dir() land in the target's storage, not the engine's.
SPECSEED_STORAGE_ENV = "SPECSEED_STORAGE"


def default_specseed_dir() -> Path:
    """Dev default: this code repo's root (holds skills/ + dev storage/).

    storage_paths.py -> specseed_runtime/ -> src/ -> repo root.
    """
    return Path(__file__).resolve().parents[2]


def default_storage_dir() -> Path:
    env = os.environ.get(SPECSEED_STORAGE_ENV)
    if env:
        return Path(env).expanduser()
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


def agent_output_dir(storage: str | Path | None = None) -> Path:
    """storage/agent-output/ - live per-task agent stdout logs.

    Ephemeral debug feed for the UI ('Agent output' popup). One <task_id>.log per
    work run, written live as the agent talks. Not source of truth, never
    migrated; pruned to a recent window (prune_agent_output)."""
    base = Path(storage) if storage else default_storage_dir()
    return base / "agent-output"


def agent_output_file(task_id: object, storage: str | Path | None = None) -> Path:
    """The live output log for one work task: storage/agent-output/<task_id>.log."""
    return agent_output_dir(storage) / f"{task_id}.log"


def prune_agent_output(storage: str | Path | None = None, keep: int = 200) -> None:
    """Keep only the ``keep`` newest output logs; drop the rest. Best-effort.

    The feed is ephemeral and never remembered, so old logs are pure clutter -
    trim them by mtime so the dir can't grow without bound."""
    out_dir = agent_output_dir(storage)
    try:
        logs = sorted(out_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in logs[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass
