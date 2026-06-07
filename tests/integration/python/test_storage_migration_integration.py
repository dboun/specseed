"""Upgrade an old target in place: copied-code + module-adjacent dbs -> clean target.

Opt-in. Simulates a real pre-0.4.0 target repo: the old installer had copied the
engine into ``<repo>/.specseed/`` and dropped databases inside those runtime dirs.
Running the migration chain (the seam every entrypoint hits before reading) moves
every database into ``storage/``, deletes the copied engine code, and leaves the
data readable through the normal accessors. No network, no agents.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specseed_runtime.db.database import Database
from specseed_runtime.migrating import migrate
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


pytestmark = pytest.mark.integration


def _old_shape_target(tmp_path: Path) -> tuple[Path, Path]:
    """A pre-0.4.0 ``.specseed``: copied engine + module-adjacent sqlite data."""
    specseed_dir = tmp_path / "target" / ".specseed"
    runtime = specseed_dir / "specseed_runtime"
    (runtime / "executing").mkdir(parents=True)
    (runtime / "executing" / "run.py").write_text("# copied engine\n", encoding="utf-8")
    # copied skills, including the old version marker the installer shipped
    (specseed_dir / "skills" / "specseed").mkdir(parents=True)
    (specseed_dir / "skills" / "specseed" / "version.txt").write_text("0.3.0\n", encoding="utf-8")

    remote = TrackingRemoteLocal(db_path=runtime / "tracking" / "tracking_remote_local.db", author="human")
    assert remote.add_entry("remote ticket", "body").ok
    local = TrackingLocal(db_path=runtime / "tracking" / "tracking_local.db", author="agent")
    assert local.add_entry("local mirror entry", "body").ok
    queue = Database(db_path=runtime / "db" / "specseed.db")
    queue.enqueue("handle_test", post_id=1, payload={"why": "migration"})

    storage = specseed_dir / "storage"
    storage.mkdir(parents=True)
    (storage / "configuration.json").write_text(
        json.dumps({"version": 1, "dev_branch": "custom"}) + "\n", encoding="utf-8"
    )
    # no version marker -> looks like the 0.3.0 baseline
    return specseed_dir, storage


def test_migrates_all_databases_and_deletes_copied_code(tmp_path: Path) -> None:
    specseed_dir, storage = _old_shape_target(tmp_path)

    applied = migrate.run_migrations(storage=storage)

    # whole chain ran, marker now matches the running engine
    assert "m_0_3_0__0_3_1" in applied
    assert "m_0_3_1__0_4_0" in applied
    assert (storage / "version.txt").read_text(encoding="utf-8").strip() == migrate.code_version()

    # copied engine code is gone; storage + config survive
    assert not (specseed_dir / "specseed_runtime").exists()
    assert not (specseed_dir / "skills").exists()

    # data readable through the normal accessors at the flat storage paths
    remote = TrackingRemoteLocal(db_path=storage / "tracking_remote_local.db")
    assert {e.title for e in remote.list_entries(is_open=None).data} == {"remote ticket"}
    local = TrackingLocal(db_path=storage / "tracking_local.db")
    assert {e.title for e in local.list_entries(is_open=None).data} == {"local mirror entry"}
    queue = Database(db_path=storage / "specseed.db")
    assert queue.pending_count() == 1, "queued task lost in migration"
    assert queue.tasks_for(1)[0]["action"] == "handle_test"

    cfg = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
    assert "version" not in cfg
    # 0.12.0 hop renamed dev_branch -> specseed_primary_branch
    assert "dev_branch" not in cfg
    assert cfg["specseed_primary_branch"] == "custom"


def test_second_migration_is_idempotent(tmp_path: Path) -> None:
    _specseed_dir, storage = _old_shape_target(tmp_path)
    migrate.run_migrations(storage=storage)
    before = {p.name: p.read_bytes() for p in storage.iterdir() if p.is_file()}

    assert migrate.run_migrations(storage=storage) == []

    after = {p.name: p.read_bytes() for p in storage.iterdir() if p.is_file()}
    assert before == after
