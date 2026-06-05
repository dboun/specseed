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
        ctx = self._ctx(self._auto_off(), FakeAgentRunner(AgentResult(ok=True, returncode=0)))
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
                        FakeAgentRunner(AgentResult(ok=True, returncode=0)))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(eid))

    def test_auto_on_implements_without_parking(self) -> None:
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        cfg = self._config()
        cfg["permissions"]["platform"] = {"auto_implement_issue": True}
        ctx = self._ctx(cfg, FakeAgentRunner(AgentResult(ok=True, returncode=0)))
        out = dispatch(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(eid))


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
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True, returncode=0)))
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
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True, returncode=0)))
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

    def test_rejection_marks_rejected_and_does_not_settle(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self._write_doc("api-srs.md")
        self._write_plan(ctx, rid, ["spec/api-srs.md"])
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(rejected_by=["alice"]))
        self.assertIn("rejected", detail)
        self.assertIn("spec-change:status:rejected", self._remote_labels(rid))
        self.assertNotIn("settled: true", (self.root / "spec" / "api-srs.md").read_text(encoding="utf-8"))

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


if __name__ == "__main__":
    unittest.main()
