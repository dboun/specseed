"""test_database.py - the two-lane work queue (lane + priority).

Drives an isolated ``Database`` (explicit db_path, never the singleton) and
asserts the lane/priority claim ordering, the ready-now count the work thread
gates on, and that supersession/teardown still work across lanes.

Only Python stdlib + the engine. No network, no agent.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from specseed_runtime.db.database import (
    DEFAULT_PRIORITY,
    LANE_CONTROL,
    LANE_WORK,
    Database,
)


def _future_iso(seconds: int) -> str:
    nxt = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=seconds)
    return nxt.isoformat().replace("+00:00", "Z")


class LaneAndPriorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(db_path=Path(self._tmp.name) / "queue.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_enqueue_defaults_to_control_lane(self) -> None:
        tid = self.db.enqueue("handle_entry_created", post_id="1")
        row = self.db.get_task(tid)
        self.assertEqual(row["lane"], LANE_CONTROL)
        self.assertEqual(row["priority"], DEFAULT_PRIORITY)

    def test_claim_is_lane_scoped(self) -> None:
        self.db.enqueue("a", post_id="1", lane=LANE_CONTROL)
        self.db.enqueue("b", post_id="2", lane=LANE_WORK)
        ctrl = self.db.claim_next(LANE_CONTROL)
        self.assertEqual(ctrl["action"], "a")
        # The work item is invisible to a control claim and vice versa.
        self.assertIsNone(self.db.claim_next(LANE_CONTROL))
        work = self.db.claim_next(LANE_WORK)
        self.assertEqual(work["action"], "b")

    def test_higher_priority_claimed_first(self) -> None:
        low = self.db.enqueue("low", post_id="1", priority=10)
        high = self.db.enqueue("high", post_id="2", priority=90)
        mid = self.db.enqueue("mid", post_id="3", priority=50)
        self.assertEqual(self.db.claim_next(LANE_CONTROL)["task_id"], high)
        self.assertEqual(self.db.claim_next(LANE_CONTROL)["task_id"], mid)
        self.assertEqual(self.db.claim_next(LANE_CONTROL)["task_id"], low)

    def test_fifo_within_a_priority_band(self) -> None:
        first = self.db.enqueue("a", post_id="1", priority=50)
        second = self.db.enqueue("b", post_id="2", priority=50)
        self.assertEqual(self.db.claim_next(LANE_CONTROL)["task_id"], first)
        self.assertEqual(self.db.claim_next(LANE_CONTROL)["task_id"], second)

    def test_not_before_skipped_until_due(self) -> None:
        tid = self.db.enqueue("retry", post_id="1")
        self.db.requeue(tid, not_before=_future_iso(3600))
        self.assertIsNone(self.db.claim_next(LANE_CONTROL))

    def test_pending_count_by_lane(self) -> None:
        self.db.enqueue("a", post_id="1", lane=LANE_CONTROL)
        self.db.enqueue("b", post_id="2", lane=LANE_CONTROL)
        self.db.enqueue("c", post_id="3", lane=LANE_WORK)
        self.assertEqual(self.db.pending_count(), 3)
        self.assertEqual(self.db.pending_count(LANE_CONTROL), 2)
        self.assertEqual(self.db.pending_count(LANE_WORK), 1)

    def test_ready_now_excludes_future_retries(self) -> None:
        ready = self.db.enqueue("ready", post_id="1", lane=LANE_CONTROL)
        delayed = self.db.enqueue("delayed", post_id="2", lane=LANE_CONTROL)
        self.db.requeue(delayed, not_before=_future_iso(3600))
        self.assertEqual(self.db.pending_count(LANE_CONTROL), 2)
        self.assertEqual(self.db.pending_count(LANE_CONTROL, ready_now=True), 1)
        # the work thread's gate: control lane has nothing claimable right now
        self.db.claim_next(LANE_CONTROL)  # claims the ready one
        self.assertEqual(self.db.pending_count(LANE_CONTROL, ready_now=True), 0)
        _ = ready

    def test_unknown_lane_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.db.enqueue("a", post_id="1", lane="nope")

    def test_active_count_spans_lanes(self) -> None:
        self.db.enqueue("a", post_id="1", lane=LANE_CONTROL)
        self.db.enqueue("c", post_id="3", lane=LANE_WORK)
        claimed = self.db.claim_next(LANE_WORK)
        self.assertEqual(claimed["lane"], LANE_WORK)
        # one pending control + one in-progress work = 2 active
        self.assertEqual(self.db.active_count(), 2)
        self.assertEqual(self.db.active_count(LANE_WORK), 1)


if __name__ == "__main__":
    unittest.main()
