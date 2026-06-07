"""test_advance.py - in-code state transitions + code-review loop.

FakeAgentRunner only; TrackingRemoteLocal/TrackingLocal mirrors. No GitHub/GitLab.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from specseed_runtime.scheduling.spec_change import spec_change_dir

from specseed_runtime.db.database import Database
from specseed_runtime.executing.agent_runner import (
    AgentResult,
    FakeAgentRunner,
)
from specseed_runtime.executing import advance
from specseed_runtime.executing.advance import (
    REVIEW_MARKER,
    parse_review,
)
from specseed_runtime.executing.context import ExecutionContext
from specseed_runtime.executing.dispatch import dispatch
from specseed_runtime.executing.permissions import Permissions
from specseed_runtime.entities.entity_base import Entity
from specseed_runtime.entities import issue as _issue  # noqa: F401
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


def _impl_ok(status="done", summary="implemented", files=None, recommend=False):
    """An implement AgentResult carrying a valid structured report."""
    return AgentResult(
        ok=True, returncode=0,
        report={
            "status": status, "summary": summary, "files_changed": files or [],
            "recommend_spec_change": recommend,
        },
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
            "approvals": {"approver_usernames": ["alice"]},
            "permissions": {},
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
        ctx = self._ctx(self._config(), FakeAgentRunner(_impl_ok()))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:done", labels)
        self.assertNotIn("issue:status:todo", labels)
        self.assertFalse(self._remote_details(eid).is_open)

    def test_implement_with_review_goes_in_review(self) -> None:
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        ctx = self._ctx(self._config(review={"enabled": True}),
                        FakeAgentRunner(_impl_ok()))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:in_review", labels)
        self.assertTrue(self._remote_details(eid).is_open)

    def test_implement_reported_blocked_parks_blocked(self) -> None:
        # Agent says it could not make the change (rc 0): must NOT advance to review.
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        ctx = self._ctx(
            self._config(review={"enabled": True}),
            FakeAgentRunner(_impl_ok(status="blocked", summary="sandbox blocked the write")),
        )
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:blocked", labels)
        self.assertNotIn("issue:status:in_review", labels)
        bodies = [c.body for c in self._remote_details(eid).comments]
        self.assertTrue(any("sandbox blocked the write" in b for b in bodies))

    def test_implement_recommends_spec_change_blocks_and_drafts(self) -> None:
        # Implementer flags the spec up front: block, draft adapt, skip review.
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        ctx = self._ctx(
            self._config(review={"enabled": True}),
            FakeAgentRunner(_impl_ok(status="blocked", summary="spec contradicts itself",
                                     recommend=True)),
        )
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:blocked", labels)
        self.assertNotIn("issue:status:in_review", labels)
        drafts = [
            d for d in (self.remote.get_entry(s.id).data for s in self.remote.list_entries().data)
            if "spec-change:adapt" in {l.name for l in d.labels} and "draft" in {l.name for l in d.labels}
        ]
        self.assertEqual(len(drafts), 1)


class ReviewTransitionTest(_Base):
    def _review_runner(self, verdict, confidence, recommend=False):
        stdout = "Detailed review.\nSPECSEED_REVIEW verdict={0} confidence={1}".format(verdict, confidence)
        return FakeAgentRunner(AgentResult(
            ok=True, returncode=0, stdout=stdout,
            report={
                "verdict": verdict, "confidence": confidence, "summary": "Detailed review.",
                "recommend_spec_change": recommend,
            },
        ))

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

    def test_review_approve_low_confidence_awaits_human(self) -> None:
        # approve but under the bar -> human sign-off, NOT a reimplement loop.
        eid = self._seed("Review me", ["issue", "issue:status:in_review"])
        ctx = self._ctx(self._config(review={"enabled": True, "confidence_threshold": 0.75, "max_attempts": 3}),
                        self._review_runner("approve", 0.4))
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:awaiting_approval", labels)
        self.assertNotIn("issue:status:todo", labels)
        self.assertTrue(self._remote_details(eid).is_open)

    def test_review_pass_ignores_legacy_human_gate_key(self) -> None:
        # require_human_approval is gone; a stale key in config changes nothing.
        eid = self._seed("Review me", ["issue", "issue:status:in_review"])
        ctx = self._ctx(
            self._config(review={"enabled": True, "require_human_approval": True}),
            self._review_runner("approve", 0.95),
        )
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(eid))
        self.assertFalse(self._remote_details(eid).is_open)

    def _drafts(self):
        return [
            d for d in self._all_remote_entries()
            if "spec-change:adapt" in {l.name for l in d.labels} and "draft" in {l.name for l in d.labels}
        ]

    def test_review_exhausts_with_recommend_drafts_adapt(self) -> None:
        # two prior review attempts already recorded; max_attempts=3 -> this is #3.
        # reviewer recommends a spec change -> draft adapt + blocked.
        prior = ["review one\n" + REVIEW_MARKER, "review two\n" + REVIEW_MARKER]
        eid = self._seed("Review me", ["issue", "issue:status:in_review"], comments=prior)
        ctx = self._ctx(self._config(review={"enabled": True, "max_attempts": 3}),
                        self._review_runner("changes", 0.2, recommend=True))
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:blocked", self._remote_labels(eid))
        self.assertEqual(len(self._drafts()), 1)

    def test_review_exhausts_without_recommend_blocks_no_adapt(self) -> None:
        # Same exhaustion, but reviewer did NOT recommend a spec change -> blocked
        # for a human decision, and NO draft adapt post is opened.
        prior = ["review one\n" + REVIEW_MARKER, "review two\n" + REVIEW_MARKER]
        eid = self._seed("Review me", ["issue", "issue:status:in_review"], comments=prior)
        ctx = self._ctx(self._config(review={"enabled": True, "max_attempts": 3}),
                        self._review_runner("changes", 0.2, recommend=False))
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:blocked", self._remote_labels(eid))
        self.assertEqual(len(self._drafts()), 0)
        bodies = [c.body for c in self._remote_details(eid).comments]
        self.assertTrue(any("human" in (b or "").lower() for b in bodies))

    def test_review_max_attempts_defaults_to_two(self) -> None:
        # No max_attempts in config -> default 2. One prior attempt recorded, so
        # this failing review is #2 == the limit -> exhausted (blocked), not a loop.
        prior = ["review one\n" + REVIEW_MARKER]
        eid = self._seed("Review me", ["issue", "issue:status:in_review"], comments=prior)
        ctx = self._ctx(self._config(review={"enabled": True}),
                        self._review_runner("changes", 0.2, recommend=True))
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:blocked", self._remote_labels(eid))
        self.assertEqual(len(self._drafts()), 1)

    def _all_remote_entries(self):
        listing = self.remote.list_entries()
        out = []
        for summary in listing.data:
            out.append(self.remote.get_entry(summary.id).data)
        return out


class ImplementApprovalGateTest(_Base):
    """platform.auto_implement_issue=False parks a ready issue for sign-off."""

    def _auto_off(self, approvers=None):
        cfg = self._config()
        cfg["permissions"]["platform"] = {"auto_implement_issue": False}
        if approvers is not None:
            cfg["approvals"]["approver_usernames"] = list(approvers)
        return cfg

    def test_todo_parks_awaiting_approval_when_auto_off(self) -> None:
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        ctx = self._ctx(self._auto_off(), FakeAgentRunner(_impl_ok()))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:awaiting_approval", labels)
        self.assertNotIn("issue:status:todo", labels)
        bodies = [c.body for c in self._remote_details(eid).comments]
        self.assertTrue(any("approve" in b for b in bodies))

    def test_todo_proceeds_when_already_approved(self) -> None:
        # local comments are authored by "agent" (the tracker author) — make it an approver.
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        self.local.add_entry_comment(eid, "approve {0}".format(eid))
        ctx = self._ctx(self._auto_off(approvers=["agent"]),
                        FakeAgentRunner(_impl_ok()))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(eid))

    def test_auto_on_implements_without_parking(self) -> None:
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        cfg = self._config()
        cfg["permissions"]["platform"] = {"auto_implement_issue": True}
        ctx = self._ctx(cfg, FakeAgentRunner(_impl_ok()))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(eid))


class _SR:
    """Minimal state-result stub for resolve_approval / resolve_blocked."""

    def __init__(self, approved_by=(), rejected_by=()):
        self.approved_by = list(approved_by)
        self.rejected_by = list(rejected_by)


_COMMENT = "handle_comment_added"
_REACT = "handle_reaction_added"


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

    def test_prose_comment_redirects_to_todo_with_guidance(self) -> None:
        # A free-text comment is taken as change guidance: resume the implementer.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["please rename the function to run()"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        detail = advance.resolve_approval(ctx, entity, _SR([]), conversation, _COMMENT)
        self.assertIn("guidance", detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_retry_comment_redirects_to_todo_blind(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["retry"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        detail = advance.resolve_approval(ctx, entity, _SR([]), conversation, _COMMENT)
        self.assertIn("retry", detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_bare_reject_reaction_posts_options_once(self) -> None:
        # 👎 with no guidance (a reaction event, not a comment): post the options
        # prompt and wait; the issue stays parked. A second pass posts nothing more.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        detail = advance.resolve_approval(ctx, entity, _SR(rejected_by=["alice"]), conversation, _REACT)
        self.assertIn("options", detail)
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))
        self.assertIn(advance.OPTIONS_MARKER,
                      "\n".join(c.body for c in self._remote_details(eid).comments))
        # second pass: options already posted -> nothing applied.
        entity, conversation = _load(ctx, eid)
        again = advance.resolve_approval(ctx, entity, _SR(rejected_by=["alice"]), conversation, _REACT)
        self.assertIsNone(again)
        options = [c for c in self._remote_details(eid).comments if advance.OPTIONS_MARKER in (c.body or "")]
        self.assertEqual(len(options), 1)

    def test_bare_reject_command_posts_options(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["reject"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        detail = advance.resolve_approval(ctx, entity, _SR(rejected_by=["alice"]), conversation, _COMMENT)
        self.assertIn("options", detail)
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))

    def test_reject_with_prose_is_guidance(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["reject use a dataclass instead"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        detail = advance.resolve_approval(ctx, entity, _SR(rejected_by=["alice"]), conversation, _COMMENT)
        self.assertIn("guidance", detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_directive_ignored_on_non_comment_event(self) -> None:
        # A stale prose comment must NOT redirect when the waking event is a label
        # change (no fresh human directive).
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["do something"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        self.assertIsNone(
            advance.resolve_approval(ctx, entity, _SR([]), conversation, "handle_label_added")
        )
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))


class BlockedBypassTest(_Base):
    def test_approve_forces_done(self) -> None:
        eid = self._seed("Stuck", ["issue", "issue:status:blocked"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        detail = advance.resolve_blocked(ctx, entity, _SR(["alice"]), conversation, _REACT)
        self.assertIn("done", detail)
        self.assertIn("issue:status:done", self._remote_labels(eid))
        self.assertFalse(self._remote_details(eid).is_open)

    def test_guidance_comment_reopens_to_todo(self) -> None:
        eid = self._seed("Stuck", ["issue", "issue:status:blocked"],
                         comments=["try the other API"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        detail = advance.resolve_blocked(ctx, entity, _SR([]), conversation, _COMMENT)
        self.assertIn("todo", detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_nothing_actionable_stays_blocked(self) -> None:
        eid = self._seed("Stuck", ["issue", "issue:status:blocked"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        self.assertIsNone(advance.resolve_blocked(ctx, entity, _SR([]), conversation, _REACT))
        self.assertIn("issue:status:blocked", self._remote_labels(eid))


def _load(ctx, eid):
    from specseed_runtime.executing.context import load_entity
    return load_entity(ctx, str(eid))


class RelationshipsParseTest(unittest.TestCase):
    def test_parent_links(self) -> None:
        from specseed_runtime.executing import relationships as r
        self.assertEqual(r.parent_id("## Links\nTicket: #41\nDepends on: #9", "ticket"), "41")
        self.assertEqual(r.parent_id("Epic: #12  Issues: #1", "epic"), "12")
        self.assertIsNone(r.parent_id("no links here", "epic"))

    def test_child_links_inline(self) -> None:
        from specseed_runtime.executing import relationships as r
        self.assertEqual(r.child_ids("Issues: #8, #9, #10", "issues"), ["8", "9", "10"])

    def test_child_links_section(self) -> None:
        from specseed_runtime.executing import relationships as r
        body = "# Epic\n\n## Tickets\n#7\n#8\n\n## Goal\nstuff #99"
        self.assertEqual(r.child_ids(body, "tickets"), ["7", "8"])

    def test_depends_on_not_mistaken_for_children(self) -> None:
        from specseed_runtime.executing import relationships as r
        self.assertEqual(r.child_ids("Ticket: #2\nDepends on: #5", "issues"), [])


class RollUpTest(_Base):
    """An epic -> ticket -> two issues tree; finishing the last issue closes up."""

    def _tree(self, *, issue_b_status="issue:status:todo"):
        # Create on both trackers in the same order so ids line up (1..4).
        specs = [
            ("Epic", ["epic", "epic:status:todo"], "# Epic\n\n## Tickets\n#2\n"),
            ("Ticket", ["ticket", "ticket:status:todo"], "Epic: #1\nIssues: #3, #4\n"),
            ("Issue A", ["issue", "issue:status:done"], "Ticket: #2\n"),
            ("Issue B", ["issue", issue_b_status], "Ticket: #2\n"),
        ]
        for tracker in (self.local, self.remote):
            for title, labels, body in specs:
                for label in labels:
                    tracker.create_label(label)
                tracker.add_entry(title, body=body, labels=labels)
        # Issue A is already finished on the remote (source of truth for roll-up).
        self.remote.set_entry_closed(3)

    def test_finishing_last_issue_closes_ticket_and_epic(self) -> None:
        self._tree()
        ctx = self._ctx(self._config(), FakeAgentRunner(_impl_ok()))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": "4", "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(4))
        # ticket #2 and epic #1 rolled up to done + closed
        self.assertIn("ticket:status:done", self._remote_labels(2))
        self.assertFalse(self._remote_details(2).is_open)
        self.assertIn("epic:status:done", self._remote_labels(1))
        self.assertFalse(self._remote_details(1).is_open)
        self.assertIn("rolled up", out.detail)

    def test_open_sibling_keeps_parents_open(self) -> None:
        # Issue A is NOT finished -> finishing B must not close the ticket/epic.
        self._tree()
        self.remote.set_entry_open(3)
        self.remote.remove_entry_label(3, "issue:status:done")
        self.remote.add_entry_label(3, "issue:status:in_progress")
        ctx = self._ctx(self._config(), FakeAgentRunner(_impl_ok()))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": "4", "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(4))
        self.assertIn("ticket:status:todo", self._remote_labels(2))
        self.assertTrue(self._remote_details(2).is_open)
        self.assertIn("epic:status:todo", self._remote_labels(1))


class _SR2:
    """State-result stub carrying both approval verdicts (for spec-change requests)."""

    def __init__(self, approved_by=(), rejected_by=()):
        self.approved_by = list(approved_by)
        self.rejected_by = list(rejected_by)


class SettleDocTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_prepends_frontmatter_when_absent(self) -> None:
        doc = self.dir / "srs.md"
        doc.write_text("# SRS\nbody\n", encoding="utf-8")
        self.assertTrue(advance._settle_doc(doc, "2026-06-05"))
        text = doc.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nsettled: true\nsettled_at: 2026-06-05\n---\n"))
        self.assertIn("# SRS", text)

    def test_updates_existing_frontmatter_without_duplicating(self) -> None:
        doc = self.dir / "srs.md"
        doc.write_text("---\ncomponent: api\nsettled: false\n---\n\n# SRS\n", encoding="utf-8")
        advance._settle_doc(doc, "2026-06-05")
        text = doc.read_text(encoding="utf-8")
        self.assertEqual(text.count("settled:"), 1)
        self.assertIn("settled: true", text)
        self.assertIn("settled_at: 2026-06-05", text)
        self.assertIn("component: api", text)


class SpecChangeRequestSettleTest(_Base):
    REQ_LABELS = ["spec-change:adapt", "spec-change:status:awaiting_approval"]

    def _request_entity(self):
        for label in self.REQ_LABELS + ["spec-change:status:done", "spec-change:status:rejected"]:
            self.remote.create_label(label)
        rid = self.remote.add_entry("adapt request", labels=self.REQ_LABELS).data.id
        entity = Entity.for_labels(post_id=rid, labels=list(self.REQ_LABELS))
        entity.reactions = []
        return rid, entity

    def _write_plan(self, ctx, rid, settle_docs):
        d = spec_change_dir(rid, ctx.storage)
        d.mkdir(parents=True, exist_ok=True)
        (d / "plan.json").write_text(json.dumps({"settle_docs": settle_docs}), encoding="utf-8")

    def _write_doc(self, name, text="---\ncomponent: api\n---\n\n# SRS\n"):
        (self.root / "spec").mkdir(parents=True, exist_ok=True)
        (self.root / "spec" / name).write_text(text, encoding="utf-8")

    def test_approval_settles_docs_and_finalizes(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self._write_doc("api-srs.md")
        self._write_plan(ctx, rid, ["spec/api-srs.md"])
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        self.assertIn("settled", detail)
        self.assertIn("spec-change:status:done", self._remote_labels(rid))
        self.assertNotIn("spec-change:status:awaiting_approval", self._remote_labels(rid))
        self.assertIn("settled: true", (self.root / "spec" / "api-srs.md").read_text(encoding="utf-8"))
        self.assertFalse(self._remote_details(rid).is_open)  # request closed on approval

    def test_approval_enqueues_apply_when_work_present(self) -> None:
        # Plan-first: with a deferred apply.py + work to create, approval queues the
        # apply run and leaves the request OPEN (apply.py finalizes/closes it).
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self._write_doc("api-srs.md")
        d = spec_change_dir(rid, ctx.storage)
        d.mkdir(parents=True, exist_ok=True)
        (d / "plan.json").write_text(
            json.dumps({
                "request_id": rid, "route": "adapt",
                "settle_docs": ["spec/api-srs.md"],
                "creates": [{"tier": "issue", "title": "FEAT-0001", "labels": ["issue", "issue:status:todo"]}],
            }),
            encoding="utf-8",
        )
        (d / "apply.py").write_text("print('noop')\n", encoding="utf-8")
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        self.assertIn("apply enqueued", detail)
        self.assertIn("spec-change:status:done", self._remote_labels(rid))
        self.assertTrue(self._remote_details(rid).is_open)  # the runtime closes it after apply
        apply_task = next(t for t in self.db.tasks_for(rid) if t["action"] == "run_spec_change_script")
        # the approval-path apply is tagged so the executor closes the request on success
        self.assertTrue(apply_task["payload"].get("close_request"))

    def test_double_approval_does_not_enqueue_apply_twice(self) -> None:
        # Two stale approval events (👍 + comment in one drain) must not duplicate
        # the creating apply.py run. The second call sees the remote already 'done'.
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        d = spec_change_dir(rid, ctx.storage)
        d.mkdir(parents=True, exist_ok=True)
        (d / "plan.json").write_text(
            json.dumps({"request_id": rid, "route": "adapt",
                        "creates": [{"tier": "issue", "title": "FEAT-0001", "labels": ["issue", "issue:status:todo"]}]}),
            encoding="utf-8",
        )
        (d / "apply.py").write_text("print('noop')\n", encoding="utf-8")
        advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        # entity is the STALE local snapshot (still awaiting_approval); resolve again
        again = advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        self.assertIn("stale", again)
        apply_tasks = [t for t in self.db.tasks_for(rid) if t["action"] == "run_spec_change_script"]
        self.assertEqual(len(apply_tasks), 1)

    def test_rejection_marks_rejected_and_does_not_settle(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self._write_doc("api-srs.md")
        self._write_plan(ctx, rid, ["spec/api-srs.md"])
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(rejected_by=["alice"]))
        self.assertIn("rejected", detail)
        self.assertIn("spec-change:status:rejected", self._remote_labels(rid))
        self.assertNotIn("settled: true", (self.root / "spec" / "api-srs.md").read_text(encoding="utf-8"))
        self.assertFalse(self._remote_details(rid).is_open)  # request closed on rejection

    def test_no_verdict_returns_none(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self.assertIsNone(advance.resolve_spec_change_request(ctx, entity, _SR2()))

    def test_missing_plan_settles_nothing_but_finalizes(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        self.assertIn("0 docs", detail)
        self.assertIn("spec-change:status:done", self._remote_labels(rid))

    def test_dispatch_resolves_request_without_agent(self) -> None:
        # An approve comment on the request must settle + finalize deterministically,
        # never invoking the agent.
        class _BoomRunner:
            def run(self, *a, **k):
                raise AssertionError("agent must not run for a deterministic approval")

        from specseed_runtime.executing.approvals import approval_request_comment

        for label in self.REQ_LABELS + ["spec-change:status:done"]:
            self.local.create_label(label)
            self.remote.create_label(label)
        marker = approval_request_comment("APR-0001", "the batch")
        rid = self.local.add_entry("adapt request", labels=self.REQ_LABELS).data.id
        self.local.add_entry_comment(rid, marker)
        self.local.add_entry_comment(rid, "approve APR-0001")
        # mirror into remote so the swap can mutate it (same first id)
        self.remote.add_entry("adapt request", labels=self.REQ_LABELS)
        self._write_doc("api-srs.md")
        ctx = self._ctx(self._config(), _BoomRunner())
        self._write_plan(ctx, rid, ["spec/api-srs.md"])
        # the approve comment is authored by "agent" (the local author); make it an approver
        ctx.config["approvals"]["approver_usernames"] = ["agent", "alice"]
        out = dispatch(ctx, {"action": "handle_comment_added", "post_id": str(rid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("settled", out.detail)
        self.assertIn("spec-change:status:done", self._remote_labels(rid))


class ProposeSpecChangeTest(_Base):
    """Plan-first gate: propose posts the summary + APR and parks, creating nothing."""

    def _request(self):
        for lbl in ("spec-change:adapt", "spec-change:status:awaiting_approval"):
            self.remote.create_label(lbl)
        return self.remote.add_entry("adapt request", labels=["spec-change:adapt"]).data.id

    def _write_plan(self, ctx, rid, plan):
        d = spec_change_dir(rid, ctx.storage)
        d.mkdir(parents=True, exist_ok=True)
        (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    def _plan(self, rid):
        return {
            "request_id": rid, "route": "adapt",
            "apr": {"id": "APR-0001", "summary": "Approve CLI scaffold"},
            "plan_summary": "## Proposed plan\n- EPIC-0001 scaffold — the CLI shell\n- FEAT-0001 parsing — arg dispatch",
            "creates": [{"tier": "issue", "title": "FEAT-0001 parsing", "labels": ["issue", "issue:status:todo"]}],
        }

    def _dispatch_propose(self, ctx, rid):
        return dispatch(ctx, {
            "task_id": 1, "action": "propose_spec_change",
            "post_id": rid, "payload": {"request_id": str(rid), "route": "adapt"},
        })

    def test_propose_posts_summary_and_parks_without_creating_posts(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid = self._request()
        before = len(self.remote.list_entries(is_open=None).data)
        self._write_plan(ctx, rid, self._plan(rid))
        out = self._dispatch_propose(ctx, rid)
        self.assertTrue(out.success)
        self.assertIn("spec-change:status:awaiting_approval", self._remote_labels(rid))
        bodies = [c.body for c in self.remote.get_entry(rid).data.comments]
        self.assertTrue(any("APR-0001" in b for b in bodies))
        self.assertTrue(any("Proposed plan" in b for b in bodies))
        # NOTHING new created on the remote - the whole point of plan-first.
        self.assertEqual(len(self.remote.list_entries(is_open=None).data), before)

    def test_propose_is_idempotent(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid = self._request()
        self._write_plan(ctx, rid, self._plan(rid))
        self._dispatch_propose(ctx, rid)
        n1 = len(self.remote.get_entry(rid).data.comments)
        out = self._dispatch_propose(ctx, rid)
        self.assertTrue(out.success)
        self.assertIn("already posted", out.detail)
        self.assertEqual(len(self.remote.get_entry(rid).data.comments), n1)  # no double-post

    def test_propose_without_apr_fails(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid = self._request()
        plan = self._plan(rid)
        plan.pop("apr")
        self._write_plan(ctx, rid, plan)
        out = self._dispatch_propose(ctx, rid)
        self.assertFalse(out.success)

    def test_propose_missing_plan_fails(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid = self._request()
        out = self._dispatch_propose(ctx, rid)
        self.assertFalse(out.success)


if __name__ == "__main__":
    unittest.main()
