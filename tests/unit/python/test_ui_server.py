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


def _make_queue_db(storage: Path, tasks: int = 0, errors: int = 0, *, old_shape: bool = False) -> None:
    db = server._queue_db(storage)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    lane_cols = "" if old_shape else ", lane TEXT NOT NULL DEFAULT 'control', priority INTEGER NOT NULL DEFAULT 50"
    conn.executescript(
        f"""
        CREATE TABLE tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL, post_id TEXT,
            payload TEXT NOT NULL DEFAULT '{{}}',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, last_attempted_at TEXT,
            not_before TEXT
            {lane_cols}
        );
        CREATE TABLE task_errors (
            error_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL, message TEXT NOT NULL, executed_at TEXT NOT NULL
        );
        """
    )
    for i in range(1, tasks + 1):
        status = "pending" if i % 2 else "success"
        lane = "work" if i % 3 == 0 else "control"
        conn.execute(
            "INSERT INTO tasks(action, post_id, status, created_at"
            + (") VALUES (?, ?, ?, ?)" if old_shape else ", lane, priority) VALUES (?, ?, ?, ?, ?, ?)"),
            (f"act_{i}", str(i), status, "2026-01-01T00:00:00Z")
            if old_shape
            else (f"act_{i}", str(i), status, "2026-01-01T00:00:00Z", lane, 50 + i),
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
        self.assertEqual(out["counts"]["lanes"]["control"]["pending"], 1)
        self.assertEqual(out["counts"]["lanes"]["work"]["pending"], 1)

    def test_queue_page_includes_lane_and_priority(self) -> None:
        _make_queue_db(self.storage, tasks=3)
        out = server._read_tasks(self.storage, queue=(0, 1))
        row = out["tasks"]["items"][0]
        self.assertEqual(row["lane"], "work")
        self.assertEqual(row["priority"], 53)

    def test_old_shape_queue_defaults_to_control_lane(self) -> None:
        _make_queue_db(self.storage, tasks=2, old_shape=True)
        out = server._read_tasks(self.storage, queue=(0, 10))
        self.assertEqual({t["lane"] for t in out["tasks"]["items"]}, {"control"})
        self.assertEqual({t["priority"] for t in out["tasks"]["items"]}, {50})
        self.assertEqual(out["counts"]["lanes"]["control"]["pending"], 1)
        self.assertEqual(out["counts"]["lanes"]["work"]["pending"], 0)

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
            self.assertEqual(counts["lanes"]["control"]["pending"], 1)
            self.assertEqual(counts["lanes"]["work"]["pending"], 1)
            self.assertEqual(counts["lanes"]["work"]["success"], 0)


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
        tdb = server._tracker_db(self.storage)
        tdb.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(tdb)
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

    def test_important_labels_offer_ask_not_question(self) -> None:
        # question was renamed to ask (Phase 3); the picker must follow.
        self.assertIn("ask", server.IMPORTANT_LABELS)
        self.assertNotIn("question", server.IMPORTANT_LABELS)

    def test_important_labels_are_real_supported_labels(self) -> None:
        # Curated subset must not drift from the runtime taxonomy.
        from specseed_runtime.tracking.supported_values import SUPPORTED_LABELS

        self.assertTrue(set(server.IMPORTANT_LABELS) <= set(SUPPORTED_LABELS))

    def test_human_labels_offer_ask_not_question(self) -> None:
        labels = server._human_labels()
        self.assertIn("ask", labels)
        self.assertNotIn("question", labels)


def _make_queue_rows(storage: Path, rows) -> None:
    """rows: (post_id, status, lane). Full (lane+priority) schema."""
    db = server._queue_db(storage)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL, post_id TEXT,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, last_attempted_at TEXT, not_before TEXT,
            lane TEXT NOT NULL DEFAULT 'control', priority INTEGER NOT NULL DEFAULT 50
        );
        CREATE TABLE task_errors (
            error_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL, message TEXT NOT NULL, executed_at TEXT NOT NULL
        );
        """
    )
    for post_id, status, lane in rows:
        conn.execute(
            "INSERT INTO tasks(action, post_id, status, created_at, lane) VALUES (?, ?, ?, ?, ?)",
            ("work_run", post_id, status, "2026-01-01T00:00:00Z", lane),
        )
    conn.commit()
    conn.close()


class AgentOutputServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)
        self.record = {"storage": str(self.storage)}

    def test_in_progress_post_tasks_maps_running_work_to_task_id(self) -> None:
        # 1: post "7" work in_progress (task 1); 2: control in_progress (ignored);
        # 3: post "7" pending (ignored). Newest in_progress work wins per post.
        _make_queue_rows(self.storage, [("7", "in_progress", "work"),
                                        ("8", "in_progress", "control"),
                                        ("7", "pending", "work")])
        mapping = server._in_progress_post_tasks(self.storage)
        self.assertEqual(mapping, {"7": "1"})
        self.assertEqual(server._in_progress_post_ids(self.storage), {"7"})

    def test_enrich_one_stamps_agent_fields(self) -> None:
        _make_queue_rows(self.storage, [("7", "in_progress", "work")])
        running = server._enrich_one(self.record, {"id": 7})
        self.assertTrue(running["agent_running"])
        self.assertEqual(running["agent_task_id"], "1")
        idle = server._enrich_one(self.record, {"id": 99})
        self.assertFalse(idle["agent_running"])
        self.assertIsNone(idle["agent_task_id"])

    def test_enrich_one_non_dict_passthrough(self) -> None:
        self.assertEqual(server._enrich_one(self.record, None), None)

    def test_read_agent_output_reads_log_else_empty(self) -> None:
        self.assertEqual(server._read_agent_output(self.storage, 1), "")
        out = server.storage_paths.agent_output_dir(self.storage)
        out.mkdir(parents=True)
        (out / "1.log").write_text("● Read(a.py)\n● done\n", encoding="utf-8")
        self.assertEqual(server._read_agent_output(self.storage, 1), "● Read(a.py)\n● done\n")

    def test_read_agent_output_tails_large_file(self) -> None:
        out = server.storage_paths.agent_output_dir(self.storage)
        out.mkdir(parents=True)
        (out / "1.log").write_text("\n".join(f"line {i}" for i in range(100000)), encoding="utf-8")
        tail = server._read_agent_output(self.storage, 1, tail_bytes=200)
        self.assertLessEqual(len(tail.encode("utf-8")), 200)
        self.assertTrue(tail.endswith("line 99999"))

    def test_task_status(self) -> None:
        self.assertIsNone(server._task_status(self.storage, 1))  # no db
        _make_queue_rows(self.storage, [("7", "in_progress", "work"), ("8", "success", "work")])
        self.assertEqual(server._task_status(self.storage, 1), "in_progress")
        self.assertEqual(server._task_status(self.storage, 2), "success")
        self.assertIsNone(server._task_status(self.storage, 999))

    def test_read_tasks_flags_has_output(self) -> None:
        _make_queue_rows(self.storage, [("7", "in_progress", "work"), ("8", "success", "work")])
        out = server.storage_paths.agent_output_dir(self.storage)
        out.mkdir(parents=True)
        (out / "1.log").write_text("x", encoding="utf-8")  # task 1 has a log; task 2 doesn't
        items = {it["task_id"]: it["has_output"] for it in server._read_tasks(self.storage)["tasks"]["items"]}
        self.assertTrue(items[1])
        self.assertFalse(items[2])


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
        db = server._queue_db(self.storage)
        db.parent.mkdir(parents=True, exist_ok=True)
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
        conn = sqlite3.connect(server._queue_db(self.storage))
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


class InProgressPostIdsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name)
        _make_queue_db(self.storage)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _insert(self, post_id, status, lane="work") -> None:
        conn = sqlite3.connect(server._queue_db(self.storage))
        conn.execute(
            "INSERT INTO tasks(action, post_id, status, created_at, lane) VALUES (?, ?, ?, ?, ?)",
            ("act", post_id, status, "2026-01-01T00:00:00Z", lane),
        )
        conn.commit()
        conn.close()

    def test_only_work_lane_in_progress_posts_reported(self) -> None:
        self._insert("7", "in_progress")
        self._insert("8", "pending")
        self._insert("9", "success")
        self._insert("10", "in_progress", lane="control")
        self._insert(None, "in_progress")  # no post: ignored
        self.assertEqual(server._in_progress_post_ids(self.storage), {"7"})

    def test_old_shape_in_progress_posts_still_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            _make_queue_db(storage, old_shape=True)
            conn = sqlite3.connect(server._queue_db(storage))
            conn.execute(
                "INSERT INTO tasks(action, post_id, status, created_at) VALUES (?, ?, ?, ?)",
                ("act", "7", "in_progress", "2026-01-01T00:00:00Z"),
            )
            conn.commit()
            conn.close()
            self.assertEqual(server._in_progress_post_ids(storage), {"7"})

    def test_missing_db_degrades_to_empty(self) -> None:
        self.assertEqual(server._in_progress_post_ids(self.storage / "nope"), set())


class RepoSummaryQueueTest(unittest.TestCase):
    def test_summary_carries_live_queue_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            _make_queue_db(storage, tasks=3)  # statuses alternate pending/success
            record = {
                "id": "r1",
                "name": "repo",
                "provider": "local",
                "target": str(storage),
                "storage": str(storage),
            }
            summary = server._repo_summary(record)
            self.assertEqual(summary["queue"]["pending"], 2)
            self.assertEqual(summary["queue"]["in_progress"], 0)
            self.assertEqual(summary["queue"]["lanes"]["control"]["pending"], 1)
            self.assertEqual(summary["queue"]["lanes"]["work"]["pending"], 1)

    def test_summary_queue_zero_without_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record = {
                "id": "r1",
                "name": "repo",
                "provider": "local",
                "target": tmp,
                "storage": tmp,
            }
            summary = server._repo_summary(record)
            self.assertEqual(summary["queue"]["pending"], 0)
            self.assertEqual(summary["queue"]["in_progress"], 0)
            self.assertEqual(summary["queue"]["lanes"]["work"]["pending"], 0)


class PrepareTargetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_existing_dir_passes_through(self) -> None:
        self.assertEqual(server._prepare_target(str(self.root)), self.root)

    def test_tilde_expands_to_home(self) -> None:
        import os

        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.root)
        try:
            (self.root / "repo").mkdir()
            self.assertEqual(server._prepare_target("~/repo"), self.root / "repo")
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

    def test_missing_without_create_raises_target_missing(self) -> None:
        with self.assertRaises(server.TargetMissing):
            server._prepare_target(str(self.root / "nope"))

    def test_missing_with_create_makes_dir(self) -> None:
        path = self.root / "deep" / "new_repo"
        self.assertEqual(server._prepare_target(str(path), create=True), path)
        self.assertTrue(path.is_dir())

    def test_empty_target_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            server._prepare_target("")


class SpecDocsTest(unittest.TestCase):
    """The Spec tab reads <specseed_dir>/spec — sibling of storage. Dumb listing:
    whatever files exist, served by relative path, with a traversal guard."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # New layout: the spec dir is <data_root>/spec (storage == the data root).
        # resolve() to match _spec_dir (macOS /var -> /private/var symlink).
        self.root = Path(self.tmp.name).resolve()
        self.storage = self.root / "data_root"
        self.storage.mkdir()
        self.spec = self.storage / "spec"
        self.record = {"storage": str(self.storage)}

    def _write(self, rel: str, text: str) -> Path:
        path = self.spec / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_spec_dir_under_data_root(self) -> None:
        self.assertEqual(server._spec_dir(self.record), self.spec)

    def test_list_missing_dir(self) -> None:
        out = server._spec_list(self.record)
        self.assertFalse(out["exists"])
        self.assertEqual(out["files"], [])
        self.assertEqual(out["dir"], str(self.spec))

    def test_list_files_sorted_with_metadata(self) -> None:
        self._write("vision.md", "# vision")
        self._write("adr.csv", "Decision,Why\n")
        self._write("nested/extra.txt", "hi")
        out = server._spec_list(self.record)
        self.assertTrue(out["exists"])
        paths = [f["path"] for f in out["files"]]
        self.assertEqual(paths, ["adr.csv", "nested/extra.txt", "vision.md"])
        adr = next(f for f in out["files"] if f["path"] == "adr.csv")
        self.assertEqual(adr["ext"], "csv")
        self.assertGreater(adr["size"], 0)
        self.assertTrue(adr["mtime"].endswith("Z"))

    def test_list_ignores_directories(self) -> None:
        (self.spec / "empty_dir").mkdir(parents=True)
        self._write("vision.md", "x")
        out = server._spec_list(self.record)
        self.assertEqual([f["path"] for f in out["files"]], ["vision.md"])

    def test_file_returns_text_and_meta(self) -> None:
        self._write("srs.md", "# SRS\n\nbody")
        out = server._spec_file(self.record, "srs.md")
        self.assertEqual(out["path"], "srs.md")
        self.assertEqual(out["ext"], "md")
        self.assertEqual(out["text"], "# SRS\n\nbody")
        self.assertFalse(out["truncated"])

    def test_file_nested_path(self) -> None:
        self._write("a/b/c.md", "deep")
        out = server._spec_file(self.record, "a/b/c.md")
        self.assertEqual(out["path"], "a/b/c.md")
        self.assertEqual(out["text"], "deep")

    def test_file_requires_path(self) -> None:
        with self.assertRaises(RuntimeError):
            server._spec_file(self.record, "")

    def test_file_missing_raises(self) -> None:
        self.spec.mkdir()
        with self.assertRaises(RuntimeError):
            server._spec_file(self.record, "nope.md")

    def test_file_traversal_blocked(self) -> None:
        # a secret next to the spec dir must not be reachable via ../
        (self.root / "secret.txt").write_text("nope", encoding="utf-8")
        self._write("vision.md", "x")
        with self.assertRaises(RuntimeError):
            server._spec_file(self.record, "../secret.txt")

    def test_file_absolute_path_blocked(self) -> None:
        self._write("vision.md", "x")
        with self.assertRaises(RuntimeError):
            server._spec_file(self.record, "/etc/hosts")


