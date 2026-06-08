#!/usr/bin/env python3
"""specseed command router.

specseed is one thing you run once. ``specseed serve`` opens the web UI in a
browser; the same management is available headless from the CLI:

    specseed add --target <repo> --provider github   # register a repo
    specseed list                                     # repos + runner state
    specseed start  <repo>                            # spawn its runner
    specseed pause  <repo>
    specseed resume <repo>
    specseed stop   <repo>
    specseed status <repo>

``configure`` and ``run`` remain the per-repo primitives (``run`` is the
foreground worker that ``start`` spawns detached).
"""

from __future__ import annotations

import argparse
import importlib.util
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

from specseed_runtime import registry  # noqa: E402
from specseed_runtime.configuring import configure  # noqa: E402
from specseed_runtime.executing import run as run_mod  # noqa: E402
from specseed_runtime.executing import runner_control  # noqa: E402

DEFAULT_SPECSEED_DIR = ".specseed"
DEFAULT_PORT = 5050
DEV_PORT = 5051  # dev checkout serves here so it never collides with an installed specseed


def default_port() -> int:
    return DEV_PORT if registry.is_dev() else DEFAULT_PORT


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


# --------------------------------------------------------------------------- #
# repo resolution for management commands
# --------------------------------------------------------------------------- #
def _resolve_repo(args: argparse.Namespace) -> dict | None:
    """Resolve a management target to a registry-shaped record.

    Prefer an explicit registry key (positional ``repo`` = id/name/path); fall
    back to ``--target`` / cwd and synthesize a record so commands work even
    before a repo is registered.
    """
    key = getattr(args, "repo", None)
    if key:
        record = registry.get_repo(key)
        if record is not None:
            return record
    target, storage = resolve_paths(getattr(args, "target", None) or key, getattr(args, "specseed_dir", DEFAULT_SPECSEED_DIR))
    record = registry.get_repo(str(target))
    if record is not None:
        return record
    if not target.is_dir():
        print(f"specseed: unknown repo: {key or target}", file=sys.stderr)
        return None
    return {
        "id": None,
        "name": target.name,
        "target": str(target),
        "specseed_dir": getattr(args, "specseed_dir", DEFAULT_SPECSEED_DIR),
        "storage": str(storage),
        "provider": "local",
    }


# --------------------------------------------------------------------------- #
# configure / run (per-repo primitives)
# --------------------------------------------------------------------------- #
def _configure(args: argparse.Namespace) -> int:
    target, storage = resolve_paths(args.target, args.specseed_dir)
    if not _ensure_target(target):
        return 1
    storage.mkdir(parents=True, exist_ok=True)
    argv = ["--storage", str(storage)]
    if args.show:
        argv.append("--show")
    if args.defaults:
        argv.append("--defaults")
    if args.defaults_overwrite:
        argv.append("--defaults-overwrite")
    if args.use_config_file:
        argv += ["--use-config-file", args.use_config_file]
    if args.use_remote_file:
        argv += ["--use-remote-file", args.use_remote_file]
    with _in_dir(target):
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


# --------------------------------------------------------------------------- #
# registry management
# --------------------------------------------------------------------------- #
def _add(args: argparse.Namespace) -> int:
    target, storage = resolve_paths(args.target, args.specseed_dir)
    if not _ensure_target(target):
        return 1
    if args.provider not in registry.PROVIDERS:
        print(f"specseed: provider must be one of {registry.PROVIDERS}", file=sys.stderr)
        return 2
    storage.mkdir(parents=True, exist_ok=True)
    record = registry.add_repo(
        target, provider=args.provider, specseed_dir=args.specseed_dir, name=args.name
    )
    if record.get("provider") != args.provider:
        print(
            f"specseed: '{record['name']}' already registered with provider "
            f"'{record['provider']}'. Provider is final; not changing.",
            file=sys.stderr,
        )
    # github/gitlab: persist provider + repo + token so the runner can reach it.
    if args.provider in ("github", "gitlab"):
        if not args.repo or not args.token:
            print(
                "specseed: github/gitlab need --repo <owner/name> and --token <tok>.",
                file=sys.stderr,
            )
            return 2
        remote = configure.coerce_remote_state(configure.load_remote_state(storage))
        remote["enabled"] = True
        remote["provider"] = args.provider
        remote["repo"] = args.repo
        cfg = configure.load_config(storage)
        configure.write_config_files(storage, cfg, remote, token=args.token)
    else:
        remote = configure.coerce_remote_state(configure.load_remote_state(storage))
        remote["enabled"] = False
        remote["provider"] = None
        cfg = configure.load_config(storage)
        configure.write_config_files(storage, cfg, remote)
    print(f"registered {record['name']} ({record['provider']}) -> {record['target']}")
    print(f"id: {record['id']}")
    if not repo_is_setup(storage):
        print("next: `specseed configure` (or the web Configuration tab), then `specseed start`.")
    return 0


def _list(args: argparse.Namespace) -> int:
    repos = registry.list_repos()
    if not repos:
        print("no repos registered. add one with `specseed add --target <repo>`.")
        return 0
    rows = []
    for record in repos:
        status = runner_control.read_runner_status(record["storage"])
        state = status.get("state", "stopped")
        if not status.get("alive") and state != "stopped":
            state = "stopped"
        pending = status.get("pending", "")
        rows.append((record["id"], record["name"], record["provider"], state, str(pending), record["target"]))
    headers = ("ID", "NAME", "PROVIDER", "STATE", "QUEUE", "TARGET")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    for r in rows:
        print("  ".join(str(r[i]).ljust(widths[i]) for i in range(len(headers))))
    return 0


