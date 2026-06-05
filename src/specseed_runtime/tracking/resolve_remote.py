"""
resolve_remote.py - build the *configured* tracker from storage config.

The specseed skill, when it processes a spec-change, writes a small Python
``apply.py`` script under ``storage/spec-change/<id>/``. That script mutates the
**remote** posts (epics/tickets/issues, labels, comments, dashboards). Which
remote it talks to is a deployment choice the human made in ``configure.py``:
local-only, GitHub, or GitLab. This module is the single seam those generated
scripts import so they never hardcode a provider.

``resolve_remote()`` returns the remote that is the **source of truth** -- the
thing a spec-change script should write to. ``resolve_local()`` returns the
local read cache (``TrackingLocal``); read planning data from it rather than the
network so a spec-change run never hammers the provider API.

Configuration lives in the co-located storage dir (the same files
``configuring/configure.py`` writes):

    remote.json          enabled + provider + repo
    token_remote.txt      the raw access token (gitignored)

Only Python stdlib is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from specseed_runtime.storage_paths import default_storage_dir, storage_db_path
from specseed_runtime.tracking.tracking_base import TrackingBase
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_github import TrackingRemoteGitHub
from specseed_runtime.tracking.tracking_remote_gitlab import TrackingRemoteGitLab
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal


def _resolve_storage(storage: Optional[str | Path]) -> Path:
    return Path(storage) if storage else default_storage_dir()


def _load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_config(storage: Optional[str | Path] = None) -> dict:
    """Return configuration.json (or an empty dict if missing/unreadable)."""
    return _load_json(_resolve_storage(storage) / "configuration.json") or {}


def load_remote_state(storage: Optional[str | Path] = None) -> dict:
    """Return remote.json (or an empty dict if missing/unreadable)."""
    return _load_json(_resolve_storage(storage) / "remote.json") or {}


def load_token(storage: Optional[str | Path] = None) -> Optional[str]:
    """Return the raw access token from token_remote.txt, or None."""
    try:
        token = (_resolve_storage(storage) / "token_remote.txt").read_text(encoding="utf-8").strip()
        return token or None
    except OSError:
        return None


def resolve_remote(storage: Optional[str | Path] = None) -> TrackingBase:
    """Build the configured *remote* tracker (the source of truth to write to).

    remote disabled  -> ``TrackingRemoteLocal`` (no network stand-in).
    provider github   -> ``TrackingRemoteGitHub(repo, token)``.
    provider gitlab   -> ``TrackingRemoteGitLab(repo, token)``.

    Raises ``ValueError`` when a remote provider is enabled but its repo or
    token is missing, so a spec-change run fails loudly rather than half-writing.
    """
    storage = _resolve_storage(storage)
    remote_state = load_remote_state(storage)

    if not remote_state.get("enabled"):
        return TrackingRemoteLocal(db_path=storage_db_path("tracking_remote_local.db", storage))

    provider = remote_state.get("provider")
    repo = remote_state.get("repo")
    token = load_token(storage)

    if not repo:
        raise ValueError("remote enabled but remote.json has no repo")
    if not token:
        raise ValueError("remote enabled but token_remote.txt is missing")

    if provider == "github":
        return TrackingRemoteGitHub(repo=repo, token=token)
    if provider == "gitlab":
        return TrackingRemoteGitLab(repo=repo, token=token)
    raise ValueError(f"unsupported remote provider: {provider!r}")


def resolve_local(
    db_path: Optional[str | Path] = None,
    storage: Optional[str | Path] = None,
) -> TrackingLocal:
    """Build the local read cache. Query this for planning data, not the remote.

    ``db_path`` wins; otherwise the db lives in ``storage`` (default storage dir).
    """
    if db_path is not None:
        return TrackingLocal(db_path=db_path)
    return TrackingLocal(db_path=storage_db_path("tracking_local.db", storage))
