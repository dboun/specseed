"""
populate_defaults.py - seed specseed tracking labels and permanent posts.

The public tracking contract is intentionally small. This script uses that
contract for normal work and keeps the provider-specific cleanup needed for
label deletion/update here.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Iterable, Optional

from src.target_facing.specseed_target_src.tracking.tracking_base import TrackingBase
from src.target_facing.specseed_target_src.tracking.tracking_local import TrackingLocal
from src.target_facing.specseed_target_src.tracking.tracking_remote_github import TrackingRemoteGitHub
from src.target_facing.specseed_target_src.tracking.tracking_remote_gitlab import TrackingRemoteGitLab
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import TrackingRemoteLocal


BACKENDS = ("local", "remote_local", "remote_github", "remote_gitlab")

@dataclass(frozen=True)
class LabelSpec:
    name: str
    color: str
    description: str


LABEL_SPECS = [
    LabelSpec(
        "spec-change:adopt",
        "6f42c1",
        "Recover a spec from an existing, unspecced codebase.",
    ),
    LabelSpec(
        "spec-change:adapt",
        "8250df",
        "Request to change or extend an existing spec.",
    ),
    LabelSpec(
        "spec-change:tweak",
        "c5def5",
        "Small, direct spec edit request.",
    ),
    LabelSpec(
        "spec-change:inject",
        "d93f0b",
        "Add manual work and make it the current sprint.",
    ),
    LabelSpec(
        "spec-change:plan-next-sprint",
        "1d76db",
        "Request to spec and break down the next sprint.",
    ),
    *[
        LabelSpec(
            f"spec-change:status:{status}",
            color,
            f"Spec-change request is {status.replace('_', ' ')}.",
        )
        for status, color in {
            "open": "1d76db",
            "awaiting_approval": "d93f0b",
            "approved": "0e8a16",
            "done": "0e8a16",
            "rejected": "b60205",
        }.items()
    ],
    LabelSpec(
        "draft",
        "ededed",
        "Ignored draft entry. Unlabeled entries are moved here automatically.",
    ),
    LabelSpec("current_sprint", "fbca04", "Current sprint dashboard entry."),
    LabelSpec("epic", "5319e7", "Top-level work grouping."),
    LabelSpec("ticket", "0052cc", "Mid-level work slice inside an epic."),
    LabelSpec("issue", "006b75", "Execution unit inside a ticket."),
    *[
        LabelSpec(
            f"{tier}:status:{status}",
            color,
            f"{tier.title()} is {status.replace('_', ' ')}.",
        )
        for tier in ("epic", "ticket", "issue")
        for status, color in {
            "todo": "ededed",
            "in_progress": "1d76db",
            "blocked": "b60205",
            "in_review": "fbca04",
            "awaiting_approval": "d93f0b",
            "done": "0e8a16",
            "wont_do": "555555",
            "deprecated": "555555",
        }.items()
    ],
    LabelSpec(
        "management",
        "6f42c1",
        "Permanent management dashboard entry.",
    ),
    LabelSpec(
        "question",
        "d4c5f9",
        "Question or clarification thread.",
    ),
]

DESIRED_LABELS = {label.name for label in LABEL_SPECS}

TIMELINE_BODY = (
    "# TIMELINE\n\n"
    "Chronological log of what the specseed scheduler has done: synced changes, "
    "claimed and finished work, and state transitions. The scheduler maintains this "
    "post; treat it as read-only history.\n"
)

ROADMAP_BODY = (
    "# ROADMAP\n\n"
    "The current work breakdown: epics, their tickets, and the issues under each, "
    "with workflow status. The scheduler keeps this in sync from the work posts; "
    "treat it as a read-only overview. To change scope, open or edit a "
    "`spec-change:adapt` post, not this dashboard.\n"
)

CONTROL_BODY = (
    "# CONTROL\n\n"
    "Operate the specseed scheduler by commenting one command here (first word of "
    "the comment, case-insensitive):\n\n"
    "- **STATUS** - reply with the runner state, poll interval, and queue counts.\n"
    "- **START** - resume polling and draining work (also un-pauses).\n"
    "- **PAUSE** - stop claiming new work; the loop stays alive and still reads commands.\n"
    "- **STOP** - stop the scheduler; it cancels the in-flight task and exits.\n\n"
    "Only the approver usernames configured in `configuration.json` may issue "
    "commands; everything else here is ignored. Approvals of work gates are handled "
    "on the work posts themselves (an approver commenting `approve <id>`), not here.\n"
)

CURRENT_SPRINT_BODY = (
    "# Current sprint\n\n"
    "The issues in the active sprint and their status. The scheduler keeps this in "
    "sync; treat it as a read-only board. Plan the next sprint with a "
    "`spec-change:plan-next-sprint` post. If you are unsure what to plan next, "
    "open that post with a short help request.\n"
)

FIRST_ADAPT_DRAFT_TITLE = "Draft: describe what you want specseed to do"
FIRST_ADAPT_DRAFT_BODY = (
    "# Draft adapt request\n\n"
    "Describe what you want to build, change, or plan next. Keep the `draft` label "
    "while you are still editing.\n\n"
    "When this is ready, remove the `draft` label and save the post. Specseed will "
    "run `spec-change:adapt` from this request.\n"
)

DEFAULT_POSTS = [
    ("TIMELINE", TIMELINE_BODY, ["management"], True),
    ("ROADMAP", ROADMAP_BODY, ["management"], True),
    ("CONTROL", CONTROL_BODY, ["management"], True),
    ("Current sprint", CURRENT_SPRINT_BODY, ["management", "current_sprint"], False),
    (
        FIRST_ADAPT_DRAFT_TITLE,
        FIRST_ADAPT_DRAFT_BODY,
        ["draft", "spec-change:adapt", "spec-change:status:open"],
        False,
    ),
]


def make_tracker(kind: str) -> TrackingBase:
    if kind == "local":
        return TrackingLocal()
    if kind == "remote_local":
        return TrackingRemoteLocal()
    if kind == "remote_github":
        return TrackingRemoteGitHub()
    if kind == "remote_gitlab":
        return TrackingRemoteGitLab()
    raise ValueError(f"unsupported backend: {kind}")


def populate_defaults(
    kind: str,
    tracker: Optional[TrackingBase] = None,
    *,
    prune: bool = True,
) -> dict[str, object]:
    """Populate default labels and permanent management posts.

    Returns a small summary dict suitable for CLI output or tests.
    """

    if kind not in BACKENDS:
        raise ValueError(f"backend must be one of: {', '.join(BACKENDS)}")
    tracker = tracker or make_tracker(kind)
    summary: dict[str, object] = {
        "backend": kind,
        "deleted_labels": [],
        "ensured_labels": [],
        "default_posts": {},
        "drafted_entries": [],
        "pinned_entries": [],
    }

    if prune:
        for label in _list_label_names(tracker):
            if label not in DESIRED_LABELS:
                _delete_label(kind, tracker, label)
                summary["deleted_labels"].append(label)

    for spec in LABEL_SPECS:
        _ensure_label_exact(kind, tracker, spec)
        summary["ensured_labels"].append(spec.name)

    for title, body, labels, should_pin in DEFAULT_POSTS:
        entry_id, created = _ensure_default_post(tracker, title, body, labels)
        summary["default_posts"][title] = {"id": entry_id, "created": created}
        if should_pin:
            _require_ok(tracker.pin_entry(entry_id), f"pin {title}")
            summary["pinned_entries"].append(entry_id)

    for entry in _entries(tracker):
        if not entry.labels:
            _require_ok(tracker.add_entry_label(entry.id, "draft"), f"draft entry {entry.id}")
            summary["drafted_entries"].append(entry.id)

    return summary


def _entries(tracker: TrackingBase) -> list[object]:
    result = tracker.list_entries(is_open=None)
    _require_ok(result, "list entries")
    return list(result.data or [])


def _list_label_names(tracker: TrackingBase) -> list[str]:
    result = tracker.list_labels()
    _require_ok(result, "list labels")
    return [label.name for label in result.data.labels]


def _ensure_default_post(
    tracker: TrackingBase,
    title: str,
    body: str,
    labels: list[str],
) -> tuple[int | str, bool]:
    existing = next((entry for entry in _entries(tracker) if entry.title == title), None)
    if existing is None:
        result = tracker.add_entry(title, body=body, labels=labels)
        _require_ok(result, f"create {title}")
        return result.data.id, True

    current = {label.name for label in existing.labels}
    for label in labels:
        if label not in current:
            _require_ok(tracker.add_entry_label(existing.id, label), f"label {title} with {label}")
    return existing.id, False


def _ensure_label_exact(kind: str, tracker: TrackingBase, spec: LabelSpec) -> None:
    _require_ok(
        tracker.ensure_label(spec.name, color=spec.color, description=spec.description),
        f"ensure label {spec.name}",
    )
    _update_label(kind, tracker, spec)


def _delete_label(kind: str, tracker: TrackingBase, name: str) -> None:
    if isinstance(tracker, TrackingLocal):
        with sqlite3.connect(tracker.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM labels WHERE name = ?", (name,))
        return

    quoted = urllib.parse.quote(name, safe="")
    if kind == "remote_github":
        _request_ignore_404(tracker, "DELETE", f"/repos/{tracker.repo}/labels/{quoted}")
        return
    if kind == "remote_gitlab":
        _request_ignore_404(tracker, "DELETE", f"/projects/{tracker.project}/labels/{quoted}")
        return
    raise ValueError(f"label deletion unsupported for {kind}")


def _update_label(kind: str, tracker: TrackingBase, spec: LabelSpec) -> None:
    if isinstance(tracker, TrackingLocal):
        with sqlite3.connect(tracker.db_path) as conn:
            conn.execute(
                "UPDATE labels SET color = ?, description = ? WHERE name = ?",
                (spec.color, spec.description, spec.name),
            )
        return

    quoted = urllib.parse.quote(spec.name, safe="")
    if kind == "remote_github":
        _require_raw(
            tracker,
            "PATCH",
            f"/repos/{tracker.repo}/labels/{quoted}",
            {"color": spec.color.lstrip("#"), "description": spec.description},
            f"update label {spec.name}",
        )
        return
    if kind == "remote_gitlab":
        _require_raw(
            tracker,
            "PUT",
            f"/projects/{tracker.project}/labels/{quoted}",
            {
                "new_name": spec.name,
                "color": tracker._gitlab_color(spec.color),
                "description": spec.description,
            },
            f"update label {spec.name}",
        )
        return
    raise ValueError(f"label update unsupported for {kind}")


def _request_ignore_404(tracker: TrackingBase, method: str, path: str) -> None:
    try:
        tracker._request(method, path)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return
        raise


def _require_raw(
    tracker: TrackingBase,
    method: str,
    path: str,
    body: dict[str, object],
    action: str,
) -> None:
    try:
        tracker._request(method, path, body=body)
    except Exception as exc:
        raise RuntimeError(f"{action} failed: {exc}") from exc


def _require_ok(result: object, action: str) -> None:
    if not result.ok:
        raise RuntimeError(f"{action} failed: {result.error}")


def _print_summary(summary: dict[str, object]) -> None:
    print(f"OK: populated defaults for {summary['backend']}")
    print(f"- deleted labels: {len(summary['deleted_labels'])}")
    print(f"- ensured labels: {len(summary['ensured_labels'])}")
    print(f"- default posts: {len(summary['default_posts'])}")
    print(f"- drafted entries: {len(summary['drafted_entries'])}")
    print(f"- pinned entries: {len(summary['pinned_entries'])}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="populate specseed tracking defaults")
    parser.add_argument("backend", choices=BACKENDS)
    parser.add_argument(
        "--keep-existing-labels",
        action="store_true",
        help="do not delete labels outside the specseed default set",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        summary = populate_defaults(args.backend, prune=not args.keep_existing_labels)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
