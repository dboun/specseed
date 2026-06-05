from __future__ import annotations

import json
import mimetypes
import os
import sys
from dataclasses import asdict, is_dataclass
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

from specseed_runtime.tracking.supported_values import SUPPORTED_REACTIONS
from specseed_runtime.tracking.tracking_remote_local import DEFAULT_DB_PATH, TrackingRemoteLocal


ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "5000"))
AUTHOR = os.environ.get("TRACKER_AUTHOR", "remote")


def _db_path() -> Path:
    return Path(os.environ.get("TRACKER_DB_PATH") or DEFAULT_DB_PATH)


def _tracker() -> TrackingRemoteLocal:
    return TrackingRemoteLocal(db_path=_db_path(), author=os.environ.get("TRACKER_AUTHOR", AUTHOR))


def _plain(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


def _ok(result: object) -> object:
    if getattr(result, "ok", False):
        return _plain(getattr(result, "data", None))
    raise RuntimeError(str(getattr(result, "error", None) or "tracking op failed"))


def _state(value: str | None) -> bool | None:
    if value == "open":
        return True
    if value == "closed":
        return False
    return None


def _csv(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_PATCH(self) -> None:
        self._route()

    def do_DELETE(self) -> None:
        self._route()

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _route(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/"):
                self._api(parsed.path, parse_qs(parsed.query))
                return
            self._static(parsed.path)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, status=500)

    def _api(self, path: str, query: dict[str, list[str]]) -> None:
        parts = [part for part in path.split("/") if part]
        if parts == ["api", "meta"] and self.command == "GET":
            self._json(
                {
                    "ok": True,
                    "data": {
                        "dbPath": str(_db_path()),
                        "reactions": sorted(SUPPORTED_REACTIONS),
                    },
                }
            )
            return

        if parts == ["api", "posts"] and self.command == "GET":
            state = query.get("state", ["open"])[0]
            data = _ok(_tracker().list_entries(is_open=_state(state)))
            self._json({"ok": True, "data": data, "meta": {"dbPath": str(_db_path())}})
            return

        if parts == ["api", "posts"] and self.command == "POST":
            body = self._body()
            data = _ok(
                _tracker().add_entry(
                    title=str(body.get("title") or ""),
                    body=str(body.get("body") or ""),
                    labels=_csv(body.get("labels")),
                    assignees=_csv(body.get("assignees")),
                )
            )
            self._json({"ok": True, "data": data}, status=201)
            return

        if len(parts) >= 3 and parts[:2] == ["api", "posts"]:
            post_id = parts[2]
            if len(parts) == 3 and self.command == "GET":
                self._json({"ok": True, "data": _ok(_tracker().get_entry(post_id))})
                return
            if len(parts) == 3 and self.command == "PATCH":
                body = self._body()
                self._json(
                    {
                        "ok": True,
                        "data": _ok(
                            _tracker().edit_entry(
                                post_id,
                                title=body.get("title"),
                                body=body.get("body"),
                            )
                        ),
                    }
                )
                return
            if len(parts) == 3 and self.command == "DELETE":
                self._json({"ok": True, "data": _ok(_tracker().delete_entry(post_id))})
                return
            if parts[3:] == ["toggle"] and self.command == "POST":
                tracker = _tracker()
                current = _ok(tracker.is_entry_open(post_id))
                result = tracker.set_entry_closed(post_id) if current["is_open"] else tracker.set_entry_open(post_id)
                self._json({"ok": True, "data": _ok(result)})
                return
            if parts[3:] == ["labels"] and self.command == "POST":
                body = self._body()
                action = str(body.get("action") or "add")
                label = str(body.get("label") or "").strip()
                result = (
                    _tracker().remove_entry_label(post_id, label)
                    if action == "remove"
                    else _tracker().add_entry_label(post_id, label)
                )
                self._json({"ok": True, "data": _ok(result)})
                return
            if parts[3:] == ["comments"] and self.command == "POST":
                body = self._body()
                self._json(
                    {
                        "ok": True,
                        "data": _ok(_tracker().add_entry_comment(post_id, str(body.get("body") or ""))),
                    },
                    status=201,
                )
                return
            if parts[3:] == ["reactions"] and self.command == "POST":
                body = self._body()
                self._json(
                    {
                        "ok": True,
                        "data": _ok(_tracker().add_entry_reaction(post_id, str(body.get("reaction") or ""))),
                    },
                    status=201,
                )
                return
            if len(parts) == 6 and parts[3] == "comments" and parts[5] == "reactions" and self.command == "POST":
                body = self._body()
                self._json(
                    {
                        "ok": True,
                        "data": _ok(
                            _tracker().add_entry_comment_reaction(
                                post_id,
                                parts[4],
                                str(body.get("reaction") or ""),
                            )
                        ),
                    },
                    status=201,
                )
                return

        self._json({"ok": False, "error": "not found"}, status=404)

    def _static(self, path: str) -> None:
        target = ROOT / (path.lstrip("/") or "index.html")
        if not target.exists() or not target.is_file() or ROOT not in target.resolve().parents:
            target = ROOT / "index.html"
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(target.read_bytes())

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _json(self, payload: object, status: int = 200) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"specseed tracker UI: http://127.0.0.1:{PORT}")
    print(f"TRACKER_DB_PATH={_db_path()}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
