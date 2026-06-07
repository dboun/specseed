"""test_ui_server.py - pagination helpers of the shared web UI server.

Loads ``src/ui/server.py`` the same way the launcher does (file location, not
a package import) and exercises the read-only monitor data functions against
a throwaway sqlite queue + JSONL log.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

_SERVER_PATH = Path(__file__).resolve().parents[3] / "src" / "ui" / "server.py"
_spec = importlib.util.spec_from_file_location("specseed_web_server_test", _SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


def _make_queue_db(storage: Path, tasks: int = 0, errors: int = 0) -> None:
    db = storage / "specseed.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL, post_id TEXT,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, last_attempted_at TEXT,
            not_before TEXT
        );
        CREATE TABLE task_errors (
            error_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL, message TEXT NOT NULL, executed_at TEXT NOT NULL
        );
        """
    )
    for i in range(1, tasks + 1):
        status = "pending" if i % 2 else "success"
        conn.execute(
            "INSERT INTO tasks(action, post_id, status, created_at) VALUES (?, ?, ?, ?)",
            (f"act_{i}", str(i), status, "2026-01-01T00:00:00Z"),
        )
    for i in range(1, errors + 1):
        conn.execute(
            "INSERT INTO task_errors(task_id, message, executed_at) VALUES (?, ?, ?)",
            (i, f"boom {i}", "2026-01-01T00:00:00Z"),
        )
    conn.commit()
    conn.close()


class ReadTasksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)

    def test_missing_db_returns_empty_pages(self) -> None:
        out = server._read_tasks(self.storage, queue=(0, 10), errors=(5, 10))
        self.assertEqual(out["tasks"], {"items": [], "total": 0, "offset": 0, "limit": 10})
        self.assertEqual(out["errors"], {"items": [], "total": 0, "offset": 5, "limit": 10})
        self.assertEqual(out["counts"]["pending"], 0)

    def test_queue_pages_newest_first_with_totals(self) -> None:
        _make_queue_db(self.storage, tasks=7)
        page1 = server._read_tasks(self.storage, queue=(0, 3))
        self.assertEqual(page1["tasks"]["total"], 7)
        self.assertEqual([t["task_id"] for t in page1["tasks"]["items"]], [7, 6, 5])
        page2 = server._read_tasks(self.storage, queue=(3, 3))
        self.assertEqual([t["task_id"] for t in page2["tasks"]["items"]], [4, 3, 2])
        last = server._read_tasks(self.storage, queue=(6, 3))
        self.assertEqual([t["task_id"] for t in last["tasks"]["items"]], [1])
        past = server._read_tasks(self.storage, queue=(99, 3))
        self.assertEqual(past["tasks"]["items"], [])
        self.assertEqual(past["tasks"]["total"], 7)

    def test_queue_page_includes_pending(self) -> None:
        _make_queue_db(self.storage, tasks=4)
        out = server._read_tasks(self.storage, queue=(0, 10))
        statuses = {t["status"] for t in out["tasks"]["items"]}
        self.assertIn("pending", statuses)
        self.assertEqual(out["counts"]["pending"], 2)
        self.assertEqual(out["counts"]["success"], 2)

    def test_errors_paged_with_task_join(self) -> None:
        _make_queue_db(self.storage, tasks=5, errors=5)
        out = server._read_tasks(self.storage, errors=(2, 2))
        self.assertEqual(out["errors"]["total"], 5)
        self.assertEqual([e["error_id"] for e in out["errors"]["items"]], [3, 2])
        self.assertEqual(out["errors"]["items"][0]["action"], "act_3")


class QueueCountsTest(unittest.TestCase):
    def test_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            self.assertEqual(server._queue_counts(storage)["pending"], 0)
            _make_queue_db(storage, tasks=3)
            counts = server._queue_counts(storage)
            self.assertEqual(counts["pending"], 2)
            self.assertEqual(counts["success"], 1)


class ReadLogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "platform.log"

    def _write(self, n: int) -> None:
        lines = [json.dumps({"ts": f"t{i}", "event": f"e{i}"}) for i in range(1, n + 1)]
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_missing_file(self) -> None:
        out = server._read_log(self.log, 0, 10)
        self.assertEqual(out, {"items": [], "total": 0, "offset": 0, "limit": 10})

    def test_newest_first_pages(self) -> None:
        self._write(10)
        page1 = server._read_log(self.log, 0, 4)
        self.assertEqual(page1["total"], 10)
        self.assertEqual([l["event"] for l in page1["items"]], ["e10", "e9", "e8", "e7"])
        page2 = server._read_log(self.log, 4, 4)
        self.assertEqual([l["event"] for l in page2["items"]], ["e6", "e5", "e4", "e3"])
        last = server._read_log(self.log, 8, 4)
        self.assertEqual([l["event"] for l in last["items"]], ["e2", "e1"])
        past = server._read_log(self.log, 50, 4)
        self.assertEqual(past["items"], [])
        self.assertEqual(past["total"], 10)

    def test_non_json_line_kept_raw(self) -> None:
        self.log.write_text("not json\n", encoding="utf-8")
        out = server._read_log(self.log, 0, 10)
        self.assertEqual(out["items"], [{"raw": "not json"}])


class EnrichListTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)
        self.record = {"storage": str(self.storage)}

    def _make_tracker_db(self, rows) -> None:
        # rows: (entry_id, body, updated_at)
        conn = sqlite3.connect(self.storage / "tracking_remote_local.db")
        conn.execute(
            "CREATE TABLE comments (id INTEGER PRIMARY KEY, entry_id INTEGER, body TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO comments(entry_id, body, created_at, updated_at) VALUES (?, ?, ?, ?)",
            [(e, b, u, u) for (e, b, u) in rows],
        )
        conn.commit()
        conn.close()

    def test_stamps_counts_text_and_activity(self) -> None:
        self._make_tracker_db(
            [(1, "hello", "2026-01-02T00:00:00Z"), (1, "world", "2026-01-03T00:00:00Z"), (3, "c", "2026-01-01T00:00:00Z")]
        )
        posts = [
            {"id": 1, "updated_at": "2026-01-01T00:00:00Z"},
            {"id": 2, "updated_at": "2026-02-01T00:00:00Z"},
            {"id": 3, "updated_at": "2026-01-05T00:00:00Z"},
        ]
        out = server._enrich_list(self.record, posts)
        self.assertEqual([p["comment_count"] for p in out], [2, 0, 1])
        # search blob carries both comment bodies for post 1
        self.assertIn("hello", out[0]["comments_text"])
        self.assertIn("world", out[0]["comments_text"])
        # last_activity_at = newest of entry vs comments
        self.assertEqual(out[0]["last_activity_at"], "2026-01-03T00:00:00Z")  # latest comment
        self.assertEqual(out[1]["last_activity_at"], "2026-02-01T00:00:00Z")  # no comments -> entry
        self.assertEqual(out[2]["last_activity_at"], "2026-01-05T00:00:00Z")  # entry newer than comment

    def test_needs_approval_from_labels(self) -> None:
        posts = [
            {"id": 1, "labels": [{"name": "spec-change:status:awaiting_approval"}]},
            {"id": 2, "labels": [{"name": "issue:status:awaiting_approval"}]},
            {"id": 3, "labels": [{"name": "issue:status:todo"}]},
            {"id": 4},
        ]
        out = server._enrich_list(self.record, posts)
        self.assertEqual([p["needs_approval"] for p in out], [True, True, False, False])

    def test_missing_db_defaults_zero(self) -> None:
        posts = [{"id": 1}]
        out = server._enrich_list(self.record, posts)
        self.assertEqual(out[0]["comment_count"], 0)
        self.assertEqual(out[0]["comments_text"], "")

    def test_non_list_passthrough(self) -> None:
        self.assertEqual(server._enrich_list(self.record, {"x": 1}), {"x": 1})

    def test_important_labels_constant(self) -> None:
        # Drives every label picker; draft present, type:/difficulty: absent.
        self.assertIn("draft", server.IMPORTANT_LABELS)
        self.assertTrue(all(not n.startswith(("type:", "difficulty:")) for n in server.IMPORTANT_LABELS))


