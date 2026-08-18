"""test_advance.py - in-code state transitions + code-review loop.

FakeAgentRunner only; TrackingRemoteLocal/TrackingLocal mirrors. No GitHub/GitLab.
"""

from __future__ import annotations

import json
import subprocess
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
        # A real git repo so a merge-on-completion (merge auto-on) can actually run -
        # the runtime owns git. dbs + storage are gitignored so checkouts never fight
        # open file handles.
        (self.root / ".gitignore").write_text("*.db\n*.db-*\nstorage/\n", encoding="utf-8")
        self._git("init")
        self._git("symbolic-ref", "HEAD", "refs/heads/main")
        # Commit the .gitignore (real specseed scaffold does) so it is stable across
        # branches; otherwise `git add -A` stages it onto a feature branch and a
        # later `checkout main` deletes it, exposing the sqlite dbs to staging.
        self._git("add", ".gitignore")
        self._git("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-m", "root")
        self.remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="alice")
        self.local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        self.db = Database(db_path=self.root / "queue.db")

    def _git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.root),
                              capture_output=True, text=True)

    def _config(self, review=None, merge=False):
        cfg = {
            "specseed_dir": "seedmeta",
            "specseed_primary_branch": "main",
            "approvals": {"approver_usernames": ["alice"]},
            "permissions": {},
        }
        if merge:
            cfg["permissions"]["git"] = {"merge_to_primary": True}
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

    def _drain(self, ctx, max_iter=100):
        """Drive the queue to quiescence like the two-lane scheduler.

        The split means a work-triggering control event now SCHEDULES a work_run
        instead of running the agent inline; the agent run + post-work transition
        (+ any merge) play out across the work lane and a process_work_result
        control item. This claims control-then-work, dispatches, and completes,
        so a test can assert the final remote state exactly as the live runner.
        """
        from specseed_runtime.db.database import LANE_CONTROL, LANE_WORK

        for _ in range(max_iter):
            task = self.db.claim_next(LANE_CONTROL) or self.db.claim_next(LANE_WORK)
            if task is None:
                return
            out = dispatch(ctx, task)
            if out is not None and out.requeue and not out.quota:
                # park the retry far out so the drain terminates (these tests do not
                # exercise the retry path).
                self.db.requeue(task["task_id"], not_before="2999-01-01T00:00:00Z")
            else:
                self.db.complete(task["task_id"], bool(out and out.success), out.error if out else None)
        raise AssertionError("drain did not quiesce within max_iter")

    def _run(self, ctx, task):
        """Dispatch a control event, then drain the work it schedules."""
        out = dispatch(ctx, task)
        self._drain(ctx)
        return out

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

    def _gate_comment(self, marker, apr="APR-0001", thumbs=(), heart=(), down=(), author="specseed"):
        """A platform gate comment (its marker + APR token) with reactions ON it -
        the merge-gate approval is read from this comment, not the post."""
        reactions = []
        if thumbs:
            reactions.append({"kind": "thumbs_up", "users": list(thumbs)})
        if heart:
            reactions.append({"kind": "heart", "users": list(heart)})
        if down:
            reactions.append({"kind": "thumbs_down", "users": list(down)})
        body = "gate\n{0}\n<!-- {1} {2} -->".format(marker, advance.APPROVAL_REQUEST_MARKER, apr)
        return {"id": apr, "author": author, "body": body, "reactions": reactions}


