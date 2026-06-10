"""specseed web service - one server, all repos.

A single shared web UI over the global registry. Each repo's runner stays a
separate process; this server only reads their storage (queue db, logs, runner
heartbeat) and writes the small control file to command them. The tracker
endpoints are scoped per repo and only meaningful for the local provider;
github/gitlab repos are managed on their own platform.

Only Python stdlib is used.
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import sqlite3
import sys
import threading
import webbrowser
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _add_repo_root_to_path() -> None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "specseed_runtime").is_dir():
            sys.path.insert(0, str(parent / "src"))
            return
        if (parent / "specseed_runtime").exists():
            sys.path.insert(0, str(parent))
            return


_add_repo_root_to_path()

from specseed_runtime import registry
from specseed_runtime import storage_paths
from specseed_runtime.configuring import configure
from specseed_runtime.executing import agent_runner
from specseed_runtime.executing import runner_control
from specseed_runtime.db.database import DEFAULT_PRIORITY, LANE_CONTROL, LANE_WORK, LANES
from specseed_runtime.tracking import populate_defaults
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal
from specseed_runtime.tracking.supported_values import (
    DIFFICULTY_LABELS,
    SUPPORTED_REACTIONS,
    WORK_TYPE_LABELS,
)

ROOT = Path(__file__).resolve().parent

# Permanent dashboard posts the tracker manages itself (SCHEDULE/ROADMAP/CONTROL/
# CURRENT SPRINT) - surfaced as quick toggles, never in the normal list. The adapt
# DRAFT post is NOT one of these: it is a user-editable request template.
DEFAULT_POST_TITLES = {
    title for title, _body, labels, _pin in populate_defaults.DEFAULT_POSTS if "management" in labels
}

# Labels a human actually reaches for, surfaced first in every label picker
# (filter menu, add-label dropdown, new-post form). Ordered alphabetically; the
# rest fall under an "Others:" group. Kept here so the UI never re-derives it.
IMPORTANT_LABELS = [
    "ask",
    "current_sprint",
    "draft",
    "epic",
    "issue",
    "platform_error",
    "spec-change:adapt",
    "spec-change:adopt",
    "spec-change:inject",
    "spec-change:plan-next-sprint",
    "spec-change:tweak",
    "ticket",
]


# Set by serve(); lets env_payload report the real bind address for the copy chip.
_SERVE: dict = {}


def _lan_ip() -> str:
    """Best-effort LAN IP of this machine (the address a phone on the same
    network would use). No packets are sent - a UDP connect just picks the route."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def env_payload() -> dict:
    """Global server facts for the UI shell (/api/env).

    Runs inside the serving process (ThreadingHTTPServer = one process), so
    ``os.getpid()`` is the specseed web-server pid.
    """
    return {
        "dev": registry.is_dev(),
        "home": str(registry.specseed_home()),
        "pid": os.getpid(),
        "host": _SERVE.get("host"),
        "port": _SERVE.get("port"),
        "lan_ip": _lan_ip(),
    }


def _human_labels() -> list[str]:
    base = [f"spec-change:{r}" for r in ("adopt", "adapt", "tweak", "inject", "plan-next-sprint")]
    base += sorted(WORK_TYPE_LABELS) + sorted(DIFFICULTY_LABELS) + ["ask"]
    return base


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _in_future(ts: object) -> bool:
    """True if an ISO-Z timestamp is still ahead of now (a scheduled retry)."""
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt > datetime.now(timezone.utc)