import os
import shutil
import subprocess


@unittest.skipUnless(shutil.which("git"), "git not installed")
class CodeViewerTest(unittest.TestCase):
    """The Code tab reads the target repo's OWN git. We build a throwaway repo
    with subprocess git (no network, no agent) and exercise the read helpers."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / "repo"
        self.repo.mkdir()
        self.record = {"target": str(self.repo)}
        self._init()

    # -- fixture builder ------------------------------------------------- #
    def _git(self, *args: str) -> str:
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
        }
        out = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, env=env, check=True,
        )
        return out.stdout.decode()

    def _write(self, rel: str, text: str) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _init(self) -> None:
        self._git("init", "-b", "main")
        self._write("README.md", "# Title\n\nroot readme")
        self._write("src/app.py", "print('hi')\n")
        self._write("src/util.py", "x = 1\n")
        self._git("add", "-A")
        self._git("commit", "-m", "first commit")
        # a .gitignored file must never surface in the tree
        self._write(".gitignore", "secret.txt\n")
        self._write("secret.txt", "do not show me")
        self._write("src/app.py", "print('hello')\n")  # modify for a diff
        self._git("add", "-A")
        self._git("commit", "-m", "second commit")
        self._git("branch", "feature")

    # -- meta ------------------------------------------------------------ #
    def test_meta_reports_git_facts(self) -> None:
        m = server._code_meta(self.record)
        self.assertTrue(m["is_git"])
        self.assertFalse(m["empty"])
        self.assertEqual(m["head"], "main")
        self.assertEqual(m["default_ref"], "main")
        self.assertIn("main", m["branches"])
        self.assertIn("feature", m["branches"])

    def test_meta_non_git_dir(self) -> None:
        # Stop git's upward search at the temp dir so the host's own git state
        # (on macOS even $TMPDIR can sit inside a repo) can't leak in.
        with tempfile.TemporaryDirectory() as plain:
            old = os.environ.get("GIT_CEILING_DIRECTORIES")
            os.environ["GIT_CEILING_DIRECTORIES"] = str(Path(plain).resolve().parent)
            try:
                m = server._code_meta({"target": plain})
            finally:
                if old is None:
                    os.environ.pop("GIT_CEILING_DIRECTORIES", None)
                else:
                    os.environ["GIT_CEILING_DIRECTORIES"] = old
            self.assertFalse(m["is_git"])
            self.assertEqual(m["branches"], [])

    # -- tree ------------------------------------------------------------ #
    def test_tree_root_lists_dirs_first_and_renders_readme(self) -> None:
        t = server._code_tree(self.record, "main", "")
        names = [(e["type"], e["name"]) for e in t["entries"]]
        # src (tree) sorts before the files; .gitignore is tracked so it shows,
        # but secret.txt (ignored, untracked) must NOT.
        self.assertEqual(names[0], ("tree", "src"))
        flat = [e["name"] for e in t["entries"]]
        self.assertIn("README.md", flat)
        self.assertNotIn("secret.txt", flat)
        self.assertIsNotNone(t["readme"])
        self.assertEqual(t["readme"]["name"], "README.md")
        self.assertIn("root readme", t["readme"]["text"])

    def test_tree_subdir(self) -> None:
        t = server._code_tree(self.record, "main", "src")
        self.assertEqual(sorted(e["name"] for e in t["entries"]), ["app.py", "util.py"])
        self.assertEqual(t["entries"][0]["path"], "src/app.py")
        self.assertIsNone(t["readme"])

    def test_tree_bad_dir_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            server._code_tree(self.record, "main", "nope")

    # -- blob ------------------------------------------------------------ #
    def test_blob_text(self) -> None:
        b = server._code_blob(self.record, "main", "src/app.py")
        self.assertEqual(b["text"], "print('hello')\n")
        self.assertFalse(b["binary"])
        self.assertEqual(b["ext"], "py")
        self.assertGreater(b["size"], 0)

    def test_blob_at_older_ref(self) -> None:
        # the first commit still had the original line - addressed by ref
        first = server._code_commits(self.record, "main", 0, 10)["items"][-1]["short"]
        b = server._code_blob(self.record, first, "src/app.py")
        self.assertEqual(b["text"], "print('hi')\n")

    def test_blob_binary_detected(self) -> None:
        (self.repo / "blob.bin").write_bytes(b"\x00\x01\x02ABC")
        self._git("add", "-A")
        self._git("commit", "-m", "add binary")
        b = server._code_blob(self.record, "main", "blob.bin")
        self.assertTrue(b["binary"])
        self.assertEqual(b["text"], "")

    def test_blob_on_dir_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            server._code_blob(self.record, "main", "src")

    # -- commits --------------------------------------------------------- #
    def test_commits_paginated_newest_first(self) -> None:
        c = server._code_commits(self.record, "main", 0, 1)
        self.assertEqual(c["total"], 2)
        self.assertEqual(c["items"][0]["subject"], "second commit")
        self.assertTrue(c["has_more"])
        page2 = server._code_commits(self.record, "main", 1, 1)
        self.assertEqual(page2["items"][0]["subject"], "first commit")
        self.assertFalse(page2["has_more"])

    # -- single commit --------------------------------------------------- #
    def test_commit_meta_files_and_patch(self) -> None:
        head = server._code_commits(self.record, "main", 0, 1)["items"][0]["sha"]
        cm = server._code_commit(self.record, head)
        self.assertEqual(cm["subject"], "second commit")
        changed = {f["path"] for f in cm["files"]}
        self.assertIn("src/app.py", changed)
        self.assertIn("+print('hello')", cm["patch"])
        self.assertFalse(cm["truncated"])

    def test_commit_root_commit_shows_full_add(self) -> None:
        first = server._code_commits(self.record, "main", 0, 10)["items"][-1]["sha"]
        cm = server._code_commit(self.record, first)
        statuses = {f["status"] for f in cm["files"]}
        self.assertEqual(statuses, {"A"})  # --root makes the first commit all-adds

    def test_commit_bad_sha_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            server._code_commit(self.record, "deadbeef")

    # -- compare --------------------------------------------------------- #
    def test_compare_two_commits(self) -> None:
        items = server._code_commits(self.record, "main", 0, 10)["items"]
        base, head = items[-1]["short"], items[0]["short"]
        cp = server._code_compare(self.record, base, head)
        self.assertEqual(cp["ahead"], 1)
        self.assertEqual(cp["base"], base)
        self.assertIn("src/app.py", {f["path"] for f in cp["files"]})
        self.assertIn(".gitignore", {f["path"] for f in cp["files"]})

    # -- primary-branch default ------------------------------------------ #
    def test_meta_default_ref_follows_primary_branch(self) -> None:
        storage = self.repo.parent / "data_root"
        storage.mkdir()
        record = {"target": str(self.repo), "storage": str(storage)}
        cfg = server.storage_paths.config_file(storage)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        # HEAD is "main", but the configured primary is "feature" -> default to it
        cfg.write_text(json.dumps({"specseed_primary_branch": "feature"}))
        m = server._code_meta(record)
        self.assertEqual(m["primary_branch"], "feature")
        self.assertEqual(m["default_ref"], "feature")
        self.assertEqual(m["head"], "main")  # head still reports the checkout
        # a primary that doesn't exist locally falls back to HEAD
        cfg.write_text(json.dumps({"specseed_primary_branch": "ghost"}))
        self.assertEqual(server._code_meta(record)["default_ref"], "main")

    # -- working tree (opt-in) ------------------------------------------- #
    def test_meta_reports_clean_then_dirty(self) -> None:
        m = server._code_meta(self.record)
        self.assertFalse(m["dirty"])
        self.assertEqual(m["worktree_branch"], "main")
        # stage a new file -> dirty flips, so the UI offers the toggle
        self._write("src/new.py", "y = 2\n")
        self._git("add", "src/new.py")
        self.assertTrue(server._code_meta(self.record)["dirty"])

    def test_worktree_tree_shows_uncommitted_but_not_ignored(self) -> None:
        self._write("src/new.py", "y = 2\n")  # untracked, not committed
        self._git("add", "src/new.py")
        # committed tree (default) doesn't have it; working tree does
        committed = [e["name"] for e in server._code_tree(self.record, "main", "src")["entries"]]
        self.assertNotIn("new.py", committed)
        wt = server._code_tree(self.record, "main", "src", worktree=True)
        self.assertTrue(wt["working_tree"])
        self.assertIn("new.py", [e["name"] for e in wt["entries"]])
        # gitignored secret.txt never shows even in the working tree
        root = server._code_tree(self.record, "main", "", worktree=True)
        self.assertNotIn("secret.txt", [e["name"] for e in root["entries"]])

    def test_worktree_blob_reads_disk_and_rejects_ignored(self) -> None:
        self._write("src/app.py", "print('WIP')\n")  # modified, uncommitted
        b = server._code_blob(self.record, "main", "src/app.py", worktree=True)
        self.assertTrue(b["working_tree"])
        self.assertEqual(b["text"], "print('WIP')\n")
        with self.assertRaises(RuntimeError):
            server._code_blob(self.record, "main", "secret.txt", worktree=True)

    def test_worktree_ignored_for_non_checked_out_ref(self) -> None:
        self._write("src/app.py", "print('WIP')\n")  # only on disk for main
        # asking for the working tree of a DIFFERENT branch falls back to committed
        b = server._code_blob(self.record, "feature", "src/app.py", worktree=True)
        self.assertFalse(b["working_tree"])
        self.assertEqual(b["text"], "print('hello')\n")

    # -- validation ------------------------------------------------------ #
    def test_check_ref(self) -> None:
        self.assertEqual(server._check_ref(""), "HEAD")
        self.assertEqual(server._check_ref("feature/x"), "feature/x")
        for bad in ["--upload-pack=x", "a..b", "a b", "-rf"]:
            with self.assertRaises(RuntimeError):
                server._check_ref(bad)

    def test_check_path(self) -> None:
        self.assertEqual(server._check_path("/src/app.py"), "src/app.py")
        self.assertEqual(server._check_path(""), "")
        for bad in ["../x", "a/../b", "a//b"]:
            with self.assertRaises(RuntimeError):
                server._check_path(bad)


if __name__ == "__main__":
    unittest.main()
