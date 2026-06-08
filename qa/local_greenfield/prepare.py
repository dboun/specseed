#!/usr/bin/env python3
"""Prepare local greenfield human QA repo.

No agent runs. This only creates a target repo, configures it, registers it, and
prints commands for the human to start the runner and UI.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path


RUNNER_FUNCTIONS = (
    "spec",
    "implementation",
    "review",
    "merge_conflicts",
    "resolve_platform_errors",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run(cmd: list[str], *, cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)


def runner_config(provider: str = "codex") -> dict[str, object]:
    if provider == "haiku":
        spec = {
            "provider": "claude",
            "provider_data_dir": "~/.claude",
            "model": "haiku",
            "effort": "low",
        }
    else:
        spec = {
            "provider": "codex",
            "provider_data_dir": "~/.codex",
            "model": "gpt-5.4-mini",
            "effort": "low",
        }
    return {"runner": {fn: [dict(spec)] for fn in RUNNER_FUNCTIONS}}


def codex_config() -> dict[str, object]:
    return runner_config("codex")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare local_greenfield QA repo.")
    parser.add_argument(
        "--haiku",
        action="store_true",
        help="use Claude haiku with low effort instead of Codex gpt-5.4-mini",
    )
    return parser.parse_args(argv)


def next_instance(playground: Path, stamp: str) -> Path:
    base = playground / f"local_greenfield-{stamp}"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = playground / f"local_greenfield-{stamp}-{index}"
        if not candidate.exists():
            return candidate
        index += 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner_kind = "haiku" if args.haiku else "codex"
    root = repo_root()
    playground = root / ".playground"
    stamp = datetime.now().strftime("%Y_%m_%d-%H_%M_%S")
    instance = next_instance(playground, stamp)
    target = instance / "repo"
    config_file = instance / f"configuration.{runner_kind}.json"
    specseed = root / "src" / "specseed"

    target.mkdir(parents=True, exist_ok=False)
    run(["git", "init", "-q"], cwd=target)
    config_file.write_text(json.dumps(runner_config(runner_kind), indent=2) + "\n", encoding="utf-8")

    run(
        [
            str(specseed),
            "configure",
            "--target",
            str(target),
            "--defaults",
            "--use-config-file",
            str(config_file),
        ],
        cwd=root,
    )
    config_file.unlink()
    run(
        [
            str(specseed),
            "add",
            "--target",
            str(target),
            "--provider",
            "local",
            "--name",
            instance.name,
        ],
        cwd=root,
    )

    print(f"\n> prepared: \t {instance}")
    print(f"> target: \t {target}\n")
    print("> run this to start the runner on it: \n\t" + shlex.join([str(specseed), "start", "--target", str(target)]))
    print("\n> run this to start the ui: \n\t" + shlex.join([str(specseed), "serve", "--host", "0.0.0.0"]))
    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