def _plain(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


def _ok(result: object) -> object:
    if getattr(result, "ok", False):
        return _plain(getattr(result, "data", None))
    raise RuntimeError(str(getattr(result, "error", None) or "tracking op failed"))


def _state(value: str | None) -> bool | None:
    return {"open": True, "closed": False}.get(value or "", None)


def _csv(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


# --------------------------------------------------------------------------- #
# per-repo context
# --------------------------------------------------------------------------- #
class RepoNotFound(Exception):
    pass


class ExternallyManaged(Exception):
    pass


class TargetMissing(Exception):
    pass


def _prepare_target(target: str, create: bool = False) -> Path:
    """Expand ~ in an add-repo target. Missing dir: mkdir when create, else TargetMissing."""
    if not target:
        raise RuntimeError("target must be a directory path")
    path = Path(target).expanduser()
    if not path.is_dir():
        if not create:
            raise TargetMissing(str(path))
        path.mkdir(parents=True, exist_ok=True)
    return path


def _repo(repo_id: str) -> dict:
    record = registry.get_repo(repo_id)
    if record is None:
        raise RepoNotFound(repo_id)
    return record


def _tracker_db(storage: str | Path) -> Path:
    return Path(storage) / "tracking_remote_local.db"


def _queue_db(storage: str | Path) -> Path:
    return Path(storage) / "specseed.db"


def _ui_user(storage: str | Path) -> str:
    """The human identity for UI/CLI writes: the first approver, or 'user'."""
    approvers = (configure.load_config(storage).get("approvals") or {}).get("approver_usernames") or []
    return approvers[0] if approvers else "user"


def _platform_user(storage: str | Path) -> str:
    """The tracker account the platform posts as (blank -> 'specseed' fallback)."""
    return configure.load_config(storage).get("platform_username") or ""


def _tracker_for(record: dict):
    if record.get("provider") != "local":
        raise ExternallyManaged(record.get("provider"))
    # The UI's human writes share the local db with the runtime's platform writes;
    # author them as the human so platform-authored posts stay distinguishable
    # (and read-only). NOT resolve_remote() - that authors as the platform.
    return TrackingRemoteLocal(db_path=_tracker_db(record["storage"]), author=_ui_user(record["storage"]))


def _external_link(record: dict) -> str | None:
    remote = configure.load_remote_state(record["storage"])
    provider, repo = remote.get("provider"), remote.get("repo")
    if not repo:
        return None
    if provider == "github":
        return f"https://github.com/{repo}/issues"
    if provider == "gitlab":
        return f"https://gitlab.com/{repo}/-/issues"
    return None


# --------------------------------------------------------------------------- #
# monitor data (read-only over storage)
# --------------------------------------------------------------------------- #
def _qint(query: dict, name: str, default: int) -> int:
    try:
        return max(0, int(query.get(name, [default])[0]))
    except (TypeError, ValueError):
        return default


def _page_args(query: dict, key: str, default_limit: int, max_limit: int = 500) -> tuple[int, int]:
    """(offset, limit) for one paginated monitor section, from ?<key>_offset/_limit."""
    offset = _qint(query, f"{key}_offset", 0)
    limit = min(max(_qint(query, f"{key}_limit", default_limit), 1), max_limit)
    return offset, limit


def _empty_page(offset: int, limit: int) -> dict:
    return {"items": [], "total": 0, "offset": offset, "limit": limit}


def _task_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}


def _zero_counts() -> dict:
    counts = {"pending": 0, "in_progress": 0, "success": 0, "failed": 0}
    counts["lanes"] = {
        lane: {"pending": 0, "in_progress": 0, "success": 0, "failed": 0}
        for lane in LANES
    }
    return counts


def _queue_counts(storage: str | Path) -> dict:
    counts = _zero_counts()
    db = _queue_db(storage)
    if not db.exists():
        return counts
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
        for status, n in conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall():
            counts[status] = n
        if "lane" in _task_columns(conn):
            rows = conn.execute(
                "SELECT lane, status, COUNT(*) FROM tasks GROUP BY lane, status"
            ).fetchall()
            for lane, status, n in rows:
                if lane in counts["lanes"]:
                    counts["lanes"][lane][status] = n
        else:
            for status in ("pending", "in_progress", "success", "failed"):
                counts["lanes"][LANE_CONTROL][status] = counts.get(status, 0)
        conn.close()
    except sqlite3.Error:
        pass
    return counts


def _read_tasks(storage: str | Path, queue: tuple[int, int] = (0, 50), errors: tuple[int, int] = (0, 50)) -> dict:
    """Paginated queue + errors (newest first) plus full status counts."""
    db = _queue_db(storage)
    out = {
        "tasks": _empty_page(*queue),
        "errors": _empty_page(*errors),
        "counts": _zero_counts(),
    }
    if not db.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        columns = _task_columns(conn)
        for r in conn.execute("SELECT status, COUNT(*) n FROM tasks GROUP BY status").fetchall():
            out["counts"][r["status"]] = r["n"]
        if "lane" in columns:
            for r in conn.execute("SELECT lane, status, COUNT(*) n FROM tasks GROUP BY lane, status").fetchall():
                if r["lane"] in out["counts"]["lanes"]:
                    out["counts"]["lanes"][r["lane"]][r["status"]] = r["n"]
        else:
            for status in ("pending", "in_progress", "success", "failed"):
                out["counts"]["lanes"][LANE_CONTROL][status] = out["counts"].get(status, 0)
        out["tasks"]["total"] = sum(
            out["counts"].get(status, 0) for status in ("pending", "in_progress", "success", "failed")
        )
        lane_expr = "lane" if "lane" in columns else f"'{LANE_CONTROL}' AS lane"
        priority_expr = "priority" if "priority" in columns else f"{DEFAULT_PRIORITY} AS priority"
        rows = conn.execute(
            f"SELECT task_id, action, post_id, status, attempts, created_at, last_attempted_at, not_before, "
            f"{lane_expr}, {priority_expr} "
            "FROM tasks ORDER BY task_id DESC LIMIT ? OFFSET ?",
            (queue[1], queue[0]),
        ).fetchall()
        out["tasks"]["items"] = [dict(r) for r in rows]
        # Flag rows that have a live/finished agent-output log so the UI can offer
        # an 'Output' button (cheap existence check over the small visible page).
        out_dir = storage_paths.agent_output_dir(storage)
        for item in out["tasks"]["items"]:
            item["has_output"] = (out_dir / f"{item['task_id']}.log").exists()
        # Join the originating task so each error carries its action/post/attempts
        # (task_errors outlive their task by design, hence the LEFT JOIN).
        out["errors"]["total"] = conn.execute("SELECT COUNT(*) FROM task_errors").fetchone()[0]
        errs = conn.execute(
            "SELECT e.error_id, e.task_id, e.message, e.executed_at, "
            "t.action, t.post_id, t.attempts "
            "FROM task_errors e LEFT JOIN tasks t ON t.task_id = e.task_id "
            "ORDER BY e.error_id DESC LIMIT ? OFFSET ?",
            (errors[1], errors[0]),
        ).fetchall()
        out["errors"]["items"] = [dict(r) for r in errs]
        conn.close()
    except sqlite3.Error as exc:
        out["db_error"] = str(exc)
    return out


def _retry_task(storage: str | Path, task_id: object) -> dict:
    """Make a stuck task runnable now; the scheduler picks it up next claim.

    Two cases, one operation: a terminal ``failed`` task is re-queued, and a
    ``pending`` task still waiting on a future ``not_before`` (a scheduled
    backoff retry) is pulled forward by clearing it. ``attempts`` is preserved,
    so this is the SAME task to the recovery machinery - one more shot, while
    its ``platform_error`` post and the "close to cancel" switch keep working
    (a success closes the post via ``on_recovered``; a fresh failure re-enters
    recovery exactly as it would have). Any other state is rejected.
    """
    from specseed_runtime.db.database import Database, STATUS_FAILED, STATUS_PENDING

    db_path = _queue_db(storage)
    if not db_path.exists():
        raise RuntimeError("no work queue yet")
    db = Database(db_path)
    task = db.get_task(int(task_id))
    if task is None:
        raise RuntimeError(f"task {task_id} not found")
    status = task.get("status")
    scheduled = status == STATUS_PENDING and _in_future(task.get("not_before"))
    if status != STATUS_FAILED and not scheduled:
        raise RuntimeError(f"task {task_id} is '{status}'; only failed or scheduled tasks can be retried")
    db.requeue(int(task_id), not_before=None)
    return {"task_id": int(task_id), "status": STATUS_PENDING, "pulled_forward": scheduled}


def _read_log(path: Path, offset: int = 0, limit: int = 100) -> dict:
    """Newest-first page over the JSONL platform log."""
    out = _empty_page(offset, limit)
    if not path.exists():
        return out
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    out["total"] = len(raw)
    for line in reversed(raw[max(0, len(raw) - offset - limit) : max(0, len(raw) - offset)]):
        try:
            out["items"].append(json.loads(line))
        except json.JSONDecodeError:
            out["items"].append({"raw": line})
    return out


def _config_schema() -> dict:
    """Option lists + defaults the Configuration UI renders every field from.

    Sourced from ``configure`` so the UI never drifts from the real taxonomy.
    """
    return {
        "runner_functions": list(configure.RUNNER_FUNCTIONS),
        "runner_providers": list(configure.RUNNER_PROVIDERS),
        "provider_homes": dict(configure.PROVIDER_DEFAULT_HOME),
        # Preset model options per provider (codex slugs read from the local
        # cache) + the default model when a spec switches provider. The UI adds
        # a "custom" option on top of these.
        "model_presets": {p: agent_runner.model_presets(p) for p in configure.RUNNER_PROVIDERS},
        "model_defaults": {p: agent_runner.default_model(p) for p in configure.RUNNER_PROVIDERS},
        "default_spec": configure.default_runner_spec(),
        "agent_categories": dict(configure.AGENT_CATEGORIES),
        "agent_levels": list(configure.AGENT_LEVELS),
        "default_config": configure.default_config(),
    }


def _config_gate(status: dict) -> dict:
    """Whether the configuration may be edited, given the runner state."""
    state = status.get("state", "stopped")
    alive = bool(status.get("alive"))
    in_progress = int(status.get("in_progress") or 0)
    if not alive or state == "stopped":
        return {"editable": True, "reason": ""}
    if state == "running":
        return {"editable": False, "reason": "Pause or stop the runner before changing configuration."}
    if state == "paused" and in_progress > 0:
        return {
            "editable": False,
            "reason": "Paused, but the last job is still finishing. Check back in a few minutes.",
        }
    return {"editable": True, "reason": ""}


def _repo_summary(record: dict) -> dict:
    storage = record["storage"]
    status = runner_control.read_runner_status(storage)
    configured = (Path(storage) / "configuration.json").is_file() and (
        Path(storage) / "remote.json"
    ).is_file()
    # live queue counts straight from the db (the runner heartbeat goes stale when
    # the runner is down, but queued work is still queued)
    counts = _queue_counts(storage)
    return {
        "id": record["id"],
        "name": record["name"],
        "provider": record["provider"],
        "target": record["target"],
        "configured": configured,
        "external_link": _external_link(record),
        "queue": {
            "pending": counts["pending"],
            "in_progress": counts["in_progress"],
            "lanes": counts["lanes"],
        },
        "runner": {
            "state": status.get("state", "stopped"),
            "alive": bool(status.get("alive")),
            "pending": status.get("pending"),
            "in_progress": status.get("in_progress"),
            "control_pending": status.get("control_pending"),
            "work_pending": status.get("work_pending"),
            "current_control_task_id": status.get("current_control_task_id"),
            "current_work_task_id": status.get("current_work_task_id"),
            "pid": status.get("pid"),
        },
    }


# --------------------------------------------------------------------------- #
# tracker reaction toggles (direct sqlite, mirrors the tracker schema)
# --------------------------------------------------------------------------- #
def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _delete_one_reaction(db: Path, table: str, field: str, parent_id, reaction: str, author: str) -> bool:
    with _connect(db) as conn:
        row = conn.execute(
            f"SELECT id FROM {table} WHERE {field} = ? AND kind = ? AND user = ? ORDER BY id DESC LIMIT 1",
            (parent_id, reaction, author),
        ).fetchone()
        if row is None:
            return False
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row["id"],))
    return True


