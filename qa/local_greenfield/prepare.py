#!/usr/bin/env python3
"""Prepare local greenfield human QA repo.

No agent runs. This only creates a target repo, configures it, registers it, and
prints commands for the human to start the runner and UI.
"""

from __future__ import annotations

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


def codex_config() -> dict[str, object]:
    spec = {
        "provider": "codex",
        "provider_data_dir": "~/.codex",
        "model": "gpt-5.4-mini",
        "effort": "medium",
    }
    return {"runner": {fn: [dict(spec)] for fn in RUNNER_FUNCTIONS}}


def main() -> int:
    root = repo_root()
    playground = root / ".playground"
    stamp = datetime.now().strftime("%Y_%m_%d-%H_%M_%S")
    instance = playground / f"local_greenfield-{stamp}"
    target = instance / "repo"
    config_file = instance / "configuration.codex.json"
    specseed = root / "src" / "specseed"

    target.mkdir(parents=True, exist_ok=False)
    run(["git", "init", "-q"], cwd=target)
    config_file.write_text(json.dumps(codex_config(), indent=2) + "\n", encoding="utf-8")

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

    print(f"prepared: {instance}")
    print(f"target: {target}")
    print("run this to start the runner on it: " + shlex.join([str(specseed), "start", "--target", str(target)]))
    print("run this to start the ui: " + shlex.join([str(specseed), "serve", "--host", "0.0.0.0"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
