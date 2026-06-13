"""0.21.0: reshape a flat data root into single-purpose subdirs.

0.21.0 moves every repo's data OUT of the target into the app home and SPLITS the
old flat storage dir into single-purpose subdirs, so the engine can hand each agent
route only the dirs it needs. ``relocate.py`` already laid the old data down FLAT at
the home data root (mimicking the old storage layout); this hop reshapes it:

    specseed.db                              -> db/
    tracking_local.db, tracking_remote_local.db -> tracker/
    configuration.json, remote.json, token_remote.txt, version.txt -> config/
    control.json, runner.json, runner.out, seed_state.json -> runtime/
    platform.log, agent-output/              -> logs/
    AGENTS_INSTRUCTIONS_<ROUTE>.md           -> instructions/<route>/repo.md
    CUSTOM_INSTRUCTIONS[_<ROUTE>].md         -> instructions/[<route>/]custom.md

``spec/`` and ``spec-change/`` already sit correctly at the data root (relocation
kept their names). The old per-storage ``.gitignore`` (it only hid the token, which
now lives outside any repo) is dropped. Missing instruction stubs are seeded fresh in
the new layout.

Idempotent: a missing source is a no-op; an existing dest is never clobbered. Run on a
fresh data root (nothing flat to move) it just ensures the stubs. Only Python stdlib +
scaffold helpers.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from specseed_runtime.configuring import scaffold
from specseed_runtime.storage_paths import (
    config_dir,
    db_dir,
    instructions_dir,
    logs_dir,
    runtime_dir,
    tracker_dir,
)

FROM = "0.20.0"
TO = "0.21.0"

# Flat filename -> the subdir it moves into (a function of the data root).
_FILE_HOMES = {
    "specseed.db": db_dir,
    "tracking_local.db": tracker_dir,
    "tracking_remote_local.db": tracker_dir,
    "configuration.json": config_dir,
    "remote.json": config_dir,
    "token_remote.txt": config_dir,
    "version.txt": config_dir,
    "control.json": runtime_dir,
    "runner.json": runtime_dir,
    "runner.out": runtime_dir,
    "seed_state.json": runtime_dir,
    "platform.log": logs_dir,
    "agent-output": logs_dir,  # a directory
}

# Old flat instruction stub -> (route subdir or "", new basename).
_STUB_MOVES = {
    "AGENTS_INSTRUCTIONS_IMPL.md": ("impl", "repo.md"),
    "AGENTS_INSTRUCTIONS_SPEC.md": ("spec", "repo.md"),
    "AGENTS_INSTRUCTIONS_REVIEW.md": ("review", "repo.md"),
    "AGENTS_INSTRUCTIONS_ASK.md": ("ask", "repo.md"),
    "CUSTOM_INSTRUCTIONS.md": ("", "custom.md"),
    "CUSTOM_INSTRUCTIONS_IMPL.md": ("impl", "custom.md"),
    "CUSTOM_INSTRUCTIONS_SPEC.md": ("spec", "custom.md"),
    "CUSTOM_INSTRUCTIONS_REVIEW.md": ("review", "custom.md"),
    "CUSTOM_INSTRUCTIONS_ASK.md": ("ask", "custom.md"),
}


def _move_no_clobber(src: Path, dest: Path) -> None:
    if not src.exists() or dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    # A sqlite db carries WAL/SHM/journal sidecars; move them alongside so a
    # reopened db never hits a stale/missing sidecar ("disk I/O error").
    if src.name.endswith(".db"):
        for suffix in ("-wal", "-shm", "-journal"):
            side = src.parent / (src.name + suffix)
            if side.exists():
                shutil.move(str(side), str(dest.parent / (dest.name + suffix)))


def run(storage: str | Path, specseed_dir: str | Path) -> None:
    root = Path(storage)

    # 1. flat storage files -> single-purpose subdirs
    for name, home_fn in _FILE_HOMES.items():
        _move_no_clobber(root / name, home_fn(root) / name)

    # 2. flat instruction stubs -> instructions/<route>/<name> (rename)
    instr = instructions_dir(root)
    for old_name, (route, new_name) in _STUB_MOVES.items():
        dest = (instr / route / new_name) if route else (instr / new_name)
        _move_no_clobber(root / old_name, dest)

    # 3. drop the old per-storage .gitignore (it only hid the token, now outside any repo)
    stale_gi = root / ".gitignore"
    if stale_gi.is_file():
        try:
            stale_gi.unlink()
        except OSError:
            pass

    # 4. seed any still-missing stubs in the new layout (create-if-absent, never clobbers)
    scaffold.write_instruction_files(root)
    scaffold.write_custom_instruction_stubs(root)
