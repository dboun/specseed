"""0.19.0: harden target gitignore on existing storage.

The specseed dir holds runtime dbs/logs/token/spec output. It must be ignored on
the target repo history before work branches are cut, or storage can get tracked
and later block checkouts/merge prep.
"""

from __future__ import annotations

import json
from pathlib import Path

from specseed_runtime.configuring import scaffold


FROM = "0.18.0"
TO = "0.19.0"


def _primary_branch(storage: Path) -> str:
    cfg_path = storage / "configuration.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "main"
    return str(cfg.get("specseed_primary_branch") or "main")


def run(storage: str | Path, specseed_dir: str | Path) -> None:
    storage = Path(storage)
    specseed_dir = Path(specseed_dir)
    repo_root = specseed_dir.parent
    scaffold.ensure_git_repo(repo_root, _primary_branch(storage))
    gi = scaffold.ensure_repo_gitignored(repo_root, specseed_dir)
    scaffold.ensure_gitignore_committed(repo_root, gi)