def _touch_entry(db: Path, entry_id) -> None:
    with _connect(db) as conn:
        conn.execute("UPDATE entries SET updated_at = ? WHERE id = ?", (_now(), entry_id))


def _needs_approval(post: dict) -> bool:
    """A post awaits a human gate when any of its labels end ``:status:awaiting_approval``
    (the request post's ``spec-change:status:awaiting_approval`` or a work post's
    ``<tier>:status:awaiting_approval``), or ``:status:awaiting_merge`` (work accepted but
    not yet on primary - the human merges it or approves the merge)."""
    for label in post.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else None
        if name and (name.endswith(":status:awaiting_approval") or name.endswith(":status:awaiting_merge")):
            return True
    return False


def _in_progress_post_tasks(storage: str | Path) -> dict[str, str]:
    """Map each post with a running work-lane task to that task's id (newest wins).

    Drives the post-card dot, the inline 'Agent is working on this…' box, and the
    task id the 'Agent output' popup tails. Degrades to {} on any db hiccup."""
    try:
        conn = sqlite3.connect(f"file:{_queue_db(storage)}?mode=ro", uri=True, timeout=5.0)
        columns = _task_columns(conn)
        lane_clause = "AND lane = ?" if "lane" in columns else ""
        args = (LANE_WORK,) if "lane" in columns else ()
        mapping: dict[str, str] = {}
        for post_id, task_id in conn.execute(
            "SELECT post_id, task_id FROM tasks "
            f"WHERE status = 'in_progress' AND post_id IS NOT NULL {lane_clause} "
            "ORDER BY task_id DESC",
            args,
        ):
            mapping.setdefault(str(post_id), str(task_id))
        conn.close()
        return mapping
    except sqlite3.Error:
        return {}  # indicator degrades gracefully