class ImplementTransitionTest(_Base):
    def test_implement_no_review_closes_done(self) -> None:
        # merge auto-on: implement -> merge branch -> close done (no human gate).
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        ctx = self._ctx(self._config(merge=True), FakeAgentRunner(_impl_ok()))
        out = self._run(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:done", labels)
        self.assertNotIn("issue:status:todo", labels)
        self.assertFalse(self._remote_details(eid).is_open)

    def test_implement_with_review_goes_in_review(self) -> None:
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        ctx = self._ctx(self._config(review={"enabled": True}),
                        FakeAgentRunner(_impl_ok()))
        out = self._run(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
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
        out = self._run(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
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
        out = self._run(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
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
        ctx = self._ctx(self._config(review={"enabled": True, "confidence_threshold": 0.75}, merge=True),
                        self._review_runner("approve", 0.9))
        out = self._run(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
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
        out = self._run(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._remote_labels(eid)
        self.assertIn("issue:status:awaiting_approval", labels)
        self.assertNotIn("issue:status:todo", labels)
        self.assertTrue(self._remote_details(eid).is_open)

    def test_review_pass_ignores_legacy_human_gate_key(self) -> None:
        # require_human_approval is gone; a stale key in config changes nothing.
        eid = self._seed("Review me", ["issue", "issue:status:in_review"])
        ctx = self._ctx(
            self._config(review={"enabled": True, "require_human_approval": True}, merge=True),
            self._review_runner("approve", 0.95),
        )
        out = self._run(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
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
        out = self._run(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
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
        out = self._run(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
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
        out = self._run(ctx, {"action": "handle_comment_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:blocked", self._remote_labels(eid))
        self.assertEqual(len(self._drafts()), 1)

    def _all_remote_entries(self):
        listing = self.remote.list_entries()
        out = []
        for summary in listing.data:
            out.append(self.remote.get_entry(summary.id).data)
        return out


class AskTransitionTest(_Base):
    """The read-only ask run posts its answer as a platform comment, post left open."""

    def test_answered_posts_platform_comment_and_leaves_open(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner())
        eid = self._seed("How does X work?", ["ask"])
        entity = Entity.for_labels(post_id=str(eid), labels=["ask"], title="How does X work?")
        result = AgentResult(ok=True, returncode=0,
                             report={"status": "answered", "answer": "The spec says X."})

        detail = advance.apply_ask_answer(ctx, entity, result)

        self.assertIn("answered", detail)
        details = self._remote_details(eid)
        bodies = [c.body or "" for c in details.comments]
        self.assertTrue(any("The spec says X." in b for b in bodies))
        # platform-authored (specseed: prefix) so the next sync never re-triggers the ask
        self.assertTrue(any(b.startswith("specseed: ") for b in bodies))
        self.assertTrue(details.is_open)  # human closes the thread, not the runtime

    def test_needs_input_round_also_posts(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner())
        eid = self._seed("Ambiguous?", ["ask"])
        entity = Entity.for_labels(post_id=str(eid), labels=["ask"], title="Ambiguous?")
        result = AgentResult(ok=True, returncode=0,
                             report={"status": "needs_input", "answer": "Round 1 - which X?"})

        advance.apply_ask_answer(ctx, entity, result)

        bodies = [c.body or "" for c in self._remote_details(eid).comments]
        self.assertTrue(any("Round 1 - which X?" in b for b in bodies))


class ImplementApprovalGateTest(_Base):
    """platform.auto_implement_issue=False parks a ready issue for sign-off."""

    def _auto_off(self, approvers=None, merge=False):
        cfg = self._config(merge=merge)
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
        ctx = self._ctx(self._auto_off(approvers=["agent"], merge=True),
                        FakeAgentRunner(_impl_ok()))
        out = self._run(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(eid))

    def test_auto_on_implements_without_parking(self) -> None:
        eid = self._seed("Do it", ["issue", "issue:status:todo"])
        cfg = self._config(merge=True)
        cfg["permissions"]["platform"] = {"auto_implement_issue": True}
        ctx = self._ctx(cfg, FakeAgentRunner(_impl_ok()))
        out = self._run(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(eid))


class _SR:
    """Minimal state-result stub for resolve_approval / resolve_blocked.

    ``approved_by`` = 👍 (work / single gate). ``merge_approved_by`` = ❤️ (approve +
    merge in one step). ``rejected_by`` = 👎.
    """

    def __init__(self, approved_by=(), rejected_by=(), merge_approved_by=()):
        self.approved_by = list(approved_by)
        self.rejected_by = list(rejected_by)
        self.merge_approved_by = list(merge_approved_by)


_COMMENT = "handle_comment_added"
_REACT = "handle_reaction_added"


class ApprovalGateTest(_Base):
    _REVIEWED = ["review\n" + REVIEW_MARKER]

    def test_completion_gate_merges_when_approved_auto_on(self) -> None:
        # merge auto-on: 👍 on a reviewed issue signals the merge (dispatch runs it
        # and closes). advance does NOT close here - it only signals.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=self._REVIEWED)
        ctx = self._ctx(self._config(merge=True), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        t = advance.resolve_approval(ctx, entity, _SR(["alice"]), conversation)
        self.assertTrue(t.merge)
        self.assertIn("merging", t.detail)

    def test_combined_gate_work_approve_readies_merge(self) -> None:
        # below-bar + merge gated = COMBINED gate. 👍 ON the gate comment approves the
        # WORK and asks dispatch to ready a follow-up merge gate (prepare). No naked
        # merge; a standing post 👍 would be inert.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        conv = [self._gate_comment(advance.WORK_MERGE_GATE_MARKER, thumbs=["alice"])]
        t = advance.resolve_approval(ctx, entity, _SR([]), conv, _REACT)
        self.assertTrue(t.prepare)
        self.assertFalse(t.merge)
        self.assertIn("readying merge", t.detail)

    def test_combined_gate_heart_approves_and_merges(self) -> None:
        # ❤️ ON the combined gate comment approves the work AND readies+merges (prepare
        # then merge in one step).
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        conv = [self._gate_comment(advance.WORK_MERGE_GATE_MARKER, heart=["alice"])]
        t = advance.resolve_approval(ctx, entity, _SR([]), conv, _REACT)
        self.assertTrue(t.prepare)
        self.assertTrue(t.merge)
        self.assertIn("merging", t.detail)

    def test_pure_merge_gate_approved_signals_merge(self) -> None:
        # 👍 ON the live pure-merge-gate comment authorizes the merge (re-ready + merge).
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        conv = [self._gate_comment(advance.MERGE_GATE_MARKER, thumbs=["alice"])]
        t = advance.resolve_approval(ctx, entity, _SR([]), conv, _REACT)
        self.assertTrue(t.merge)
        self.assertIn("merge gate approved", t.detail)

    def test_pure_merge_gate_post_reaction_is_inert(self) -> None:
        # A 👍 on the POST (state_result), with NO reaction on the gate comment, must
        # NOT merge - this was the loop's durable re-fire.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        conv = [self._gate_comment(advance.MERGE_GATE_MARKER)]
        t = advance.resolve_approval(ctx, entity, _SR(["alice"]), conv, _REACT)
        self.assertFalse(t.merge)
        self.assertFalse(t.prepare)
        self.assertIsNone(t.detail)

    def test_stale_gate_comment_reaction_is_inert(self) -> None:
        # A 👍 left on a SUPERSEDED gate comment (older APR) does not approve the live
        # gate (the latest gate comment, no reaction).
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        old = self._gate_comment(advance.MERGE_GATE_MARKER, apr="APR-0001", thumbs=["alice"])
        new = self._gate_comment(advance.MERGE_GATE_MARKER, apr="APR-0002")
        t = advance.resolve_approval(ctx, entity, _SR([]), [old, new], _REACT)
        self.assertFalse(t.merge)
        self.assertIsNone(t.detail)

    def test_command_approves_the_live_gate(self) -> None:
        # `approve <live APR>` from an approver hits the latest gate.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        gate = self._gate_comment(advance.MERGE_GATE_MARKER, apr="APR-0002")
        cmd = {"author": "alice", "body": "approve APR-0002", "reactions": []}
        t = advance.resolve_approval(ctx, entity, _SR([]), [gate, cmd], _COMMENT)
        self.assertTrue(t.merge)

    def test_command_for_superseded_gate_is_inert(self) -> None:
        # `approve <old APR>` does not approve a newer gate (APR-0002 is live).
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        old = self._gate_comment(advance.MERGE_GATE_MARKER, apr="APR-0001")
        new = self._gate_comment(advance.MERGE_GATE_MARKER, apr="APR-0002")
        cmd = {"author": "alice", "body": "approve APR-0001", "reactions": []}
        t = advance.resolve_approval(ctx, entity, _SR([]), [old, new, cmd], _COMMENT)
        self.assertFalse(t.merge)
        self.assertIsNone(t.detail)

    def test_open_merge_gate_ready_posts_fresh_gate(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:in_review"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        detail = advance.open_merge_gate_ready(ctx, entity)
        self.assertIn("merge gate", detail)
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))
        bodies = "\n".join(c.body for c in self._remote_details(eid).comments)
        self.assertIn(advance.MERGE_GATE_MARKER, bodies)
        self.assertIn("APR-0001", bodies)  # first token minted from the storage counter

    def test_pure_merge_gate_declined_parks_awaiting_merge(self) -> None:
        # 👎 ON the pure merge gate comment = decline the runtime merge. NOT done (nothing is
        # done until on primary): park `awaiting_merge`, branch left for a manual merge, post
        # stays OPEN so a dependent keyed on `done` stays held.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        conv = [self._gate_comment(advance.MERGE_GATE_MARKER, down=["alice"])]
        t = advance.resolve_approval(ctx, entity, _SR([]), conv, _REACT)
        self.assertFalse(t.merge)
        self.assertIn("awaiting_merge", t.detail)
        self.assertIn("issue:status:awaiting_merge", self._remote_labels(eid))
        self.assertNotIn("issue:status:done", self._remote_labels(eid))
        self.assertTrue(self._remote_details(eid).is_open)
        # a fresh awaiting-merge gate comment is posted (new APR, no carried reaction)
        bodies = "\n".join(c.body for c in self._remote_details(eid).comments)
        self.assertIn(advance.AWAITING_MERGE_MARKER, bodies)

    def test_awaiting_merge_approve_merges(self) -> None:
        # 👍 on the awaiting_merge gate comment authorizes the runtime merge: re-ready +
        # merge (idempotent, so it also just confirms a hand-merge), then done.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_merge"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        conv = [self._gate_comment(advance.MERGE_GATE_MARKER, thumbs=["alice"])]
        t = advance.resolve_awaiting_merge(ctx, entity, _SR([]), conv, _REACT)
        self.assertTrue(t.prepare)
        self.assertTrue(t.merge)

    def test_awaiting_merge_no_signal_waits(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_merge"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        conv = [self._gate_comment(advance.MERGE_GATE_MARKER)]
        t = advance.resolve_awaiting_merge(ctx, entity, _SR([]), conv, _REACT)
        self.assertFalse(t.merge)
        self.assertFalse(t.prepare)
        self.assertIsNone(t.detail)
        self.assertIn("issue:status:awaiting_merge", self._remote_labels(eid))

    def test_awaiting_merge_prose_reworks_to_todo(self) -> None:
        # A free-text comment is taken as change guidance: rework, not merge.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_merge"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        conv = [
            self._gate_comment(advance.MERGE_GATE_MARKER),
            {"author": "alice", "body": "actually rename the flag first", "reactions": []},
        ]
        t = advance.resolve_awaiting_merge(ctx, entity, _SR([]), conv, _COMMENT)
        self.assertFalse(t.merge)
        self.assertIn("todo", t.detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_prework_gate_resumes_when_approved(self) -> None:
        # No review attempts recorded = a pre-work HITL gate: 👍 resumes to todo.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        t = advance.resolve_approval(ctx, entity, _SR(["alice"]), conversation)
        self.assertIn("todo", t.detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_not_approved_stays_parked(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        self.assertIsNone(advance.resolve_approval(ctx, entity, _SR([]), conversation).detail)
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))

    def test_prose_comment_redirects_to_todo_with_guidance(self) -> None:
        # A free-text comment is taken as change guidance: resume the implementer.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["please rename the function to run()"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        t = advance.resolve_approval(ctx, entity, _SR([]), conversation, _COMMENT)
        self.assertIn("guidance", t.detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_retry_comment_redirects_to_todo_blind(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["retry"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        t = advance.resolve_approval(ctx, entity, _SR([]), conversation, _COMMENT)
        self.assertIn("retry", t.detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_bare_reject_reaction_posts_options_once(self) -> None:
        # 👎 with no guidance on a WORK gate (a reaction event, not a comment): post
        # the options prompt and wait; the issue stays parked. Second pass: nothing.
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        t = advance.resolve_approval(ctx, entity, _SR(rejected_by=["alice"]), conversation, _REACT)
        self.assertIn("options", t.detail)
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))
        self.assertIn(advance.OPTIONS_MARKER,
                      "\n".join(c.body for c in self._remote_details(eid).comments))
        # second pass: options already posted -> nothing applied.
        entity, conversation = _load(ctx, eid)
        again = advance.resolve_approval(ctx, entity, _SR(rejected_by=["alice"]), conversation, _REACT)
        self.assertIsNone(again.detail)
        options = [c for c in self._remote_details(eid).comments if advance.OPTIONS_MARKER in (c.body or "")]
        self.assertEqual(len(options), 1)

    def test_bare_reject_command_posts_options(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["reject"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        t = advance.resolve_approval(ctx, entity, _SR(rejected_by=["alice"]), conversation, _COMMENT)
        self.assertIn("options", t.detail)
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))

    def test_reject_with_prose_is_guidance(self) -> None:
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["reject use a dataclass instead"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        t = advance.resolve_approval(ctx, entity, _SR(rejected_by=["alice"]), conversation, _COMMENT)
        self.assertIn("guidance", t.detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_directive_ignored_on_non_comment_event(self) -> None:
        # A stale prose comment must NOT redirect when the waking event is a label
        # change (no fresh human directive).
        eid = self._seed("Gate", ["issue", "issue:status:awaiting_approval"],
                         comments=["do something"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        self.assertIsNone(
            advance.resolve_approval(ctx, entity, _SR([]), conversation, "handle_label_added").detail
        )
        self.assertIn("issue:status:awaiting_approval", self._remote_labels(eid))


class BlockedBypassTest(_Base):
    def _approve_cmd(self, eid, verb="approve"):
        return [{"author": "alice", "body": "{0} {1}".format(verb, eid), "reactions": []}]

    def test_approve_command_forces_merge_auto_on(self) -> None:
        # merge auto-on: an `approve <id>` COMMAND over a block readies+merges and
        # closes. A bare reaction no longer clears a block (the loop fix).
        eid = self._seed("Stuck", ["issue", "issue:status:blocked"])
        ctx = self._ctx(self._config(merge=True), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        t = advance.resolve_blocked(ctx, entity, _SR([]), self._approve_cmd(eid), _COMMENT)
        self.assertTrue(t.merge)
        self.assertIn("merging", t.detail)

    def test_approve_command_over_block_readies_merge_when_gated(self) -> None:
        # merge gated: an `approve <id>` command over a block force-approves the work
        # and readies a merge gate (override never lands code on primary unmerged).
        eid = self._seed("Stuck", ["issue", "issue:status:blocked"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        t = advance.resolve_blocked(ctx, entity, _SR([]), self._approve_cmd(eid), _COMMENT)
        self.assertFalse(t.merge)
        self.assertTrue(t.prepare)
        self.assertIn("readying merge", t.detail)

    def test_merge_command_over_block_merges_when_gated(self) -> None:
        # a `merge <id>` command force-approves AND merges in one step even when gated.
        eid = self._seed("Stuck", ["issue", "issue:status:blocked"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        t = advance.resolve_blocked(ctx, entity, _SR([]), self._approve_cmd(eid, "merge"), _COMMENT)
        self.assertTrue(t.merge)
        self.assertIn("merging", t.detail)

    def test_standing_post_reaction_does_not_clear_block(self) -> None:
        # The loop fix: a durable post 👍 + a reaction event must NOT move a block.
        eid = self._seed("Stuck", ["issue", "issue:status:blocked"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = _load(ctx, eid)
        t = advance.resolve_blocked(ctx, entity, _SR(["alice"]), [], _REACT)
        self.assertIsNone(t.detail)
        self.assertFalse(t.merge)
        self.assertFalse(t.prepare)
        self.assertIn("issue:status:blocked", self._remote_labels(eid))

    def test_guidance_comment_reopens_to_todo(self) -> None:
        eid = self._seed("Stuck", ["issue", "issue:status:blocked"],
                         comments=["try the other API"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        t = advance.resolve_blocked(ctx, entity, _SR([]), conversation, _COMMENT)
        self.assertIn("todo", t.detail)
        self.assertIn("issue:status:todo", self._remote_labels(eid))

    def test_nothing_actionable_stays_blocked(self) -> None:
        eid = self._seed("Stuck", ["issue", "issue:status:blocked"])
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = _load(ctx, eid)
        self.assertIsNone(advance.resolve_blocked(ctx, entity, _SR([]), conversation, _REACT).detail)
        self.assertIn("issue:status:blocked", self._remote_labels(eid))


def _load(ctx, eid):
    from specseed_runtime.executing.context import load_entity
    return load_entity(ctx, str(eid))


class StateMachineAwaitingMergeTest(unittest.TestCase):
    def test_awaiting_merge_next_states(self) -> None:
        from specseed_runtime.state_machines import base as sm
        e = Entity.for_labels(post_id="1", labels=["issue", "issue:status:awaiting_merge"])
        nxt = sm.possible_next_states(e, {})
        self.assertIn("done", nxt)
        self.assertIn("todo", nxt)
        self.assertIn("blocked", nxt)

    def test_awaiting_merge_is_not_terminal(self) -> None:
        from specseed_runtime.state_machines import base as sm
        self.assertNotIn("awaiting_merge", sm.TERMINAL_STATES)


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
        # Bodies carry ONLY upward links (Epic:/Ticket:) - exactly what plan-first
        # creation emits, since a parent never knows its child ids at creation. The
        # roll-up must discover children from these upward links, not a downward
        # ``Issues:`` / ``## Tickets`` list (which is never populated in reality).
        specs = [
            ("Epic", ["epic", "epic:status:todo"], "# Epic\n"),
            ("Ticket", ["ticket", "ticket:status:todo"], "Epic: #1\n"),
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
        ctx = self._ctx(self._config(merge=True), FakeAgentRunner(_impl_ok()))
        out = self._run(ctx, {"action": "handle_label_added", "post_id": "4", "payload": {}})
        self.assertTrue(out.success)
        self.assertIn("issue:status:done", self._remote_labels(4))
        # ticket #2 and epic #1 rolled up to done + closed
        self.assertIn("ticket:status:done", self._remote_labels(2))
        self.assertFalse(self._remote_details(2).is_open)
        self.assertIn("epic:status:done", self._remote_labels(1))
        self.assertFalse(self._remote_details(1).is_open)
        # (the roll-up note now lands on the process_work_result outcome, not the
        # initial scheduling outcome; the remote state above is the real assertion.)

    def test_open_sibling_keeps_parents_open(self) -> None:
        # Issue A is NOT finished -> finishing B must not close the ticket/epic.
        self._tree()
        self.remote.set_entry_open(3)
        self.remote.remove_entry_label(3, "issue:status:done")
        self.remote.add_entry_label(3, "issue:status:in_progress")
        ctx = self._ctx(self._config(merge=True), FakeAgentRunner(_impl_ok()))
        out = self._run(ctx, {"action": "handle_label_added", "post_id": "4", "payload": {}})
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

    def _live_spec(self, ctx):
        from specseed_runtime.storage_paths import spec_dir
        return spec_dir(ctx.storage)

    def _write_doc(self, ctx, name, text="---\ncomponent: api\n---\n\n# SRS\n"):
        live = self._live_spec(ctx)
        live.mkdir(parents=True, exist_ok=True)
        (live / name).write_text(text, encoding="utf-8")

    def _stage_doc(self, ctx, rid, rel, text="# staged SRS\n"):
        from specseed_runtime.scheduling.spec_change import spec_change_spec_dir
        dest = spec_change_spec_dir(rid, ctx.storage) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return dest

    def test_approval_settles_docs_and_finalizes(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self._write_doc(ctx, "api-srs.md")
        self._write_plan(ctx, rid, ["spec/api-srs.md"])
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        self.assertIn("settled", detail)
        self.assertIn("spec-change:status:done", self._remote_labels(rid))
        self.assertNotIn("spec-change:status:awaiting_approval", self._remote_labels(rid))
        self.assertIn("settled: true", (self._live_spec(ctx) / "api-srs.md").read_text(encoding="utf-8"))
        self.assertFalse(self._remote_details(rid).is_open)  # request closed on approval

    def test_approval_enqueues_plan_apply_when_work_present(self) -> None:
        # Plan-first: with work to create, approval queues the runtime JSON plan
        # executor and leaves the request OPEN (plan apply finalizes/closes it).
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self._write_doc(ctx, "api-srs.md")
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
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        self.assertIn("apply enqueued", detail)
        self.assertIn("spec-change:status:done", self._remote_labels(rid))
        self.assertTrue(self._remote_details(rid).is_open)  # the runtime closes it after apply
        apply_task = next(t for t in self.db.tasks_for(rid) if t["action"] == "apply_spec_change_plan")
        # the approval-path apply is tagged so the executor closes the request on success
        self.assertTrue(apply_task["payload"].get("close_request"))

    def test_double_approval_does_not_enqueue_apply_twice(self) -> None:
        # Two stale approval events (👍 + comment in one drain) must not duplicate
        # the creating plan apply run. The second call sees the remote already 'done'.
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        d = spec_change_dir(rid, ctx.storage)
        d.mkdir(parents=True, exist_ok=True)
        (d / "plan.json").write_text(
            json.dumps({"request_id": rid, "route": "adapt",
                        "creates": [{"tier": "issue", "title": "FEAT-0001", "labels": ["issue", "issue:status:todo"]}]}),
            encoding="utf-8",
        )
        advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        # entity is the STALE local snapshot (still awaiting_approval); resolve again
        again = advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        self.assertIn("stale", again)
        apply_tasks = [t for t in self.db.tasks_for(rid) if t["action"] == "apply_spec_change_plan"]
        self.assertEqual(len(apply_tasks), 1)

    def test_rejection_marks_rejected_and_does_not_settle(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self._write_doc(ctx, "api-srs.md")
        self._write_plan(ctx, rid, ["spec/api-srs.md"])
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(rejected_by=["alice"]))
        self.assertIn("rejected", detail)
        self.assertIn("spec-change:status:rejected", self._remote_labels(rid))
        self.assertNotIn("settled: true", (self._live_spec(ctx) / "api-srs.md").read_text(encoding="utf-8"))
        self.assertFalse(self._remote_details(rid).is_open)  # request closed on rejection

    def test_no_verdict_returns_none(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self.assertIsNone(advance.resolve_spec_change_request(ctx, entity, _SR2()))

    def test_missing_plan_settles_nothing_but_finalizes(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        self.assertIn("settled 0", detail)
        self.assertIn("spec-change:status:done", self._remote_labels(rid))

    def test_approval_promotes_staged_spec_into_live_tree(self) -> None:
        # Plan-first staging: the worker wrote the doc into the request's staging dir,
        # NOT live spec/. Approval is what copies it into the live tree, then settles it.
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self._stage_doc(ctx, rid, "api-srs.md", text="---\ncomponent: api\n---\n\n# new SRS\n")
        self._write_plan(ctx, rid, ["spec/api-srs.md"])
        live = self._live_spec(ctx) / "api-srs.md"
        self.assertFalse(live.exists())  # nothing in live spec/ before approval
        detail = advance.resolve_spec_change_request(ctx, entity, _SR2(approved_by=["alice"]))
        self.assertIn("promoted 1", detail)
        self.assertTrue(live.exists())  # promoted on approval
        body = live.read_text(encoding="utf-8")
        self.assertIn("# new SRS", body)
        self.assertIn("settled: true", body)  # promoted THEN settled

    def test_rejection_does_not_promote_staged_spec(self) -> None:
        ctx = self._ctx(self._config(), FakeAgentRunner(AgentResult(ok=True)))
        rid, entity = self._request_entity()
        self._stage_doc(ctx, rid, "api-srs.md")
        self._write_plan(ctx, rid, ["spec/api-srs.md"])
        advance.resolve_spec_change_request(ctx, entity, _SR2(rejected_by=["alice"]))
        self.assertFalse((self._live_spec(ctx) / "api-srs.md").exists())  # never promoted

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
        ctx = self._ctx(self._config(), _BoomRunner())
        self._write_doc(ctx, "api-srs.md")
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
