#!/usr/bin/env python3
"""Install the specseed runtime into a target repository.

The installed tree defaults to ``<target>/.specseed``. Re-running the installer
refreshes the shipped runtime directories while preserving target-local storage,
configuration, and generated spec/work data.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_SPECSEED_DIR = ".specseed"
RUNTIME_DIRS = ("specseed_target_src", "skills")
DB_SIDECAR_SUFFIXES = ("", "-wal", "-shm")


def source_target_facing_dir() -> Path:
    return Path(__file__).resolve().parent / "target_facing"


def reject_unsafe_specseed_dir(value: str) -> Path:
    path = Path(value)
    if not value.strip():
        raise ValueError("specseed directory cannot be empty")
    if path.is_absolute():
        raise ValueError("specseed directory must be relative to the target repo")
    if any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("specseed directory must not contain empty, '.', or '..' path parts")
    return path


def rescue_stray_databases(install_root: Path) -> list[Path]:
    """Move sqlite files out of the runtime dirs before they get wiped.

    Pre-0.3.1 runtimes kept their databases module-adjacent inside
    ``specseed_target_src/``. ``copy_runtime_tree`` rmtree's that dir on
    re-install, so any ``*.db`` (+ WAL sidecars) found there is user data and
    moves into ``storage/`` first. An existing storage file is never clobbered
    (the stray stays put). Idempotent: a clean tree is a no-op.
    """
    storage = install_root / "storage"
    runtime_root = install_root / "specseed_target_src"
    rescued: list[Path] = []
    if not runtime_root.is_dir():
        return rescued
    for source in sorted(runtime_root.rglob("*.db")):
        dest = storage / source.name
        if dest.exists():
            continue
        storage.mkdir(parents=True, exist_ok=True)
        for suffix in DB_SIDECAR_SUFFIXES:
            sidecar = Path(str(source) + suffix)
            if sidecar.exists():
                shutil.move(str(sidecar), str(storage / (source.name + suffix)))
        rescued.append(dest)
    return rescued


def run_target_migrations(install_root: Path) -> str:
    """Run the freshly-installed tree's own migration chain on its storage.

    Shells out so each target imports its own modules (no cross-target module
    caching when installing into several repos from one process).
    """
    migrate_script = install_root / "specseed_target_src" / "migrating" / "migrate.py"
    out = subprocess.run(
        [sys.executable, str(migrate_script)],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"storage migration failed: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


def copy_runtime_tree(source_root: Path, install_root: Path) -> list[Path]:
    copied: list[Path] = []
    for name in RUNTIME_DIRS:
        source = source_root / name
        dest = install_root / name
        if not source.is_dir():
            raise FileNotFoundError(f"missing runtime source: {source}")
        if dest.exists():
            if not dest.is_dir():
                raise NotADirectoryError(f"cannot replace non-directory runtime path: {dest}")
            shutil.rmtree(dest)
        shutil.copytree(
            source,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        copied.append(dest)
    return copied


def install(
    target_repo: str | Path, specseed_dir: str = DEFAULT_SPECSEED_DIR
) -> tuple[Path, list[Path], dict]:
    target = Path(target_repo).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"target repo does not exist: {target}")
    if not target.is_dir():
        raise NotADirectoryError(f"target repo is not a directory: {target}")

    specseed_rel = reject_unsafe_specseed_dir(specseed_dir)
    install_root = target / specseed_rel
    install_root.mkdir(parents=True, exist_ok=True)

    # Order matters: rescue strays BEFORE the wipe, migrate AFTER the new tree lands.
    rescued = rescue_stray_databases(install_root)
    copied = copy_runtime_tree(source_target_facing_dir(), install_root)
    migration = run_target_migrations(install_root)
    return install_root, copied, {"rescued": rescued, "migration": migration}


def configure_command(target_repo: Path, install_root: Path) -> str:
    rel_configure = (
        install_root.relative_to(target_repo)
        / "specseed_target_src"
        / "configuring"
        / "configure.py"
    )
    return " ".join(("cd", shlex.quote(str(target_repo)), "&&", "python3", shlex.quote(rel_configure.as_posix())))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install specseed into a target repo.",
    )
    parser.add_argument("target_repo", help="repository to install specseed into")
    parser.add_argument(
        "specseed_dir",
        nargs="?",
        help=f"repo-relative install directory (default: {DEFAULT_SPECSEED_DIR})",
    )
    parser.add_argument(
        "--specseed-dir",
        dest="specseed_dir_option",
        help=f"repo-relative install directory (default: {DEFAULT_SPECSEED_DIR})",
    )
    args = parser.parse_args(argv)
    if args.specseed_dir and args.specseed_dir_option:
        parser.error("pass the specseed directory either positionally or with --specseed-dir, not both")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    specseed_dir = args.specseed_dir_option or args.specseed_dir or DEFAULT_SPECSEED_DIR
    try:
        install_root, copied, summary = install(args.target_repo, specseed_dir)
    except (FileNotFoundError, NotADirectoryError, ValueError, OSError, RuntimeError) as exc:
        print(f"specseed: {exc}", file=sys.stderr)
        return 1

    target_repo = Path(args.target_repo).expanduser().resolve()
    print(f"Installed specseed at {install_root}")
    for path in copied:
        print(f"  refreshed {path.name}/")
    for path in summary["rescued"]:
        print(f"  rescued {path.name} -> storage/")
    if summary["migration"]:
        print(f"  {summary['migration']}")
    print("\nNext, configure it from the target repo:")
    print(f"  {configure_command(target_repo, install_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
