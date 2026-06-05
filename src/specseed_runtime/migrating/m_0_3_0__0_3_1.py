"""
m_0_3_0__0_3_1.py - move module-adjacent sqlite files into storage/.

0.3.0 dropped its databases next to their modules (db/specseed.db,
tracking/tracking_local.db, tracking/tracking_remote_local.db) - inside the
runtime dirs the installer wipes on re-run. 0.3.1 keeps every generated file
flat in storage/. Also drops the dead "version" key from configuration.json
(the storage version marker replaced it).

Idempotent: missing sources skip; an existing destination is never clobbered
(the stray copy stays put). Only Python stdlib is used.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

FROM = "0.3.0"
TO = "0.3.1"

# (dir relative to the specseed dir, sqlite filename) of every 0.3.0 default.
_DB_LOCATIONS = (
    ("specseed_runtime/db", "specseed.db"),
    ("specseed_runtime/tracking", "tracking_local.db"),
    ("specseed_runtime/tracking", "tracking_remote_local.db"),
)
_SIDECAR_SUFFIXES = ("-wal", "-shm")


def run(storage: str | Path, specseed_dir: str | Path) -> list[Path]:
    """Apply the hop. Returns the storage paths the databases moved to."""
    storage = Path(storage)
    specseed_dir = Path(specseed_dir)
    storage.mkdir(parents=True, exist_ok=True)

    moved: list[Path] = []
    for rel_dir, name in _DB_LOCATIONS:
        source = specseed_dir / rel_dir / name
        dest = storage / name
        if not source.exists():
            continue
        if dest.exists():
            continue  # never clobber newer data; the stray copy stays put
        for suffix in ("",) + _SIDECAR_SUFFIXES:
            sidecar = Path(str(source) + suffix)
            if sidecar.exists():
                shutil.move(str(sidecar), str(storage / (name + suffix)))
        moved.append(dest)

    _drop_config_version_key(storage / "configuration.json")
    return moved


def _drop_config_version_key(config_path: Path) -> None:
    """Remove the legacy "version" key; everything else stays byte-equal."""
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(cfg, dict) or "version" not in cfg:
        return
    cfg.pop("version")
    config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