class InFutureTest(unittest.TestCase):
    def test_none_and_blank_are_not_future(self) -> None:
        self.assertFalse(server._in_future(None))
        self.assertFalse(server._in_future(""))

    def test_past_and_future(self) -> None:
        self.assertFalse(server._in_future("2000-01-01T00:00:00Z"))
        self.assertTrue(server._in_future("2999-01-01T00:00:00Z"))

    def test_garbage_is_not_future(self) -> None:
        self.assertFalse(server._in_future("not-a-date"))


class RetryTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)

    def _seed(self) -> None:
        db = self.storage / "specseed.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL, post_id TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, last_attempted_at TEXT, not_before TEXT
            );
            """
        )
        rows = [
            ("failed", "failed", 3, None),
            ("sched", "pending", 2, "2999-01-01T00:00:00Z"),
            ("due", "pending", 0, None),
            ("running", "in_progress", 1, None),
            ("ok", "success", 1, None),
        ]
        for action, status, attempts, nb in rows:
            conn.execute(
                "INSERT INTO tasks(action, status, attempts, created_at, not_before) VALUES (?,?,?,?,?)",
                (action, status, attempts, "2026-01-01T00:00:00Z", nb),
            )
        conn.commit()
        conn.close()

    def _row(self, task_id: int) -> dict:
        conn = sqlite3.connect(self.storage / "specseed.db")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        conn.close()
        return dict(row)

    def test_missing_db_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            server._retry_task(self.storage, 1)

    def test_failed_task_requeued_attempts_preserved(self) -> None:
        self._seed()
        out = server._retry_task(self.storage, 1)
        self.assertEqual(out["status"], "pending")
        self.assertFalse(out["pulled_forward"])
        row = self._row(1)
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["not_before"])
        self.assertEqual(row["attempts"], 3)  # attempts preserved (same task)

    def test_scheduled_pending_pulled_forward(self) -> None:
        self._seed()
        out = server._retry_task(self.storage, 2)
        self.assertTrue(out["pulled_forward"])
        self.assertIsNone(self._row(2)["not_before"])

    def test_due_pending_rejected(self) -> None:
        self._seed()
        with self.assertRaises(RuntimeError):
            server._retry_task(self.storage, 3)

    def test_in_progress_rejected(self) -> None:
        self._seed()
        with self.assertRaises(RuntimeError):
            server._retry_task(self.storage, 4)

    def test_success_rejected(self) -> None:
        self._seed()
        with self.assertRaises(RuntimeError):
            server._retry_task(self.storage, 5)

    def test_unknown_task_raises(self) -> None:
        self._seed()
        with self.assertRaises(RuntimeError):
            server._retry_task(self.storage, 999)


class PageArgsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertEqual(server._page_args({}, "queue", 50), (0, 50))

    def test_parses_and_clamps(self) -> None:
        q = {"queue_offset": ["25"], "queue_limit": ["10"]}
        self.assertEqual(server._page_args(q, "queue", 50), (25, 10))
        self.assertEqual(server._page_args({"q_limit": ["9999"]}, "q", 50), (0, 500))
        self.assertEqual(server._page_args({"q_limit": ["0"]}, "q", 50), (0, 1))
        self.assertEqual(server._page_args({"q_offset": ["-5"]}, "q", 50), (0, 50))

    def test_bad_input_falls_back(self) -> None:
        self.assertEqual(server._page_args({"q_offset": ["nope"]}, "q", 50), (0, 50))


if __name__ == "__main__":
    unittest.main()
