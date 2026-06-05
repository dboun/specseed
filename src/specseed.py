#!/usr/bin/env python3
"""specseed - run the spec+work engine against a target repo.

The engine lives here (this repo) and is NEVER copied into the target. A run
reads and writes only the target's specseed dir,
``<target_repo>/<specseed_dir>/``, holding ``storage/`` (dbs, config, logs, the
version marker) and the generated ``spec/``. ``specseed_dir`` defaults to
``.specseed`` and may be given as a relative (under the target) or absolute path.

    python3 src/specseed.py <target_repo>                 # specseed_dir = .specseed
    python3 src/specseed.py <target_repo> .specseed --once
    python3 src/specseed.py <target_repo> --interval 30

Configure a target first (interactive):

    python3 src/specseed_runtime/configuring/configure.py --storage <target>/.specseed/storage

Other CLIs (configure, the tracking UIs) behave the same way - they operate on a
target's storage without any code landing in the target.

Only Python stdlib is used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The engine's src/ on the path so ``specseed_runtime`` imports cleanly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from specseed_runtime.executing import run as run_mod  # noqa: E402

DEFAULT_SPECSEED_DIR = ".specseed"


def resolve_paths(target_repo: str, specseed_dir: str) -> tuple[Path, Path]:
    """(target repo root, storage dir) for a run.

    ``specseed_dir`` is joined under the target unless it is absolute; storage is
    the ``storage/`` inside it.
    """
    target = Path(target_repo).expanduser().resolve()
    sd = Path(specseed_dir).expanduser()
    specseed_root = (sd if sd.is_absolute() else target / sd).resolve()
    return target, specseed_root / "storage"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run specseed against a target repo.")
    parser.add_argument("target_repo", help="the repo specseed manages")
    parser.add_argument(
        "specseed_dir",
        nargs="?",
        default=DEFAULT_SPECSEED_DIR,
        help=f"target-relative (or absolute) specseed dir (default: {DEFAULT_SPECSEED_DIR})",
    )
    parser.add_argument("--interval", type=float, default=None, help="poll interval seconds")
    parser.add_argument("--once", action="store_true", help="run one poll+sync+drain pass and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))

    target, storage = resolve_paths(args.target_repo, args.specseed_dir)
    if not target.is_dir():
        print(f"specseed: target repo does not exist: {target}", file=sys.stderr)
        return 1
    storage.mkdir(parents=True, exist_ok=True)

    run_argv = ["--storage", str(storage), "--repo-root", str(target)]
    if args.interval is not None:
        run_argv += ["--interval", str(args.interval)]
    if args.once:
        run_argv.append("--once")
    return run_mod.main(run_argv)


if __name__ == "__main__":
    raise SystemExit(main())
