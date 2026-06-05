#!/usr/bin/env python3
"""specseed command router."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path


def _add_package_parent_to_path() -> None:
    current = Path(__file__).resolve()
    parent = current.parent.parent
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))


_add_package_parent_to_path()

from specseed_runtime.configuring import configure  # noqa: E402
from specseed_runtime.executing import run as run_mod  # noqa: E402
from specseed_runtime.storage_paths import storage_db_path  # noqa: E402

DEFAULT_SPECSEED_DIR = ".specseed"


@contextmanager
def _in_dir(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def find_target_repo(start: str | Path | None = None) -> Path:
    """Find the target repo root, preferring git's root."""
    here = Path(start or Path.cwd()).expanduser().resolve()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(here),
            capture_output=True,
            text=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except Exception:
        pass

    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    for parent in (here, *here.parents):
        if (parent / ".gitignore").exists():
            return parent
    return here


def resolve_paths(
    target_repo: str | Path | None = None,
    specseed_dir: str | Path = DEFAULT_SPECSEED_DIR,
) -> tuple[Path, Path]:
    """Return ``(target repo root, storage dir)``."""
    target = (
        Path(target_repo).expanduser().resolve()
        if target_repo is not None
        else find_target_repo()
    )
    sd = Path(specseed_dir).expanduser()
    specseed_root = (sd if sd.is_absolute() else target / sd).resolve()
    return target, specseed_root / "storage"


def repo_is_setup(storage: str | Path) -> bool:
    storage = Path(storage)
    return (storage / "configuration.json").is_file() and (storage / "remote.json").is_file()


def _ensure_target(target: Path) -> bool:
    if target.is_dir():
        return True
    print(f"specseed: target repo does not exist: {target}", file=sys.stderr)
    return False


def _specseed_dir_for_config(specseed_root: Path, target: Path) -> tuple[str, Path | None]:
    try:
        rel = specseed_root.resolve().relative_to(target.resolve())
        return rel.as_posix(), rel
    except ValueError:
        return specseed_root.resolve().as_posix(), None


def _setup(args: argparse.Namespace) -> int:
    target, storage = resolve_paths(args.target, args.specseed_dir)
    if not _ensure_target(target):
        return 1

    storage.mkdir(parents=True, exist_ok=True)
    cfg = configure.load_config(storage)
    remote = configure.load_remote_state(storage)
    specseed_value, specseed_rel = _specseed_dir_for_config(storage.parent, target)
    cfg["specseed_dir"] = specseed_value
    written = configure.write_config_files(
        storage,
        cfg,
        remote,
        repo_root=target,
        specseed_rel=specseed_rel,
        ignore_specseed=specseed_rel is not None and not args.no_gitignore,
    )

    print(f"setup: {target}")
    for name in ("config", "remote", "repo_gitignore"):
        if name in written:
            print(f"  {name}: {written[name]}")
    return 0


def _configure(args: argparse.Namespace) -> int:
    target, storage = resolve_paths(args.target, args.specseed_dir)
    if not _ensure_target(target):
        return 1
    storage.mkdir(parents=True, exist_ok=True)
    argv = ["--storage", str(storage)]
    if args.show:
        argv.append("--show")
    with _in_dir(target):
        if args.ui:
            return _run_configure_ui(["--storage", str(storage)])
        return configure.main(argv)


def _run(args: argparse.Namespace) -> int:
    target, storage = resolve_paths(args.target, args.specseed_dir)
    if not _ensure_target(target):
        return 1
    storage.mkdir(parents=True, exist_ok=True)
    argv = ["--storage", str(storage), "--repo-root", str(target)]
    if args.interval is not None:
        argv += ["--interval", str(args.interval)]
    if args.once:
        argv.append("--once")
    return run_mod.main(argv)


def _remote_local(args: argparse.Namespace) -> int:
    target, storage = resolve_paths(args.target, args.specseed_dir)
    if not _ensure_target(target):
        return 1
    if not repo_is_setup(storage):
        print(
            "specseed: repo is not set up yet. Run `specseed setup` or "
            "`specseed configure` first.",
            file=sys.stderr,
        )
        return 1
    if not args.ui:
        print("specseed: remote_local currently supports --ui only.", file=sys.stderr)
        return 2
    db = storage_db_path("tracking_remote_local.db", storage)
    with _in_dir(target):
        _run_remote_local_ui(["--db", str(db), "--author", args.author])
    return 0


def _run_configure_ui(argv: list[str]) -> int:
    from specseed_runtime.configuring import configure_ui

    return configure_ui.main(argv)


def _run_remote_local_ui(argv: list[str]) -> None:
    from specseed_runtime.tracking import tracking_remote_local_ui

    tracking_remote_local_ui.main(argv)


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", default=None, help="target repo (default: nearest git repo/cwd)")
    parser.add_argument(
        "--specseed-dir",
        default=DEFAULT_SPECSEED_DIR,
        help=f"target-relative or absolute specseed dir (default: {DEFAULT_SPECSEED_DIR})",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="specseed runtime command.")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="set up specseed storage for a repo")
    _add_target_args(setup)
    setup.add_argument("--no-gitignore", action="store_true", help="do not add specseed dir to .gitignore")
    setup.set_defaults(func=_setup)

    configure_parser = sub.add_parser("configure", help="configure a specseed repo")
    _add_target_args(configure_parser)
    configure_parser.add_argument("--ui", action="store_true", help="open the configure UI")
    configure_parser.add_argument("--show", action="store_true", help="show resolved config")
    configure_parser.set_defaults(func=_configure)

    run_parser = sub.add_parser("run", help="run the scheduler")
    _add_target_args(run_parser)
    run_parser.add_argument("--interval", type=float, default=None, help="poll interval seconds")
    run_parser.add_argument("--once", action="store_true", help="run one poll+sync+drain pass")
    run_parser.set_defaults(func=_run)

    remote_local = sub.add_parser("remote_local", help="remote-local tracker commands")
    _add_target_args(remote_local)
    remote_local.add_argument("--ui", action="store_true", help="open the remote-local UI")
    remote_local.add_argument("--author", default="remote", help="author name for new writes")
    remote_local.set_defaults(func=_remote_local)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
