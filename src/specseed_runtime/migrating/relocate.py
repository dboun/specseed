"""relocate.py - one-time move of pre-0.21 in-target data into the app home.

Before 0.21.0 a target carried its own ``<target>/<specseed_dir>/`` holding
``storage/`` (flat), the generated ``spec/``, and instruction stubs - all
gitignored. 0.21.0 moves every repo's data OUT of the target into the app home
(``$SPECSEED_HOME/repos/<slug>/``, the DATA ROOT) so the target stays clean.

This relocation is the LAUNCHER-level half of that move (the data root's location
itself changes, so a normal in-place storage hop can't do it). It lays the old
data down FLAT at the data root - exactly the shape an old storage dir had - and
then the version hop ``m_0_20_0__0_21_0`` reshapes flat -> single-purpose subdirs.

Idempotent + best-effort: it runs on every resolve, but only does work when a
legacy dir exists and the data root is still empty; afterwards the legacy dir is
gone so later runs no-op. A failure never aborts the caller.

Only Python stdlib is used.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from specseed_runtime import registry


def _is_empty(path: Path) -> bool:
    try:
        return not any(path.iterdir())
    except OSError:
        return True  # missing / unreadable -> treat as empty (relocate into it)


# Historical specseed router block that pre-0.20 targets carried in repo-root
# CLAUDE.md/AGENTS.md. Strip it here (only relocation knows the target).
_ROUTER_START = "<!-- specseed:router:start -->"
_ROUTER_END = "<!-- specseed:router:end -->"


def _strip_router_blocks(target: Path) -> None:
    for fname in ("CLAUDE.md", "AGENTS.md"):
        p = target / fname
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if _ROUTER_START not in text or _ROUTER_END not in text:
            continue
        head, _, rest = text.partition(_ROUTER_START)
        _, _, tail = rest.partition(_ROUTER_END)
        trailing = "\n" if text.endswith("\n") else ""
        head, tail = head.rstrip("\n"), tail.lstrip("\n")
        new = (head + "\n\n" + tail + trailing) if head and tail else (head or tail) + trailing
        try:
            p.write_text(new, encoding="utf-8")
        except OSError:
            pass


def _strip_gitignore_entry(target: Path, specseed_dir: str) -> None:
    """Drop the ``<specseed_dir>/`` line from the target ``.gitignore`` (best-effort)."""
    gi = target / ".gitignore"
    try:
        lines = gi.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    entry = Path(specseed_dir).as_posix().strip("/")
    kept = [ln for ln in lines if ln.strip().strip("/") != entry]
    if len(kept) == len(lines):
        return
    try:
        gi.write_text(("\n".join(kept).rstrip("\n") + "\n") if kept else "", encoding="utf-8")
    except OSError:
        pass


def _move_into(src: Path, dest_dir: Path) -> None:
    """Move ``src`` (file or dir) under ``dest_dir`` without clobbering an existing dest."""
    dest = dest_dir / src.name
    if dest.exists():
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def relocate_legacy_data(
    target: str | Path,
    specseed_dir: str | Path = registry.DEFAULT_SPECSEED_DIR,
) -> Optional[Path]:
    """Move a target's pre-0.21 ``<specseed_dir>/`` into its home data root.

    Returns the data root. No-op (returns the data root) when there is nothing to
    relocate or it was already done.
    """
    target = Path(target).expanduser().resolve()
    data_root = registry.data_root_for(target)
    legacy = registry.legacy_specseed_dir(target, specseed_dir)

    # Only relocate when an old dir exists AND the data root is still empty. After
    # a relocation the legacy dir is removed, so this naturally runs once.
    if not legacy.is_dir() or (data_root.exists() and not _is_empty(data_root)):
        return data_root

    try:
        data_root.mkdir(parents=True, exist_ok=True)
        legacy_storage = legacy / "storage"
        if legacy_storage.is_dir():
            for item in list(legacy_storage.iterdir()):
                _move_into(item, data_root)  # flat, mimicking the old storage dir
        # spec/ keeps its name (data_root/spec); instruction stubs land flat for the
        # 0.21 hop to rename into instructions/<route>/.
        for item in list(legacy.iterdir()):
            if item.name == "storage":
                continue
            _move_into(item, data_root)
        shutil.rmtree(legacy, ignore_errors=True)
        _strip_gitignore_entry(target, str(specseed_dir))
        _strip_router_blocks(target)
    except OSError:
        return data_root

    # Point the registry record at the data root (storage alias too).
    try:
        record = registry.get_repo(str(target))
        if record is not None:
            reg = registry.load_registry()
            for rec in reg.get("repos", []):
                if rec.get("id") == record.get("id"):
                    rec["data_root"] = str(data_root)
                    rec["storage"] = str(data_root)
            registry.save_registry(reg)
    except OSError:
        pass
    return data_root