def _in_progress_post_ids(storage: str | Path) -> set[str]:
    """Post ids with a work-lane task currently running (agent/git work)."""
    return set(_in_progress_post_tasks(storage))


def _agent_running_fields(post: dict, running: dict[str, str]) -> None:
    """Stamp agent_running + agent_task_id (the in-flight work task to tail)."""
    pid = str(post.get("id"))
    post["agent_running"] = pid in running
    post["agent_task_id"] = running.get(pid)


def _enrich_list(record: dict, posts: object) -> object:
    """Stamp list-only fields the summary shape lacks: comment_count, the newest
    activity timestamp (entry vs latest comment, for sorting), a search blob of
    comment bodies, the needs_approval flag, and agent_running."""
    if not isinstance(posts, list):
        return posts
    agg: dict[str, tuple[int, str | None, str]] = {}
    try:
        conn = sqlite3.connect(f"file:{_tracker_db(record['storage'])}?mode=ro", uri=True, timeout=5.0)
        agg = {
            str(row[0]): (row[1], row[2], row[3] or "")
            for row in conn.execute(
                "SELECT entry_id, COUNT(*), MAX(updated_at), GROUP_CONCAT(body, ' ') "
                "FROM comments GROUP BY entry_id"
            )
        }
        conn.close()
    except sqlite3.Error:
        pass  # chips degrade gracefully; the list itself still renders
    running = _in_progress_post_tasks(record["storage"])
    for post in posts:
        if not isinstance(post, dict):
            continue
        count, last_comment_at, bodies = agg.get(str(post.get("id")), (0, None, ""))
        post["comment_count"] = count
        post["comments_text"] = bodies
        post["needs_approval"] = _needs_approval(post)
        _agent_running_fields(post, running)
        stamps = [s for s in (post.get("updated_at"), last_comment_at) if s]
        post["last_activity_at"] = max(stamps) if stamps else post.get("updated_at")
    return posts


