"""registry.py - the global multi-repo registry.

specseed is one thing you run once and manage many repos from. The per-repo
runtime data lives in the app home, one DATA ROOT per repo under
``$SPECSEED_HOME/repos/<slug>/`` (NEVER inside the target) - the runners stay
separate. What is shared is this small global index of *which* repos exist, where
their data root is, and which tracker provider each uses. Both the CLI and the web
service are thin control planes over it.

Lives flat in ``$SPECSEED_HOME`` (default ``~/.specseed``; a dev checkout -
this file under ``src/specseed_runtime/`` - uses ``<repo>/data-dev`` instead):

    registry.json   list of registered repos

A repo record:

    {id, name, target, specseed_dir, data_root, storage, provider, added_at}

``provider`` is one of ``local`` / ``github`` / ``gitlab`` and is FINAL once set
(switching tracker backends is not supported). ``data_root`` is derived so callers
never recompute it (``storage`` is a back-compat alias = ``data_root``).
``specseed_dir`` is vestigial: kept only to locate pre-0.21 in-target data for the
one-time relocation. Only Python stdlib is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_SPECSEED_DIR = ".specseed"
PROVIDERS = ("local", "github", "gitlab")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _looks_dev(here: Path, home: Path) -> bool:
    """Pure dev-checkout test (split out for unit tests). ``here`` = registry.py,
    ``home`` = user home. Dev = ``<repo>/src/specseed_runtime/`` shape AND not under
    ``~/.specseed`` - install.sh copies the same layout there."""
    if not (here.parent.name == "specseed_runtime" and here.parent.parent.name == "src"):
        return False
    return not here.resolve().is_relative_to((home / ".specseed").resolve())


def is_dev() -> bool:
    """True when running from a dev checkout, never for the installed engine."""
    return _looks_dev(Path(__file__).resolve(), Path.home())


def dev_root() -> Path:
    """Repo root of the dev checkout (only meaningful when ``is_dev()``)."""
    return Path(__file__).resolve().parents[2]


def specseed_home() -> Path:
    """The global specseed dir: ``$SPECSEED_HOME``, else ``<repo>/data-dev`` in a
    dev checkout, else ``~/.specseed``."""
    override = os.environ.get("SPECSEED_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if is_dev():
        return (dev_root() / "data-dev").resolve()
    return (Path.home() / ".specseed").resolve()


def registry_file() -> Path:
    return specseed_home() / "registry.json"


def data_root_for(target: str | Path) -> Path:
    """The per-repo DATA ROOT: ``$SPECSEED_HOME/repos/<slug>``.

    Lives in the app home (beside the registry), NEVER inside the target repo, so
    the target stays clean and agents can't wander into engine state. Split into
    single-purpose subdirs by ``storage_paths.py``.
    """
    target = Path(target).expanduser().resolve()
    return specseed_home() / "repos" / _slug(target)


def storage_for(target: str | Path, specseed_dir: str | Path = DEFAULT_SPECSEED_DIR) -> Path:
    """Back-compat alias for :func:`data_root_for`. ``specseed_dir`` is ignored -
    data no longer lives under the target; it is only kept for legacy relocation."""
    return data_root_for(target)


def legacy_specseed_dir(target: str | Path, specseed_dir: str | Path = DEFAULT_SPECSEED_DIR) -> Path:
    """The pre-0.21 in-target ``<target>/<specseed_dir>/`` dir (for relocation)."""
    target = Path(target).expanduser().resolve()
    sd = Path(specseed_dir).expanduser()
    return (sd if sd.is_absolute() else target / sd).resolve()


def _slug(target: Path) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", target.name).strip("-").lower() or "repo"
    digest = hashlib.sha1(str(target).encode("utf-8")).hexdigest()[:6]
    return f"{base}-{digest}"


def load_registry() -> dict[str, Any]:
    """Return the registry document ({"repos": [...]}). Empty if missing/bad."""
    try:
        data = json.loads(registry_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"repos": []}
    if not isinstance(data, dict):
        return {"repos": []}
    repos = data.get("repos")
    if not isinstance(repos, list):
        data["repos"] = []
    return data


def save_registry(registry: dict[str, Any]) -> Path:
    path = registry_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    return path


def list_repos() -> list[dict[str, Any]]:
    return list(load_registry().get("repos", []))


def _matches(record: dict[str, Any], key: str, resolved: Optional[Path]) -> bool:
    if record.get("id") == key or record.get("name") == key:
        return True
    if resolved is not None:
        try:
            if Path(record.get("target", "")).resolve() == resolved:
                return True
        except OSError:
            pass
    return False


def get_repo(key: str) -> Optional[dict[str, Any]]:
    """Look a repo up by id, name, or (resolved) target path."""
    if not key:
        return None
    try:
        resolved = Path(key).expanduser().resolve()
        if not resolved.exists():
            resolved = None
    except OSError:
        resolved = None
    for record in list_repos():
        if _matches(record, key, resolved):
            return record
    return None


def add_repo(
    target: str | Path,
    *,
    provider: str = "local",
    specseed_dir: str | Path = DEFAULT_SPECSEED_DIR,
    name: Optional[str] = None,
) -> dict[str, Any]:
    """Register (or return the existing record for) a target repo.

    Idempotent on (resolved target, specseed_dir). ``provider`` is FINAL: an
    existing record keeps its provider; only the name may be updated.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r} (one of {PROVIDERS})")
    target = Path(target).expanduser().resolve()
    data_root = data_root_for(target)
    registry = load_registry()
    for record in registry["repos"]:
        same_target = Path(record.get("target", "")).expanduser().resolve() == target
        if same_target:
            if name:
                record["name"] = name
            return record
    record = {
        "id": _slug(target),
        "name": name or target.name,
        "target": str(target),
        "specseed_dir": str(specseed_dir),
        # data_root is canonical; "storage" kept as an alias many consumers read.
        "data_root": str(data_root),
        "storage": str(data_root),
        "provider": provider,
        "added_at": _now(),
    }
    registry["repos"].append(record)
    save_registry(registry)
    return record


def remove_repo(key: str) -> Optional[dict[str, Any]]:
    """Drop a repo from the registry (does NOT touch its storage). Returns it."""
    registry = load_registry()
    record = get_repo(key)
    if record is None:
        return None
    registry["repos"] = [r for r in registry["repos"] if r.get("id") != record.get("id")]
    save_registry(registry)
    return record
