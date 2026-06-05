"""test_lifecycle_integration.py - full issue lifecycle through the Scheduler.

Drives sync(remote->local) -> dispatch -> advance -> next sync across run_once
passes with a scripted FakeAgentRunner. Guards the wiring (self-triggering label
swaps) and the duplicate-side-effect regression (a status swap is a label
remove+add, so one entity can surface as several queued events in one drain;
transitions must act once, not once per event). No GitHub/GitLab.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from specseed_runtime.db.database import Database
from specseed_runtime.executing.agent_runner import AgentResult, FakeAgentRunner
from specseed_runtime.executing.scheduler import Scheduler
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal
from specseed_runtime.entities import issue as _issue  # noqa: F401
from specseed_runtime.entities import epic as _epic  # noqa: F401
from specseed_runtime.entities import ticket as _ticket  # noqa: F401


class LifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _build(self, runner, review):
        remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="alice")
        local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        db = Database(db_path=self.root / "queue.db")
        cfg = {
            "specseed_dir": "seedmeta",
            "approvals": {"approver_usernames": ["alice"]},
            "permissions": {},
            "review": review,
        }
        sched = Scheduler(db=db, runner=runner, config=cfg, storage=self.root,
                          repo_root=self.root, remote=remote, local=local, poll_interval=0)
        for l in ("issue", "issue:status:todo"):
            remote.create_label(l)
        eid = remote.add_entry("Add greeting", labels=["issue", "issue:status:todo"]).data.id
        return sched, remote, eid

    @staticmethod
    def _runner(review_script):
        state = {"i": 0}

        def side_effect(call):
            if "reviewing completed work" in call["prompt"]:
                v, c = review_script[min(state["i"], len(review_script) - 1)]
                state["i"] += 1
                return AgentResult(ok=True, returncode=0,
                                   stdout=f"review\nSPECSEED_REVIEW verdict={v} confidence={c}")
            return AgentResult(ok=True, returncode=0)

        return FakeAgentRunner(side_effect=side_effect)

    @staticmethod
    def _status(remote, eid):
        d = remote.get_entry(eid).data
        st = next((l.name.split(":status:")[1] for l in d.labels if ":status:" in l.name), None)
        return st, d.is_open

    @staticmethod
    def _drafts(remote):
        out = []
        for s in remote.list_entries().data:
            names = {l.name for l in remote.get_entry(s.id).data.labels}
            if "spec-change:adapt" in names and "draft" in names:
                out.append(s.id)
        return out

    def test_happy_path_changes_then_approve_reaches_done(self) -> None:
        sched, remote, eid = self._build(
            self._runner([("changes", 0.2), ("approve", 0.95)]),
            {"enabled": True, "confidence_threshold": 0.75, "max_attempts": 3},
        )
        for _ in range(6):
            sched.run_once()
        st, is_open = self._status(remote, eid)
        self.assertEqual(st, "done")
        self.assertFalse(is_open)
        self.assertEqual(self._drafts(remote), [])

    def test_persistent_failure_escalates_to_single_draft(self) -> None:
        sched, remote, eid = self._build(
            self._runner([("changes", 0.1)]),
            {"enabled": True, "confidence_threshold": 0.75, "max_attempts": 2},
        )
        for _ in range(8):
            sched.run_once()
        st, _ = self._status(remote, eid)
        self.assertEqual(st, "blocked")
        # exactly one draft adapt post, despite many duplicate queued events
        self.assertEqual(len(self._drafts(remote)), 1)


if __name__ == "__main__":
    unittest.main()