def _enrich_one(record: dict, post: object) -> object:
    """Stamp agent_running + agent_task_id on a single post detail (drawer view)."""
    if isinstance(post, dict):
        _agent_running_fields(post, _in_progress_post_tasks(record["storage"]))
    return post


def _read_agent_output(storage: str | Path, task_id: object, tail_bytes: int = 256 * 1024) -> str:
    """Tail of a work task's live agent-output log, or "" if none exists yet."""
    path = storage_paths.agent_output_file(task_id, storage)
    try:
        size = path.stat().st_size
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()  # drop the partial first line after the seek
            return fh.read()
    except OSError:
        return ""


def _task_status(storage: str | Path, task_id: object) -> str | None:
    """Current queue status of a task (pending/in_progress/success/failed), or None."""
    db = _queue_db(storage)
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
        row = conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _toggle_entry_reaction(record: dict, entry_id, reaction: str) -> dict:
    tracker = _tracker_for(record)
    db = _tracker_db(record["storage"])
    if _delete_one_reaction(db, "entry_reactions", "entry_id", entry_id, reaction, tracker.author):
        _touch_entry(db, entry_id)
        return {"entry_id": entry_id, "reaction": reaction, "active": False}
    data = _ok(tracker.add_entry_reaction(entry_id, reaction))
    if isinstance(data, dict):
        data["active"] = True
    return data


