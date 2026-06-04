"""
clear_everything.py - dev-only reset for tracking backends.

NEVER link this from production code, workflow code, runner code, or user-facing
docs. This file exists only so developers can clear dev trackers while testing.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import urllib.parse
from typing import Iterable, Optional

from src.target_facing.specseed_target_src.tracking.populate_defaults import BACKENDS, make_tracker
from src.target_facing.specseed_target_src.tracking.tracking_base import TrackingBase
from src.target_facing.specseed_target_src.tracking.tracking_local import TrackingLocal


def clear_everything(
    kind: str,
    tracker: Optional[TrackingBase] = None,
) -> dict[str, object]:
    if kind not in BACKENDS:
        raise ValueError(f"backend must be one of: {', '.join(BACKENDS)}")
    tracker = tracker or make_tracker(kind)
    if isinstance(tracker, TrackingLocal):
        return _clear_sqlite(tracker, kind)
    return _clear_hosted(kind, tracker)


def _clear_sqlite(tracker: TrackingLocal, kind: str) -> dict[str, object]:
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        counts = {
            "comments": _count(conn, "comments") + _count(conn, "pull_request_comments"),
            "reactions": _count(conn, "comment_reactions")
            + _count(conn, "pull_request_comment_reactions"),
            "posts": _count(conn, "entries"),
            "pull_requests": _count(conn, "pull_requests"),
            "labels": _count(conn, "labels"),
            "pins": _count(conn, "pinned_entries"),
        }
        for table in (
            "comment_reactions",
            "comments",
            "entry_labels",
            "pinned_entries",
            "pull_request_comment_reactions",
            "pull_request_comments",
            "pull_request_labels",
            "pull_requests",
            "entries",
            "labels",
        ):
            conn.execute(f"DELETE FROM {table}")
        for table in ("entries", "comments", "comment_reactions", "pull_requests"):
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
    return {"backend": kind, "mode": "hard_delete", **counts}


def _clear_hosted(kind: str, tracker: TrackingBase) -> dict[str, object]:
    summary = {
        "backend": kind,
        "mode": "hosted_close_and_label_delete",
        "posts_closed": 0,
        "pull_requests_closed": 0,
        "labels_deleted": 0,
    }
    posts = tracker.list_entries(is_open=True)
    _require_ok(posts, "list open posts")
    for post in posts.data or []:
        _require_ok(tracker.set_entry_closed(post.id), f"close post {post.id}")
        summary["posts_closed"] += 1

    prs = tracker.list_pull_requests(is_open=True)
    _require_ok(prs, "list open pull requests")
    for pr in prs.data or []:
        _require_ok(tracker.set_pull_request_closed(pr.id), f"close pull request {pr.id}")
        summary["pull_requests_closed"] += 1

    labels = tracker.list_labels()
    _require_ok(labels, "list labels")
    for label in labels.data.labels:
        _delete_hosted_label(kind, tracker, label.name)
        summary["labels_deleted"] += 1
    return summary


def _delete_hosted_label(kind: str, tracker: TrackingBase, name: str) -> None:
    quoted = urllib.parse.quote(name, safe="")
    if kind == "remote_github":
        _request_ignore_404(tracker, "DELETE", f"/repos/{tracker.repo}/labels/{quoted}")
        return
    if kind == "remote_gitlab":
        _request_ignore_404(tracker, "DELETE", f"/projects/{tracker.project}/labels/{quoted}")
        return
    raise ValueError(f"hosted label deletion unsupported for {kind}")


def _request_ignore_404(tracker: TrackingBase, method: str, path: str) -> None:
    try:
        tracker._request(method, path)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return
        raise


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _require_ok(result: object, action: str) -> None:
    if not result.ok:
        raise RuntimeError(f"{action} failed: {result.error}")


def _print_summary(summary: dict[str, object]) -> None:
    print(f"OK: cleared {summary['backend']} ({summary['mode']})")
    for key, value in summary.items():
        if key not in ("backend", "mode"):
            print(f"- {key}: {value}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="dev-only tracking reset")
    parser.add_argument("backend", choices=BACKENDS)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        summary = clear_everything(args.backend)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
