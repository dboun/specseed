"""
tracking_remote_local.py - a "remote" tracker backed by the local sqlite store.

In production the source of truth is a real remote (GitHub/GitLab). For local
development and tests we still want a *remote* to sync from, without any network.
``TrackingRemoteLocal`` is exactly that stand-in: it inherits the entire sqlite
implementation from :class:`TrackingLocal` and implements nothing new. The only
difference is its default database file, so a ``TrackingRemoteLocal`` (the
source of truth) and a ``TrackingLocal`` (the local copy) live in separate files
and can sit on opposite ends of a ``sync_from_remote`` call.

Only Python stdlib is used.
"""

from __future__ import annotations

from pathlib import Path

from tracking_local import TrackingLocal


DEFAULT_DB_PATH = Path(__file__).with_name("tracking_remote_local.db")


class TrackingRemoteLocal(TrackingLocal):
    """A remote tracker that is really just another local sqlite database.

    Behaves identically to :class:`TrackingLocal`; only the default storage
    location differs so it can be the source of truth in a local sync.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, author: str = "remote") -> None:
        super().__init__(db_path=db_path, author=author)