def _toggle_comment_reaction(record: dict, entry_id, comment_id, reaction: str) -> dict:
    tracker = _tracker_for(record)
    db = _tracker_db(record["storage"])
    if _delete_one_reaction(db, "comment_reactions", "comment_id", comment_id, reaction, tracker.author):
        _touch_entry(db, entry_id)
        return {"entry_id": entry_id, "comment_id": comment_id, "reaction": reaction, "active": False}
    data = _ok(tracker.add_entry_comment_reaction(entry_id, comment_id, reaction))
    if isinstance(data, dict):
        data["active"] = True
    return data


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "specseed/1.0"

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_PATCH(self) -> None:
        self._route()

    def do_DELETE(self) -> None:
        self._route()

    def do_PUT(self) -> None:
        self._route()

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _route(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/"):
                self._api([p for p in parsed.path.split("/") if p], parse_qs(parsed.query))
                return
            self._static(parsed.path)
        except RepoNotFound as exc:
            self._json({"ok": False, "error": f"unknown repo: {exc}"}, status=404)
        except ExternallyManaged as exc:
            self._json({"ok": False, "error": f"externally managed ({exc})"}, status=409)
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)}, status=500)

    # -- API ---------------------------------------------------------------- #
    def _api(self, parts: list[str], query: dict) -> None:
        # /api/env - global server facts (dev mode, registry home)
        if parts == ["api", "env"] and self.command == "GET":
            self._json({"ok": True, "data": env_payload()})
            return
        # /api/repos ...
        if parts[:2] == ["api", "repos"]:
            self._api_repos(parts[2:], query)
            return
        self._json({"ok": False, "error": "not found"}, status=404)

    def _api_repos(self, rest: list[str], query: dict) -> None:
        if not rest:
            if self.command == "GET":
                repos = [_repo_summary(r) for r in registry.list_repos()]
                self._json({"ok": True, "data": repos})
                return
            if self.command == "POST":
                body = self._body()
                target = str(body.get("target") or "").strip()
                provider = str(body.get("provider") or "local").strip()
                if provider not in registry.PROVIDERS:
                    raise RuntimeError(f"provider must be one of {registry.PROVIDERS}")
                try:
                    target_path = _prepare_target(target, create=bool(body.get("create")))
                except TargetMissing:
                    self._json(
                        {"ok": False, "error": "target directory does not exist", "code": "target_missing"},
                        status=404,
                    )
                    return
                record = registry.add_repo(target_path, provider=provider, name=body.get("name") or None)
                Path(record["storage"]).mkdir(parents=True, exist_ok=True)
                # remote.json + token are written by the /setup step next.
                self._json({"ok": True, "data": _repo_summary(record)}, status=201)
                return

        repo_id = rest[0]
        record = _repo(repo_id)
        tail = rest[1:]

        if not tail:
            if self.command == "GET":
                self._json({"ok": True, "data": _repo_summary(record)})
                return
            if self.command == "DELETE":
                registry.remove_repo(repo_id)
                self._json({"ok": True, "data": {"removed": repo_id}})
                return

        if tail == ["setup"] and self.command == "POST":
            self._setup(record)
            return
        if tail == ["meta"] and self.command == "GET":
            self._meta(record)
            return
        if tail == ["monitor"] and self.command == "GET":
            self._monitor(record, query)
            return
        if tail[:1] == ["work-output"] and self.command == "GET":
            self._work_output(record, tail[1:])
            return
        if tail == ["runner"] and self.command == "POST":
            self._runner(record)
            return
        if tail[:1] == ["tasks"]:
            self._tasks(record, tail[1:])
            return
        if tail == ["config"]:
            if self.command == "GET":
                self._get_config(record)
                return
            if self.command == "PUT":
                self._put_config(record)
                return
        if tail and tail[0] == "posts":
            self._posts(record, tail[1:], query)
            return
        self._json({"ok": False, "error": "not found"}, status=404)

    # -- setup / config ----------------------------------------------------- #
    def _setup(self, record: dict) -> None:
        body = self._body()
        storage = record["storage"]
        provider = record["provider"]
        remote = configure.coerce_remote_state(configure.load_remote_state(storage))
        cfg = configure.load_config(storage)
        if provider == "local":
            remote["enabled"] = False
            remote["provider"] = None
            configure.apply_identity_defaults(cfg, remote)  # human=user, platform=specseed
            configure.write_config_files(storage, cfg, remote)
        else:
            repo = str(body.get("repo") or "").strip()
            token = str(body.get("token") or "").strip()
            if not repo or not token:
                raise RuntimeError("github/gitlab need a repo (owner/name) and an access token")
            remote["enabled"] = True
            remote["provider"] = provider
            remote["repo"] = repo
            configure.apply_identity_defaults(cfg, remote)  # both default to repo owner
            configure.write_config_files(storage, cfg, remote, token=token)
        self._json({"ok": True, "data": _repo_summary(record)})

    def _meta(self, record: dict) -> None:
        data = {
            "provider": record["provider"],
            "external_link": _external_link(record),
            "reactions": sorted(SUPPORTED_REACTIONS),
            "human_labels": _human_labels(),
            "important_labels": list(IMPORTANT_LABELS),
            "default_post_titles": sorted(DEFAULT_POST_TITLES),
        }
        if record["provider"] == "local":
            data["ui_user"] = _ui_user(record["storage"])
            data["platform_username"] = _platform_user(record["storage"])
            tracker = _tracker_for(record)
            labels = _ok(tracker.list_labels())
            data["labels"] = labels.get("labels", []) if isinstance(labels, dict) else []
        self._json({"ok": True, "data": data})

    def _monitor(self, record: dict, query: dict) -> None:
        storage = record["storage"]
        status = runner_control.read_runner_status(storage)
        queue = _read_tasks(
            storage,
            queue=_page_args(query, "queue", 50),
            errors=_page_args(query, "errors", 50),
        )
        log_offset, log_limit = _page_args(query, "log", 100)
        self._json(
            {
                "ok": True,
                "data": {
                    "runner": status,
                    "queue": queue["tasks"],
                    "counts": queue["counts"],
                    "errors": queue["errors"],
                    "log": _read_log(Path(storage) / "platform.log", log_offset, log_limit),
                    "gate": _config_gate({**status, **queue["counts"]}),
                },
            }
        )

    def _work_output(self, record: dict, sub: list[str]) -> None:
        # /api/repos/<id>/work-output/<task_id> - live agent stdout for a work run.
        if not sub:
            self._json({"ok": False, "error": "task id required"}, status=404)
            return
        task_id = sub[0]
        storage = record["storage"]
        status = _task_status(storage, task_id)
        self._json(
            {
                "ok": True,
                "data": {
                    "task_id": task_id,
                    "status": status,
                    "running": status == "in_progress",
                    "text": _read_agent_output(storage, task_id),
                },
            }
        )

    def _tasks(self, record: dict, sub: list[str]) -> None:
        # /api/repos/<id>/tasks/<task_id>/retry (POST) - re-queue a failed/scheduled task
        if len(sub) == 2 and sub[1] == "retry" and self.command == "POST":
            self._json({"ok": True, "data": _retry_task(record["storage"], sub[0])})
            return
        self._json({"ok": False, "error": "not found"}, status=404)

    def _runner(self, record: dict) -> None:
        body = self._body()
        action = str(body.get("action") or "").strip()
        storage = record["storage"]
        if action == "start":
            if not (Path(storage) / "configuration.json").is_file():
                raise RuntimeError("configure the repo before starting its runner")
            data = runner_control.start_runner(record)
        elif action == "pause":
            runner_control.pause_runner(storage)
            data = runner_control.read_runner_status(storage)
        elif action == "resume":
            runner_control.resume_runner(storage)
            data = runner_control.read_runner_status(storage)
        elif action == "stop":
            data = runner_control.stop_runner(storage)
        else:
            raise RuntimeError("action must be start/pause/resume/stop")
        self._json({"ok": True, "data": data})

    def _get_config(self, record: dict) -> None:
        storage = record["storage"]
        status = runner_control.read_runner_status(storage)
        counts = _queue_counts(storage)
        self._json(
            {
                "ok": True,
                "data": {
                    "config": configure.load_config(storage),
                    "remote": configure.load_remote_state(storage),
                    "gate": _config_gate({**status, **counts}),
                    "schema": _config_schema(),
                },
            }
        )

    def _put_config(self, record: dict) -> None:
        storage = record["storage"]
        status = runner_control.read_runner_status(storage)
        counts = _queue_counts(storage)
        gate = _config_gate({**status, **counts})
        if not gate["editable"]:
            self._json({"ok": False, "error": gate["reason"]}, status=409)
            return
        body = self._body()
        cfg = configure.coerce_config(body.get("config") or configure.load_config(storage))
        remote = configure.coerce_remote_state(configure.load_remote_state(storage))
        # provider stays final; only non-provider remote fields may change here
        configure.write_config_files(storage, cfg, remote)
        self._json({"ok": True, "data": {"config": cfg}})

    # -- tracker posts (local provider only) -------------------------------- #
    def _posts(self, record: dict, tail: list[str], query: dict) -> None:
        tracker = _tracker_for(record)
        if not tail:
            if self.command == "GET":
                state = query.get("state", ["open"])[0]
                data = _ok(tracker.list_entries(is_open=_state(state)))
                self._json({"ok": True, "data": _enrich_list(record, data)})
                return
            if self.command == "POST":
                body = self._body()
                data = _ok(
                    tracker.add_entry(
                        title=str(body.get("title") or ""),
                        body=str(body.get("body") or ""),
                        labels=_csv(body.get("labels")),
                        assignees=_csv(body.get("assignees")),
                    )
                )
                self._json({"ok": True, "data": data}, status=201)
                return

        post_id = tail[0]
        sub = tail[1:]
        if not sub and self.command == "GET":
            self._json({"ok": True, "data": _enrich_one(record, _ok(tracker.get_entry(post_id)))})
            return
        if not sub and self.command == "PATCH":
            body = self._body()
            self._json({"ok": True, "data": _ok(tracker.edit_entry(post_id, title=body.get("title"), body=body.get("body")))})
            return
        if not sub and self.command == "DELETE":
            self._json({"ok": True, "data": _ok(tracker.delete_entry(post_id))})
            return
        if sub == ["toggle"] and self.command == "POST":
            current = _ok(tracker.is_entry_open(post_id))
            result = tracker.set_entry_closed(post_id) if current["is_open"] else tracker.set_entry_open(post_id)
            self._json({"ok": True, "data": _ok(result)})
            return
        if sub == ["labels"] and self.command == "POST":
            body = self._body()
            action = str(body.get("action") or "add")
            label = str(body.get("label") or "").strip()
            result = tracker.remove_entry_label(post_id, label) if action == "remove" else tracker.add_entry_label(post_id, label)
            self._json({"ok": True, "data": _ok(result)})
            return
        if sub == ["comments"] and self.command == "POST":
            body = self._body()
            self._json({"ok": True, "data": _ok(tracker.add_entry_comment(post_id, str(body.get("body") or "")))}, status=201)
            return
        if sub == ["reactions"] and self.command == "POST":
            body = self._body()
            reaction = str(body.get("reaction") or "")
            if body.get("toggle"):
                self._json({"ok": True, "data": _toggle_entry_reaction(record, post_id, reaction)})
                return
            self._json({"ok": True, "data": _ok(tracker.add_entry_reaction(post_id, reaction))}, status=201)
            return
        if len(sub) == 3 and sub[0] == "comments" and sub[2] == "reactions" and self.command == "POST":
            body = self._body()
            reaction = str(body.get("reaction") or "")
            if body.get("toggle"):
                self._json({"ok": True, "data": _toggle_comment_reaction(record, post_id, sub[1], reaction)})
                return
            self._json({"ok": True, "data": _ok(tracker.add_entry_comment_reaction(post_id, sub[1], reaction))}, status=201)
            return
        self._json({"ok": False, "error": "not found"}, status=404)

    # -- static + io -------------------------------------------------------- #
    def _static(self, path: str) -> None:
        target = ROOT / (path.lstrip("/") or "index.html")
        if not target.exists() or not target.is_file() or ROOT not in target.resolve().parents:
            target = ROOT / "index.html"
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(target.read_bytes())

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _json(self, payload: object, status: int = 200) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def serve(*, port: int | None = None, host: str = "127.0.0.1", open_browser: bool = True) -> None:
    if port is None:
        port = 5051 if registry.is_dev() else 5050
    _SERVE.update(host=host, port=port)
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"specseed UI{' (DEV)' if registry.is_dev() else ''}: {url}")
    print(f"registry: {registry.registry_file()}")
    if host in ("0.0.0.0", "::"):
        print(f"LAN (e.g. phone): http://{_lan_ip()}:{port}")
    else:
        print(f"LAN access: re-run with --host 0.0.0.0 (then http://{_lan_ip()}:{port})")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nspecseed UI stopped.")
    finally:
        httpd.server_close()


def main() -> None:
    env_port = os.environ.get("PORT")
    serve(port=int(env_port) if env_port else None)


if __name__ == "__main__":
    main()