def _start(args: argparse.Namespace) -> int:
    record = _resolve_repo(args)
    if record is None:
        return 1
    if not repo_is_setup(record["storage"]):
        print("specseed: not configured yet. Run `specseed configure` first.", file=sys.stderr)
        return 1
    status = runner_control.start_runner(record, interval=getattr(args, "interval", None))
    if status.get("alive") and status.get("pid"):
        print(f"runner for '{record['name']}' running (pid {status['pid']}).")
    else:
        print(f"runner for '{record['name']}' starting...")
    return 0


def _signal(args: argparse.Namespace, desired: str) -> int:
    record = _resolve_repo(args)
    if record is None:
        return 1
    storage = record["storage"]
    if desired == runner_control.PAUSED:
        runner_control.pause_runner(storage)
        verb = "pause requested"
    elif desired == runner_control.RUNNING:
        runner_control.resume_runner(storage)
        verb = "resume requested"
    else:
        runner_control.stop_runner(storage)
        verb = "stop requested"
    print(f"{verb} for '{record['name']}'.")
    return 0


def _status(args: argparse.Namespace) -> int:
    record = _resolve_repo(args)
    if record is None:
        return 1
    status = runner_control.read_runner_status(record["storage"])
    print(f"repo:    {record['name']} ({record['provider']})")
    print(f"target:  {record['target']}")
    print(f"state:   {status.get('state', 'stopped')} (alive={status.get('alive', False)})")
    if status.get("pid"):
        print(f"pid:     {status['pid']}")
    for key in ("pending", "in_progress", "current_task_id", "last_poll_at"):
        if status.get(key) is not None:
            print(f"{key}: {status[key]}")
    return 0


def _remove(args: argparse.Namespace) -> int:
    record = registry.remove_repo(args.repo)
    if record is None:
        print(f"specseed: unknown repo: {args.repo}", file=sys.stderr)
        return 1
    print(f"removed '{record['name']}' from the registry (storage left intact).")
    return 0


# --------------------------------------------------------------------------- #
# web service
# --------------------------------------------------------------------------- #
def _load_web_server():
    path = Path(__file__).resolve().parents[1] / "ui" / "server.py"
    spec = importlib.util.spec_from_file_location("specseed_web_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _serve(args: argparse.Namespace) -> int:
    server = _load_web_server()
    port = args.port if args.port is not None else int(os.environ.get("PORT", default_port()))
    server.serve(port=port, host=args.host, open_browser=not args.no_browser)
    return 0


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
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

    configure_parser = sub.add_parser("configure", help="configure a specseed repo")
    _add_target_args(configure_parser)
    configure_parser.add_argument("--show", action="store_true", help="show resolved config")
    configure_parser.add_argument("--defaults", action="store_true", help="write defaults without prompts")
    configure_parser.add_argument(
        "--defaults-overwrite",
        action="store_true",
        help="overwrite existing config with defaults without prompts",
    )
    configure_parser.add_argument("--use-config-file", default=None, help="use this configuration.json file")
    configure_parser.add_argument("--use-remote-file", default=None, help="use this remote.json file")
    configure_parser.set_defaults(func=_configure)

    run_parser = sub.add_parser("run", help="run the scheduler (foreground worker)")
    _add_target_args(run_parser)
    run_parser.add_argument("--interval", type=float, default=None, help="poll interval seconds")
    run_parser.add_argument("--once", action="store_true", help="run one poll+sync+drain pass")
    run_parser.set_defaults(func=_run)

    serve_parser = sub.add_parser("serve", help="launch the web UI (manages all repos)")
    serve_parser.add_argument(
        "--port", type=int, default=None,
        help=f"port (default: $PORT or {DEFAULT_PORT}; dev checkout: {DEV_PORT})",
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    serve_parser.add_argument("--no-browser", action="store_true", help="do not auto-open a browser")
    serve_parser.set_defaults(func=_serve)

    add_parser = sub.add_parser("add", help="register a repo")
    _add_target_args(add_parser)
    add_parser.add_argument("--provider", default="local", choices=registry.PROVIDERS, help="tracker backend (FINAL)")
    add_parser.add_argument("--name", default=None, help="display name (default: repo dir name)")
    add_parser.add_argument("--repo", default=None, help="owner/name for github/gitlab")
    add_parser.add_argument("--token", default=None, help="access token for github/gitlab")
    add_parser.set_defaults(func=_add)

    list_parser = sub.add_parser("list", aliases=["ls"], help="list registered repos + runner state")
    list_parser.set_defaults(func=_list)

    for name, helptext in (("start", "spawn a repo's runner"), ("status", "show a repo's runner status")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("repo", nargs="?", default=None, help="repo id/name/path (default: cwd)")
        _add_target_args(p)
        if name == "start":
            p.add_argument("--interval", type=float, default=None, help="poll interval seconds")
        p.set_defaults(func=_start if name == "start" else _status)

    for name, desired in (("pause", runner_control.PAUSED), ("resume", runner_control.RUNNING), ("stop", runner_control.STOPPED)):
        p = sub.add_parser(name, help=f"{name} a repo's runner")
        p.add_argument("repo", nargs="?", default=None, help="repo id/name/path (default: cwd)")
        _add_target_args(p)
        p.set_defaults(func=lambda a, _d=desired: _signal(a, _d))

    rm_parser = sub.add_parser("remove", aliases=["rm"], help="unregister a repo (keeps its storage)")
    rm_parser.add_argument("repo", help="repo id/name/path")
    rm_parser.set_defaults(func=_remove)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:  # bare `specseed` -> launch the UI
        raw = ["serve"]
    args = parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
