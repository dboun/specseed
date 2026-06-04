"""test_advance.py - in-code state transitions + code-review loop.

FakeAgentRunner only; TrackingRemoteLocal/TrackingLocal mirrors. No GitHub/GitLab.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from src.target_facing.specseed_target_src.db.database import Database
from src.target_facing.specseed_target_src.executing.agent_runner import (
    AgentResult,
    FakeAgentRunner,
)
from src.target_facing.specseed_target_src.executing import advance
from src.target_facing.specseed_target_src.executing.advance import (
    REVIEW_MARKER,
    parse_review,
)
from src.target_facing.specseed_target_src.executing.context import ExecutionContext
from src.target_facing.specseed_target_src.executing.dispatch import dispatch
from src.target_facing.specseed_target_src.executing.permissions import Permissions
from src.target_facing.specseed_target_src.entities.entity_base import Entity
from src.target_facing.specseed_target_src.entities import issue as _issue  # noqa: F401
from src.target_facing.specseed_target_src.tracking.tracking_local import TrackingLocal
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


class EntityParsingTest(unittest.TestCase):
    def test_canonical_tier_status_form(self) -> None:
        e = Entity.for_labels(post_id="1", labels=["issue", "issue:status:in_review"])
        self.assertEqual(e.tier, "issue")
        self.assertEqual(e.status, "in_review")

    def test_prefixed_form_still_works(self) -> None:
        e = Entity.for_labels(post_id="1", labels=["tier:issue", "status:todo"])
        self.assertEqual(e.tier, "issue")
        self.assertEqual(e.status, "todo")

    def test_bare_tier_without_status(self) -> None:
        e = Entity.for_labels(post_id="1", labels=["epic"])
        self.assertEqual(e.tier, "epic")
        self.assertIsNone(e.status)


class ParseReviewTest(unittest.TestCase):
    def test_approve_high_confidence(self) -> None:
        v, c = parse_review("looks good\nSPECSEED_REVIEW verdict=approve confidence=0.9")
        self.assertEqual(v, "approve")
        self.assertAlmostEqual(c, 0.9)

    def test_changes(self) -> None:
        v, c = parse_review("nope\nSPECSEED_REVIEW verdict=changes confidence=0.3")
        self.assertEqual(v, "changes")
        self.assertAlmostEqual(c, 0.3)

    def test_missing_line_defaults_to_changes(self) -> None:
        v, c = parse_review("just some prose, no verdict line")
        self.assertEqual(v, "changes")
        self.assertEqual(c, 0.0)

    def test_clamps_confidence(self) -> None:
        _, c = parse_review("SPECSEED_REVIEW verdict=approve confidence=2.5")
        self.assertEqual(c, 1.0)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="alice")
        self.local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        self.db = Database(db_path=self.root / "queue.db")

    def _config(self, review=None):
        cfg = {
            "specseed_dir": "seedmeta",
            "backend": {"enabled": False, "provider": None},
            "approvals": {"approver_usernames": ["alice"]},
            "permissions": {"remote": {"post_issues": True}},
        }
        if review is not None:
            cfg["review"] = review
        return cfg

    def _ctx(self, config, runner):
        return ExecutionContext(
            db=self.db,
            local=self.local,
            remote=self.remote,
            config=config,
            permissions=Permissions(config),
            runner=runner,
            repo_root=self.root,
            storage=self.root / "storage",
            cancel=threading.Event(),
            agent_timeout_s=30.0,
        )

    def _seed(self, title, labels, comments=()):
        for label in labels:
            self.local.create_label(label)
        eid = self.local.add_entry(title, labels=labels).data.id
        for body in comments:
            self.local.add_entry_comment(eid, body)
        # mirror the same entry into the remote so transitions can mutate it
        for label in labels:
            self.remote.create_label(label)
        self.remote.add_entry(title, labels=labels)
        return eid

    def _remote_labels(self, eid):
        details = self.remote.get_entry(eid).data
        return {lbl.name for lbl in details.labels}

    def _remote_details(self, eid):
        return self.remote.get_entry(eid).data


class ImplementTransitionTest(_Base):
    def test_implement_no_review_closes_done(self) -> None:
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True, returncode=0)))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:done", labels)
        self.assertNotIn("issue:status:todo", labels)
        self.assertFalse(self._remote_details(eid).is_open)

    def test_implement_with_review_goes_in_review(self) -> None:
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        ctx = self._ctx(self._config(review={"enabled": True}),
                        FakeAgentRunner(AgentResult(ok=True, returncode=0)))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:in_review", labels)
        self.assertTrue(self._remote_details(eid).is_open)


class ReviewTransitionTest(_Base):
    def _review_runner(self, verdict, confidence):
        stdout = "Detailed review.\nSPECSEED_REVIEW verdict={0} confidence={1}".format(verdict, confidence)
        return FakeAgentRunner(AgentResult(ok=True, returncode=0, stdout=stdout))

    def test_review_pass_closes_done(self) -> None:
        eid = self._seed("Review me", ["issue", "issue:status:in_review"])
        ctx = self._ctx(self._config(review={"enabled": True, "confidence_threshold": 0.75}),
                        self._review_runner("approve", 0.9))
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(eid))
        self.assertFalse(self._remote_details(eid).is_open)
        bodies = [c.body for c in self._remote_details(eid).comments]
        self.assertTrue(any(REVIEW_MARKER in b for b in bodies))

    def test_review_pass_low_confidence_loops_back(self) -> None:
        eid = self._seed("Review me", ["issue", "issue:status:in_review"])
        ctx = self._ctx(self._config(review={"enabled": True, "confidence_threshold": 0.75, "max_attempts": 3}),
                        self._review_runner("approve", 0.4))
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_review_changes_requires_human_approval(self) -> None:
        eid = self._seed("Review me", ["issue", "issue:status:in_review"])
        ctx = self._ctx(
            self._config(review={"enabled": True, "require_human_approval": True}),
            self._review_runner("approve", 0.95),
        )
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))
        self.assertTrue(self._remote_details(eid).is_open)

    def test_review_exhausts_attempts_escalates(self) -> None:
        # two prior review attempts already recorded; max_attempts=3 -> this is #3
        prior = ["review one\n" + REVIEW_MARKER, "review two\n" + REVIEW_MARKER]
        eid = self._seed("Review me", ["issue", "issue:status:in_review"], comments=prior)
        ctx = self._ctx(self._config(review={"enabled": True, "max_attempts": 3}),
                        self._review_runner("changes", 0.2))
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:blocked", self._remote_labels(eid))
        # a draft spec-change:adapt post was created on the remote
        drafts = [
            d for d in self._all_remote_entries()
            if "spec-change:adapt" in {l.name for l in d.labels} and "draft" in {l.name for l in d.labels}
        ]
        self.assertEqual(len(drafts), 1)

    def _all_remote_entries(self):
        listing = self.remote.list_entries()
        out = []
        for summary in listing.data:
            out.append(self.remote.get_entry(summary.id).data)
        return out


class _SR:
    """Minimal state-result stub for resolve_approval."""

    def __init__(self, approved_by):
        self.approved_by = approved_by


class ApprovalGateTest(_Base):
    def test_completion_gate_closes_when_approved(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["review\n" + REVIEW_MARKER])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        detail = advance.resolve_approval(ctx, entity, _SR(["alice"]), conversation)
        self.assertIn("done", detail)
        self.assertIn("issue:status:done", self._remote_labels(eid))
        self.assertFalse(self._remote_details(eid).is_open)

    def test_prework_gate_resumes_when_approved(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        detail = advance.resolve_approval(ctx, entity, _SR(["alice"]), conversation)
        self.assertIn("todo", detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_not_approved_stays_parked(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        self.assertIsNone(advance.resolve_approval(ctx, entity, _SR([]), conversation))
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))


def _load(ctx, eid):
    from src.target_facing.specseed_target_src.executing.context import load_entity
    return load_entity(ctx, str(eid))


if __name__ == "__main__":
    unittest.main()
