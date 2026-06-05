"""Upgrade-by-reinstall: old module-adjacent sqlite data -> storage/ migration.

Opt-in. Simulates a real 0.3.0 target repo (databases inside the runtime dirs
the installer wipes) and verifies the re-install rescues every database into
storage/, runs the migration chain, and the data stays readable through the
normal accessors. No network, no agents.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import specseed
from src.target_facing.specseed_target_src.db.database import Database
from src.target_facing.specseed_target_src.tracking.tracking_local import TrackingLocal
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


pytestmark = pytest.mark.integration


def _old_shape_install(tmp_path: Path) -> Path:
    """Install once, then shove real sqlite data into the 0.3.0 locations."""
    repo = tmp_path / "target"
    repo.mkdir()
    install_root, _copied, _summary = specseed.install(repo)

    runtime = install_root / "specseed_target_src"
    # the 0.3.1 install wrote a marker; erase it to fake a 0.3.0 tree
    (install_root / "storage" / "version.txt").unlink()

    remote = TrackingRemoteLocal(db_path=runtime / "tracking" / "tracking_remote_local.db", author="human")
    assert remote.add_entry("remote ticket", "body").ok
    local = TrackingLocal(db_path=runtime / "tracking" / "tracking_local.db", author="agent")
    assert local.add_entry("local mirror entry", "body").ok
    queue = Database(db_path=runtime / "db" / "specseed.db")
    queue.enqueue("handle_test", post_id=1, payload={"why": "migration"})

    config = install_root / "storage" / "configuration.json"
    config.write_text(json.dumps({"version": 1, "dev_branch": "custom"}) + "\n", encoding="utf-8")
    return install_root


def test_reinstall_migrates_all_databases_and_state(tmp_path: Path) -> None:
    install_root = _old_shape_install(tmp_path)
    repo = install_root.parent

    _root, _copied, summary = specseed.install(repo)

    storage = install_root / "storage"
    # every database rescued, runtime tree clean
    assert {p.name for p in summary["rescued"]} == {
        "specseed.db",
        "tracking_local.db",
        "tracking_remote_local.db",
    }
    assert list((install_root / "specseed_target_src").rglob("*.db*")) == []

    # data readable through the normal accessors at the storage paths
    remote = TrackingRemoteLocal(db_path=storage / "tracking_remote_local.db")
    assert {e.title for e in remote.list_entries(is_open=None).data} == {"remote ticket"}
    local = TrackingLocal(db_path=storage / "tracking_local.db")
    assert {e.title for e in local.list_entries(is_open=None).data} == {"local mirror entry"}
    queue = Database(db_path=storage / "specseed.db")
    assert queue.pending_count() == 1, "queued task lost in migration"
    assert queue.tasks_for(1)[0]["action"] == "handle_test"

    # marker matches the shipped version, legacy config version key dropped
    shipped = (install_root / "skills" / "specseed" / "version.txt").read_text(encoding="utf-8").strip()
    assert (storage / "version.txt").read_text(encoding="utf-8").strip() == shipped
    cfg = json.loads((storage / "configuration.json").read_text(encoding="utf-8"))
    assert "version" not in cfg
    assert cfg["dev_branch"] == "custom"


def test_second_reinstall_is_idempotent(tmp_path: Path) -> None:
    install_root = _old_shape_install(tmp_path)
    repo = install_root.parent
    specseed.install(repo)
    storage = install_root / "storage"
    before = {p.name: p.read_bytes() for p in storage.iterdir() if p.is_file()}

    _root, _copied, summary = specseed.install(repo)

    assert summary["rescued"] == []
    after = {p.name: p.read_bytes() for p in storage.iterdir() if p.is_file()}
    assert before == after
