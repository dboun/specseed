"""test_approvals.py - the APR-NNNN approval system.

Covers the pure token vocabulary (state_machines/approvals), the runtime id
allocator (executing/approvals), the state-machine approve/reject resolution
(default approver, configured approver, comment + 👍/👎 reaction signals), and the
full dispatch loop where a 👍 entry reaction clears an awaiting_approval gate.

No GitHub/GitLab: TrackingRemoteLocal/TrackingLocal mirrors only.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from src.target_facing.specseed_target_src.db.database import Database
from src.target_facing.specseed_target_src.executing import approvals as rt_approvals
from src.target_facing.specseed_target_src.executing.agent_runner import (
    AgentResult,
    FakeAgentRunner,
)
from src.target_facing.specseed_target_src.executing.context import ExecutionContext, load_entity
from src.target_facing.specseed_target_src.executing.dispatch import dispatch
from src.target_facing.specseed_target_src.executing.permissions import Permissions
from src.target_facing.specseed_target_src.entities.entity_base import Entity
from src.target_facing.specseed_target_src.entities import issue as _issue  # noqa: F401
from src.target_facing.specseed_target_src.state_machines import approvals
from src.target_facing.specseed_target_src.state_machines.base import (
    approved_by,
    evaluate_entity_state,
    rejected_by,
)
from src.target_facing.specseed_target_src.tracking.comment import TrackingReaction
from src.target_facing.specseed_target_src.tracking.tracking_local import TrackingLocal
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


class TokenVocabularyTest(unittest.TestCase):
    def test_format_and_number_round_trip(self) -> None:
        self.assertEqual(approvals.format_apr(1), "APR-0001")
        self.assertEqual(approvals.format_apr(42), "APR-0042")
        self.assertEqual(approvals.format_apr(12345), "APR-12345")
        self.assertEqual(approvals.apr_number("APR-0007"), 7)
        self.assertIsNone(approvals.apr_number("not-a-token"))

    def test_ids_in_text_are_canonicalised_and_deduped(self) -> None:
        text = "please approve apr-0007 and APR-0007 plus APR-0010"
        self.assertEqual(approvals.apr_ids_in_text(text), ["APR-0007", "APR-0010"])

    def test_requested_ids_only_from_markers(self) -> None:
        body = approvals.approval_request_comment("APR-0003", "Breakdown ready.")
        convo = [
            {"body": "I mention APR-0099 but it is not a request"},
            {"body": body},
        ]
        self.assertEqual(approvals.requested_apr_ids(convo), ["APR-0003"])

    def test_request_comment_carries_marker_and_instructions(self) -> None:
        body = approvals.approval_request_comment("APR-0005", "Do the thing.")
        self.assertIn("APR-0005", body)
        self.assertIn("approve APR-0005", body)
        self.assertIn("specseed:approval-request APR-0005", body)


class AllocatorTest(unittest.TestCase):
    def test_next_apr_id_is_monotonic_and_persistent(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        storage = Path(tmp.name)
        self.assertEqual(rt_approvals.next_apr_id(storage), "APR-0001")
        self.assertEqual(rt_approvals.next_apr_id(storage), "APR-0002")
        self.assertEqual(rt_approvals.next_apr_id(storage), "APR-0003")
        self.assertTrue(rt_approvals.counter_path(storage).exists())


def _entity(reactions=None):
    e = Entity.for_labels(post_id="5", labels=["issue", "issue:status:awaiting_approval"])
    e.reactions = list(reactions or [])
    return e


class ApprovedByTest(unittest.TestCase):
    def test_configured_approver_via_comment_on_post_id(self) -> None:
        cfg = {"approvals": {"approver_usernames": ["alice"]}}
        convo = [{"author": "alice", "body": "approve 5"}]
        self.assertEqual(approved_by(_entity(), cfg, convo), ["alice"])

    def test_non_approver_comment_ignored_when_list_configured(self) -> None:
        cfg = {"approvals": {"approver_usernames": ["alice"]}}
        convo = [{"author": "mallory", "body": "approve 5"}]
        self.assertEqual(approved_by(_entity(), cfg, convo), [])

    def test_default_any_human_when_no_list(self) -> None:
        cfg = {}  # no approver list -> any non-bot human may approve
        convo = [{"author": "carol", "body": "approve 5"}]
        self.assertEqual(approved_by(_entity(), cfg, convo), ["carol"])

    def test_bot_author_never_approves_under_default(self) -> None:
        cfg = {}
        convo = [{"author": "remote", "body": "approve 5"}]  # 'remote' is a bot stand-in
        self.assertEqual(approved_by(_entity(), cfg, convo), [])

    def test_allow_any_can_be_disabled(self) -> None:
        cfg = {"approvals": {"allow_any_approver": False}}
        convo = [{"author": "carol", "body": "approve 5"}]
        self.assertEqual(approved_by(_entity(), cfg, convo), [])

    def test_apr_token_comment_matches_requested_marker(self) -> None:
        cfg = {}
        request = approvals.approval_request_comment("APR-0001", "ready")
        convo = [
            {"author": "remote", "body": request},
            {"author": "dave", "body": "approve APR-0001"},
        ]
        self.assertEqual(approved_by(_entity(), cfg, convo), ["dave"])

    def test_thumbs_up_entry_reaction_approves(self) -> None:
        cfg = {}
        entity = _entity([TrackingReaction("thumbs_up", 1, ["erin"])])
        self.assertEqual(approved_by(entity, cfg, []), ["erin"])

    def test_thumbs_up_by_bot_does_not_approve(self) -> None:
        cfg = {}
        entity = _entity([TrackingReaction("thumbs_up", 1, ["remote"])])
        self.assertEqual(approved_by(entity, cfg, []), [])


class RejectedByTest(unittest.TestCase):
    def test_reject_comment_and_thumbs_down(self) -> None:
        cfg = {}
        request = approvals.approval_request_comment("APR-0002", "ready")
        convo = [
            {"author": "remote", "body": request},
            {"author": "frank", "body": "reject APR-0002"},
        ]
        self.assertEqual(rejected_by(_entity(), cfg, convo), ["frank"])
        entity = _entity([TrackingReaction("thumbs_down", 1, ["grace"])])
        self.assertEqual(rejected_by(entity, cfg, []), ["grace"])

    def test_evaluate_surfaces_both_lists(self) -> None:
        cfg = {}
        entity = _entity([TrackingReaction("thumbs_up", 1, ["henry"])])
        result = evaluate_entity_state(entity, cfg, [])
        self.assertEqual(result.approved_by, ["henry"])
        self.assertEqual(result.rejected_by, [])


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="alice")
        self.local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        self.db = Database(db_path=self.root / "queue.db")

    def _config(self):
        return {
            "specseed_dir": "seedmeta",
            "approvals": {},  # no list -> default any-human approver
            "permissions": {},
        }

    def _ctx(self, config):
        return ExecutionContext(
            db=self.db,
            local=self.local,
            remote=self.remote,
            config=config,
            permissions=Permissions(config),
            runner=FakeAgentRunner(AgentResult(ok=True)),
            repo_root=self.root,
            storage=self.root / "storage",
            cancel=threading.Event(),
            agent_timeout_s=30.0,
        )

    def _seed_awaiting(self, title="Gate"):
        labels = ["issue", "issue:status:awaiting_approval"]
        for label in labels:
            self.local.create_label(label)
            self.remote.create_label(label)
        eid = self.local.add_entry(title, labels=labels).data.id
        self.remote.add_entry(title, labels=labels)
        return eid

    def _remote_labels(self, eid):
        return {lbl.name for lbl in self.remote.get_entry(eid).data.labels}


class DispatchReactionApprovalTest(_Base):
    def test_thumbs_up_entry_reaction_resolves_prework_gate(self) -> None:
        eid = self._seed_awaiting()
        # a human (not the agent/bot) reacts 👍 on the local mirror
        human = TrackingLocal(db_path=self.root / "local.db", author="human")
        human.add_entry_reaction(eid, "thumbs_up")

        ctx = self._ctx(self._config())
        out = dispatch(
            ctx,
            {"action": "handle_entry_reaction_added", "post_id": str(eid), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertIn("issue:status:todo", self._remote_labels(eid))
        self.assertNotIn("issue:status:awaiting_approval", self._remote_labels(eid))

    def test_thumbs_down_entry_reaction_blocks_gate(self) -> None:
        eid = self._seed_awaiting()
        human = TrackingLocal(db_path=self.root / "local.db", author="human")
        human.add_entry_reaction(eid, "thumbs_down")

        ctx = self._ctx(self._config())
        out = dispatch(
            ctx,
            {"action": "handle_entry_reaction_added", "post_id": str(eid), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertIn("issue:status:blocked", self._remote_labels(eid))

    def test_no_reaction_stays_parked(self) -> None:
        eid = self._seed_awaiting()
        ctx = self._ctx(self._config())
        out = dispatch(
            ctx,
            {"action": "handle_entry_reaction_added", "post_id": str(eid), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))


if __name__ == "__main__":
    unittest.main()
