"""
storage_paths.py - the single seam for where generated runtime data lives.

Everything specseed generates at runtime lives under a per-repo **data root** -
``$SPECSEED_HOME/repos/<slug>/`` (see ``registry.py``). The data root is split
into SINGLE-PURPOSE subdirs so the engine can hand each agent route only the dirs
it needs (info control - agents never see the work queue, token, or logs):

    <data_root>/
      db/          specseed.db                                  work queue
      tracker/     tracking_local.db, tracking_remote_local.db  tracker cache
      config/      configuration.json, remote.json, token_remote.txt, version.txt
      runtime/     control.json, runner.json, runner.out, seed_state.json,
                   apr_counter.txt, sessions.json
      logs/        platform.log, agent-output/<task>.log
      spec/        the live project spec
      spec-change/<id>/  plan.json, apply.py, spec/  (staged)
      instructions/  custom.md + <route>/repo.md|custom.md

The data root is NEVER inside the target repo - a run reads/writes only the data
root, and the agent's working dir (the target) stays clean. Modules derive their
default paths from here; the data root is passed in explicitly (``--storage`` /
``$SPECSEED_STORAGE``), which the runner exports so subprocesses (agents, generated
apply.py) resolve the SAME data root.

Only Python stdlib is used.
"""

from __future__ import annotations

import os
from pathlib import Path

# Exported by the running scheduler (executing/run.py) so child processes that
# call default_storage_dir() land in this repo's data root, not the engine's.
SPECSEED_STORAGE_ENV = "SPECSEED_STORAGE"

# Single-purpose subdir names under a data root.
DB_SUBDIR = "db"
TRACKER_SUBDIR = "tracker"
CONFIG_SUBDIR = "config"
RUNTIME_SUBDIR = "runtime"
LOGS_SUBDIR = "logs"
SPEC_SUBDIR = "spec"
SPEC_CHANGE_SUBDIR = "spec-change"
INSTRUCTIONS_SUBDIR = "instructions"

# Which subdir each db file lives in.
_DB_HOMES = {
    "specseed.db": DB_SUBDIR,
    "tracking_local.db": TRACKER_SUBDIR,
    "tracking_remote_local.db": TRACKER_SUBDIR,
}


def default_specseed_dir() -> Path:
    """Dev default: this code repo's root (holds skills/ + dev storage/).

    storage_paths.py -> specseed_runtime/ -> src/ -> repo root.
    """
    return Path(__file__).resolve().parents[2]


def default_storage_dir() -> Path:
    """The per-repo data root, from ``$SPECSEED_STORAGE`` or a dev fallback.

    Real runs always export the env (the launcher resolves the home-based data
    root). The fallback only matters to bare module runs / tests, which usually
    pass the data root in explicitly anyway.
    """
    env = os.environ.get(SPECSEED_STORAGE_ENV)
    if env:
        return Path(env).expanduser()
    return default_specseed_dir() / "storage"


def _root(data_root: str | Path | None) -> Path:
    return Path(data_root) if data_root else default_storage_dir()


# --- single-purpose subdirs -------------------------------------------------

def db_dir(data_root: str | Path | None = None) -> Path:
    return _root(data_root) / DB_SUBDIR


def tracker_dir(data_root: str | Path | None = None) -> Path:
    return _root(data_root) / TRACKER_SUBDIR


def config_dir(data_root: str | Path | None = None) -> Path:
    return _root(data_root) / CONFIG_SUBDIR


def runtime_dir(data_root: str | Path | None = None) -> Path:
    return _root(data_root) / RUNTIME_SUBDIR


def logs_dir(data_root: str | Path | None = None) -> Path:
    return _root(data_root) / LOGS_SUBDIR


def spec_dir(data_root: str | Path | None = None) -> Path:
    """``<data_root>/spec`` - the live project spec the agent reads."""
    return _root(data_root) / SPEC_SUBDIR


def spec_change_root(data_root: str | Path | None = None) -> Path:
    """``<data_root>/spec-change`` - parent of every per-request spec-change dir."""
    return _root(data_root) / SPEC_CHANGE_SUBDIR


def instructions_dir(data_root: str | Path | None = None) -> Path:
    """``<data_root>/instructions`` - user-owned per-route guidance."""
    return _root(data_root) / INSTRUCTIONS_SUBDIR


# --- db files ---------------------------------------------------------------

def storage_db_path(name: str, storage: str | Path | None = None) -> Path:
    """Path for a named sqlite file, routed to its single-purpose subdir."""
    home = _DB_HOMES.get(name, DB_SUBDIR)
    return _root(storage) / home / name


# --- config files (engine only; never handed to an agent) -------------------

def config_file(data_root: str | Path | None = None) -> Path:
    return config_dir(data_root) / "configuration.json"


def remote_file(data_root: str | Path | None = None) -> Path:
    return config_dir(data_root) / "remote.json"


def token_file(data_root: str | Path | None = None) -> Path:
    return config_dir(data_root) / "token_remote.txt"


def version_marker_file(storage: str | Path | None = None) -> Path:
    """config/version.txt - what version last shaped this data root.

    Canonical (write) location. Pre-0.21 data roots kept it flat at the root;
    ``migrate.storage_version`` falls back there until the 0.21 hop relocates it.
    """
    return config_dir(storage) / "version.txt"


def legacy_version_marker_file(storage: str | Path | None = None) -> Path:
    """Pre-0.21 flat marker location, for the migration read-fallback only."""
    return _root(storage) / "version.txt"


# --- runtime/lifecycle files (engine only) ----------------------------------

def control_file(data_root: str | Path | None = None) -> Path:
    return runtime_dir(data_root) / "control.json"


def runner_file(data_root: str | Path | None = None) -> Path:
    return runtime_dir(data_root) / "runner.json"


def runner_out_file(data_root: str | Path | None = None) -> Path:
    return runtime_dir(data_root) / "runner.out"


def seed_marker_file(data_root: str | Path | None = None) -> Path:
    return runtime_dir(data_root) / "seed_state.json"


def sessions_file(data_root: str | Path | None = None) -> Path:
    """runtime/sessions.json - agent conversation ids keyed by post (continuity)."""
    return runtime_dir(data_root) / "sessions.json"


# --- logs (engine only) -----------------------------------------------------

def platform_log_file(data_root: str | Path | None = None) -> Path:
    return logs_dir(data_root) / "platform.log"


def skill_version_file(specseed_dir: str | Path | None = None) -> Path:
    """skills/specseed/version.txt - the version of the code that is running."""
    base = Path(specseed_dir) if specseed_dir else default_specseed_dir()
    return base / "skills" / "specseed" / "version.txt"


def agent_output_dir(storage: str | Path | None = None) -> Path:
    """logs/agent-output/ - live per-task agent stdout logs.

    Ephemeral debug feed for the UI ('Agent output' popup). One <task_id>.log per
    work run, written live as the agent talks. Not source of truth, never
    migrated; pruned to a recent window (prune_agent_output)."""
    return logs_dir(storage) / "agent-output"


def agent_output_file(task_id: object, storage: str | Path | None = None) -> Path:
    """The live output log for one work task: logs/agent-output/<task_id>.log."""
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
