"""test_dispatch.py - action -> handler routing + intent decisions.

FakeAgentRunner only; TrackingRemoteLocal/TrackingLocal mirrors. No GitHub/GitLab.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

from specseed_runtime.db.database import Database
from specseed_runtime.executing.agent_runner import (
    AgentResult,
    FakeAgentRunner,
    RunnerChains,
)
from specseed_runtime.executing.context import ExecutionContext
from specseed_runtime.executing import dispatch as dispatch_mod
from specseed_runtime.executing.dispatch import (
    AgentIntent,
    HandlerOutcome,
    decide_intent,
    dispatch,
)
from specseed_runtime.executing.permissions import Permissions
from specseed_runtime.entities.entity_base import Entity
# Importing the tier modules registers Epic/Ticket/Issue in the tier registry.
from specseed_runtime.entities import issue as _issue  # noqa: F401
from specseed_runtime.state_machines.base import evaluate_entity_state
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


def _config():
    return {
        "specseed_dir": "seedmeta",
        "approvals": {"approver_usernames": ["alice"]},
        "permissions": {},
    }


class DispatchTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text("*.db\n*.db-*\nstorage/\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-m", "root"],
            cwd=self.root, check=True, capture_output=True,
        )
        self.remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="alice")
        self.local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        self.db = Database(db_path=self.root / "queue.db")
        self.config = _config()
        self.runner = FakeAgentRunner()
        self.ctx = ExecutionContext(
            db=self.db,
            local=self.local,
            remote=self.remote,
            config=self.config,
            permissions=Permissions(self.config),
            runner=self.runner,
            repo_root=self.root,
            storage=self.root / "storage",
            cancel=threading.Event(),
            agent_timeout_s=30.0,
        )

    def _seed_local_entry(self, title, labels, assignees=("alice",)):
        # Put an entry into the LOCAL mirror (load_entity reads local). Default it
        # assigned to the agent ("alice" is the approver, so agent_assignee()) - the
        # normal "ready to work" state once auto-assign or a human has acted, so the
        # implement gate passes. Tests of the assignment gate pass assignees=().
        for label in labels:
            self.local.create_label(label)
        return self.local.add_entry(title, labels=labels, assignees=list(assignees)).data.id

    # -- two-lane drain helpers ------------------------------------------- #
    # A work-triggering control event now SCHEDULES a work_run instead of running
    # the agent inline; the agent + transition (+ merge) play out across the work
    # lane and a process_work_result control item. These mimic the scheduler.
    def _drain(self, max_iter=100):
        from specseed_runtime.db.database import LANE_CONTROL, LANE_WORK

        for _ in range(max_iter):
            task = self.db.claim_next(LANE_CONTROL) or self.db.claim_next(LANE_WORK)
            if task is None:
                return
            out = dispatch(self.ctx, task)
            if out is not None and out.requeue and not out.quota:
                self.db.requeue(task["task_id"], not_before="2999-01-01T00:00:00Z")
            else:
                self.db.complete(task["task_id"], bool(out and out.success), out.error if out else None)
        raise AssertionError("drain did not quiesce within max_iter")

    def _run_full(self, ctx, task):
        """Dispatch a control event then drain everything it schedules."""
        out = dispatch(ctx, task)
        self._drain()
        return out

    def _dispatch_then_work(self, ctx, task):
        """Dispatch a control event then run the ONE work job it schedules, returning
        that work job's outcome (so a test can assert the agent run's result)."""
        from specseed_runtime.db.database import LANE_WORK

        out = dispatch(ctx, task)
        work = self.db.claim_next(LANE_WORK)
        if work is None:
            return out
        return dispatch(ctx, work)


class DecideIntentTest(DispatchTestBase):
    def _intent_for(self, labels, action="handle_label_added"):
        entity = Entity.for_labels(post_id="1", labels=labels, title="t")
        sr = evaluate_entity_state(entity, self.config, [])
        return entity, decide_intent(entity, sr, {"action": action})

    def test_spec_change_label_to_spec_change(self) -> None:
        _, intent = self._intent_for(["spec-change:adopt"])
        self.assertEqual(intent, AgentIntent.SPEC_CHANGE)

    def test_spec_change_open_status_is_actionable(self) -> None:
        _, intent = self._intent_for(["spec-change:adapt", "spec-change:status:open"])
        self.assertEqual(intent, AgentIntent.SPEC_CHANGE)

    def test_spec_change_done_status_is_not_rerun(self) -> None:
        # A finished request must never re-run the worker, however it is poked.
        for action in ("handle_entry_updated", "handle_label_added", "handle_comment_added"):
            _, intent = self._intent_for(
                ["spec-change:adapt", "spec-change:status:done"], action=action
            )
            self.assertEqual(intent, AgentIntent.NONE, action)

    def test_spec_change_rejected_only_wakes_on_comment(self) -> None:
        # Rejecting a plan means "redraft it", not "abandon the request": the request
        # stays open and the human's next comment re-runs the worker. Label churn or an
        # updated_at bump still must not.
        labels = ["spec-change:adapt", "spec-change:status:rejected"]
        _, churn = self._intent_for(labels, action="handle_entry_updated")
        self.assertEqual(churn, AgentIntent.NONE)
        _, reply = self._intent_for(labels, action="handle_comment_added")
        self.assertEqual(reply, AgentIntent.SPEC_CHANGE)

    def test_closed_rejected_spec_change_never_reruns(self) -> None:
        # Closing the post is how a human drops a rejected request for good.
        entity = Entity.for_labels(
            post_id="1", labels=["spec-change:adapt", "spec-change:status:rejected"], title="t"
        )
        entity.is_open = False
        sr = evaluate_entity_state(entity, self.config, [])
        self.assertEqual(
            decide_intent(entity, sr, {"action": "handle_comment_added"}), AgentIntent.NONE
        )

    def test_spec_change_awaiting_approval_only_wakes_on_comment(self) -> None:
        labels = ["spec-change:adapt", "spec-change:status:awaiting_approval"]
        _, churn = self._intent_for(labels, action="handle_entry_updated")
        self.assertEqual(churn, AgentIntent.NONE)
        _, reply = self._intent_for(labels, action="handle_comment_added")
        self.assertEqual(reply, AgentIntent.SPEC_CHANGE)

    def test_spec_change_awaiting_input_only_wakes_on_comment(self) -> None:
        # The question-round parked status behaves like awaiting_approval for waking.
        labels = ["spec-change:adapt", "spec-change:status:awaiting_input"]
        _, churn = self._intent_for(labels, action="handle_entry_updated")
        self.assertEqual(churn, AgentIntent.NONE)
        _, reply = self._intent_for(labels, action="handle_comment_added")
        self.assertEqual(reply, AgentIntent.SPEC_CHANGE)

    def test_spec_change_status_label_ignored(self) -> None:
        # A spec-change:status:* label is not a route.
        _, intent = self._intent_for(["spec-change:status:awaiting_approval"])
        self.assertEqual(intent, AgentIntent.NONE)

    def test_draft_spec_change_is_ignored(self) -> None:
        _, intent = self._intent_for(["draft", "spec-change:adapt"])
        self.assertEqual(intent, AgentIntent.NONE)

    def test_todo_issue_to_implement(self) -> None:
        _, intent = self._intent_for(["tier:issue", "status:todo"])
        self.assertEqual(intent, AgentIntent.IMPLEMENT)

    def test_issue_no_status_to_implement(self) -> None:
        _, intent = self._intent_for(["tier:issue"])
        self.assertEqual(intent, AgentIntent.IMPLEMENT)

    def test_in_review_to_review(self) -> None:
        _, intent = self._intent_for(["tier:issue", "status:in_review"])
        self.assertEqual(intent, AgentIntent.REVIEW)

    def test_in_progress_issue_to_none(self) -> None:
        _, intent = self._intent_for(["tier:issue", "status:in_progress"])
        self.assertEqual(intent, AgentIntent.NONE)

    def test_epic_todo_to_none(self) -> None:
        # IMPLEMENT is issue-only.
        _, intent = self._intent_for(["tier:epic", "status:todo"])
        self.assertEqual(intent, AgentIntent.NONE)

    def test_ask_label_on_label_add_to_ask(self) -> None:
        _, intent = self._intent_for(["ask"], action="handle_label_added")
        self.assertEqual(intent, AgentIntent.ASK)

    def test_ask_label_on_comment_to_ask(self) -> None:
        # a human follow-up comment re-triggers the answer
        _, intent = self._intent_for(["ask"], action="handle_comment_added")
        self.assertEqual(intent, AgentIntent.ASK)

    def test_ask_label_other_action_to_none(self) -> None:
        # label churn / reactions must not re-run the ask
        for action in ("handle_entry_updated", "handle_label_removed"):
            _, intent = self._intent_for(["ask"], action=action)
            self.assertEqual(intent, AgentIntent.NONE, action)

    def test_draft_ask_is_ignored(self) -> None:
        _, intent = self._intent_for(["draft", "ask"])
        self.assertEqual(intent, AgentIntent.NONE)


class DispatchRoutingTest(DispatchTestBase):
    def test_unknown_action_fails(self) -> None:
        out = dispatch(self.ctx, {"action": "frobnicate", "post_id": None, "payload": {}})
        self.assertFalse(out.success)
        self.assertIn("unknown action", out.error)

    def test_cleanup_is_success_noop(self) -> None:
        out = dispatch(
            self.ctx,
            {
                "action": "cleanup",
                "post_id": "5",
                "payload": {"reason": "entry_state", "interrupted_task_id": 7},
            },
        )
        self.assertTrue(out.success)
        self.assertFalse(out.requeue)
        self.assertIn("cleanup", out.detail)
        self.assertEqual(self.runner.calls, [])

    def test_missing_entity_is_success_noop(self) -> None:
        out = dispatch(self.ctx, {"action": "handle_comment_added", "post_id": "999", "payload": {}})
        self.assertTrue(out.success)
        self.assertEqual(self.runner.calls, [])

    def test_implement_runs_agent_with_prompt(self) -> None:
        self.ctx.runner = FakeAgentRunner(AgentResult(
            ok=True, returncode=0,
            report={"status": "done", "summary": "did it", "files_changed": []},
        ))
        self.runner = self.ctx.runner
        eid = self._seed_local_entry("Do the thing", ["tier:issue", "status:todo"])
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("Route: impl.", self.runner.calls[0]["prompt"])  # impl skill bundle
        self.assertIn("Implement this work issue", self.runner.calls[0]["prompt"])
        self.assertEqual(self.runner.calls[0]["cwd"], str(self.root))

    def test_review_runs_review_prompt(self) -> None:
        self.ctx.runner = FakeAgentRunner(AgentResult(
            ok=True, returncode=0,
            report={"verdict": "approve", "confidence": 0.99, "summary": "ok"},
        ))
        self.runner = self.ctx.runner
        eid = self._seed_local_entry("Review me", ["tier:issue", "status:in_review"])
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_comment_added", "post_id": str(eid), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("Route: review.", self.runner.calls[0]["prompt"])  # review skill bundle
        self.assertIn("Review the completed work", self.runner.calls[0]["prompt"])

    def test_ask_label_runs_ask_prompt(self) -> None:
        self.ctx.runner = FakeAgentRunner(AgentResult(
            ok=True, returncode=0,
            report={"status": "answered", "answer": "the spec says X"},
        ))
        self.runner = self.ctx.runner
        eid = self._seed_local_entry("How does X work?", ["ask"])
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_comment_added", "post_id": str(eid), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("Route: ask.", self.runner.calls[0]["prompt"])  # ask skill bundle
        self.assertIn("READ-ONLY", self.runner.calls[0]["prompt"])

    def test_spec_change_label_runs_spec_change_prompt(self) -> None:
        eid = self._seed_local_entry("Adopt request", ["spec-change:adopt"])
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "spec-change:adopt"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        # `spec-change:adopt` label -> spec route + adopt subroute bundle
        self.assertIn("Route: spec. Subroute: adopt.", self.runner.calls[0]["prompt"])
        self.assertIn("===== SKILL.md =====", self.runner.calls[0]["prompt"])
        # Skill docs come from the engine (absolute path), not the target's specseed dir.
        from specseed_runtime.storage_paths import default_specseed_dir
        skill_dir = str(default_specseed_dir() / "skills" / "specseed")
        self.assertIn(f"Skill root dir: {skill_dir}", self.runner.calls[0]["prompt"])
        # staging + plan.json live under the data root's spec-change dir (absolute path)
        self.assertIn(str(self.root / "storage" / "spec-change"), self.runner.calls[0]["prompt"])

    def test_inject_label_runs_inject_route_prompt(self) -> None:
        eid = self._seed_local_entry("Manual hotfix", ["spec-change:inject"])
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "spec-change:inject"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("spec_subroutes/inject.md", self.runner.calls[0]["prompt"])
        self.assertIn("spec 'inject' subroute", self.runner.calls[0]["prompt"])

    def test_draft_removed_runs_spec_change_prompt(self) -> None:
        eid = self._seed_local_entry("Adapt request", ["spec-change:adapt"])
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_label_removed", "post_id": str(eid), "payload": {"label": "draft"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("adapt", self.runner.calls[0]["prompt"])

    def test_parked_request_wake_comment_reruns_worker(self) -> None:
        # A request parked awaiting_approval is woken ONLY by a comment (the
        # human's answer). That wake must reach the spec-change worker, not die
        # in the generic approval block: the request's spec-change:status label
        # also reads as entity status "awaiting_approval" via the :status: infix.
        eid = self._seed_local_entry(
            "Adapt request",
            ["spec-change:adapt", "spec-change:status:awaiting_approval"],
        )
        self.local.add_entry_comment(eid, "Answer: keep it simple.")
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_comment_added", "post_id": str(eid), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("spec 'adapt' subroute", self.runner.calls[0]["prompt"])

    def test_parked_request_label_churn_does_not_rerun_worker(self) -> None:
        eid = self._seed_local_entry(
            "Adapt request",
            ["spec-change:adapt", "spec-change:status:awaiting_approval"],
        )
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "question"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(self.runner.calls, [])

    def test_spec_change_skipped_when_plan_apply_already_queued(self) -> None:
        # A prior run already enqueued a plan apply; a second trigger for the same
        # request must not re-run the (expensive) worker.
        from specseed_runtime.scheduling.spec_change import enqueue_spec_change_plan

        eid = self._seed_local_entry("Adapt request", ["spec-change:adapt"])
        enqueue_spec_change_plan(request_id=str(eid), route="adapt", db=self.db)

        out = dispatch(
            self.ctx,
            {"action": "handle_label_removed", "post_id": str(eid), "payload": {"label": "draft"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(self.runner.calls, [])
        self.assertIn("already queued", out.detail)

    def test_comment_only_is_none_noop(self) -> None:
        # An in_progress issue receives a comment -> no actionable intent.
        eid = self._seed_local_entry("Working", ["tier:issue", "status:in_progress"])
        out = dispatch(
            self.ctx,
            {"action": "handle_comment_added", "post_id": str(eid), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertEqual(self.runner.calls, [])
        self.assertIn("no actionable intent", out.detail)

    def test_implement_missing_report_is_retryable_failure(self) -> None:
        # rc==0 but no result file -> not really done -> retryable failure (this is
        # the "Blocked: cannot make changes", exit 0 case).
        self.ctx.runner = FakeAgentRunner(AgentResult(ok=True, returncode=0, report=None,
                                                      report_error="no result file"))
        eid = self._seed_local_entry("Do it", ["tier:issue", "status:todo"])
        out = self._dispatch_then_work(self.ctx, {"action": "handle_entry_created", "post_id": str(eid), "payload": {}})
        self.assertFalse(out.success)
        self.assertTrue(out.retryable)
        self.assertIn("result file", out.error)

    def test_quota_exhausted_requeues_as_quota(self) -> None:
        # Every spec quota-blocked -> requeue + quota flag (no complete-as-failed,
        # so recovery never spawns an error post / resolver).
        self.ctx.runner = FakeAgentRunner(AgentResult(
            ok=False, returncode=1, error="usage limit", quota_exhausted=True, quota_reset_hint=None,
        ))
        eid = self._seed_local_entry("Do it", ["tier:issue", "status:todo"])
        out = self._dispatch_then_work(self.ctx, {"action": "handle_entry_created", "post_id": str(eid), "payload": {}})
        self.assertFalse(out.success)
        self.assertTrue(out.requeue)
        self.assertTrue(out.quota)

    def test_agent_timeout_requeues(self) -> None:
        self.ctx.runner = FakeAgentRunner(
            result=AgentResult(ok=False, timed_out=True, error="timed out")
        )
        eid = self._seed_local_entry("Slow", ["tier:issue", "status:todo"])
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_entry_created", "post_id": str(eid), "payload": {}},
        )
        self.assertFalse(out.success)
        self.assertTrue(out.requeue)

    def test_agent_hard_failure_no_requeue(self) -> None:
        self.ctx.runner = FakeAgentRunner(
            result=AgentResult(ok=False, returncode=1, error="agent exited with code 1")
        )
        eid = self._seed_local_entry("Boom", ["tier:issue", "status:todo"])
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_entry_created", "post_id": str(eid), "payload": {}},
        )
        self.assertFalse(out.success)
        self.assertFalse(out.requeue)
        self.assertIsNotNone(out.error)

    def test_agent_hard_failure_error_includes_stdout_tail(self) -> None:
        self.ctx.runner = FakeAgentRunner(
            result=AgentResult(
                ok=False,
                returncode=1,
                error="agent exited with code 1",
                stdout="Not logged in - Please run /login\n",
            )
        )
        eid = self._seed_local_entry("Boom", ["tier:issue", "status:todo"])
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_entry_created", "post_id": str(eid), "payload": {}},
        )
        self.assertFalse(out.success)
        self.assertIn("agent exited with code 1", out.error)
        self.assertIn("Not logged in", out.error)


class SpecChangeGateDecisionTest(DispatchTestBase):
    """Runtime owns the gate, derived in code from the finished run's OUTPUT.

    The agent no longer enqueues. After a spec-change run the runtime reads
    plan.json + the staged spec and decides propose (gated) vs direct (clarification).
    """

    def _spec_dir(self, rid):
        from specseed_runtime.scheduling.spec_change import spec_change_dir
        d = spec_change_dir(str(rid), self.ctx.storage)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_plan(self, rid, plan):
        d = self._spec_dir(rid)
        plan.setdefault("request_id", str(rid))
        (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    def _stage_spec(self, rid, rel="sad.md"):
        from specseed_runtime.scheduling.spec_change import spec_change_spec_dir
        dest = spec_change_spec_dir(str(rid), self.ctx.storage) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("# staged\n", encoding="utf-8")

    def _run(self, rid):
        # The spec-change follow-up (propose vs direct apply) is decided in
        # process_work_result AFTER the spec worker runs on the work lane. Dispatch
        # the control event, run the scheduled spec work_run, then the
        # process_work_result it enqueues - whose outcome carries the follow-up
        # detail. Stop there (do NOT run the enqueued plan apply).
        from specseed_runtime.db.database import LANE_CONTROL, LANE_WORK

        self.ctx.runner = FakeAgentRunner(AgentResult(ok=True, returncode=0))
        self.runner = self.ctx.runner
        dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(rid),
             "payload": {"label": "spec-change:adapt"}},
        )
        work = self.db.claim_next(LANE_WORK)
        self.assertIsNotNone(work, "a spec-change work_run should have been scheduled")
        dispatch(self.ctx, work)
        self.db.complete(work["task_id"], True)
        result_task = self.db.claim_next(LANE_CONTROL)
        self.assertIsNotNone(result_task, "the work run should enqueue process_work_result")
        return dispatch(self.ctx, result_task)

    def _actions(self, rid):
        return [t["action"] for t in self.db.tasks_for(rid)]

    def test_classify_propose_on_creates(self) -> None:
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {"creates": [{"title": "FEAT-0001"}]})
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")

    def test_classify_rejects_bare_dependency_refs(self) -> None:
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {
            "creates": [{"title": "FEAT-0002", "body": "Depends on: {id:FEAT-0001}"}],
        })
        with self.assertRaises(ValueError):
            dispatch_mod._classify_spec_change(self.ctx, str(rid))

    def test_classify_accepts_hash_dependency_placeholders(self) -> None:
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {
            "creates": [{"title": "FEAT-0002", "body": "Depends on: #{id:FEAT-0001}"}],
        })
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")

    def test_classify_propose_on_staged_spec(self) -> None:
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {})  # no work keys at all
        self._stage_spec(rid)
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")

    def test_classify_propose_on_settle_docs(self) -> None:
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {"settle_docs": ["spec/sad.md"]})
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")

    def test_classify_propose_when_touching_other_post(self) -> None:
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {"comments": [{"post": 999, "body": "hi"}]})
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")

    def test_classify_direct_for_clarification_on_request(self) -> None:
        # Only touches the request post: a clarifying comment + flipping its own status.
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {
            "comments": [{"post": str(rid), "body": "Q1?"}],
            "labels": [{"post": str(rid), "add": ["spec-change:status:awaiting_input"]}],
        })
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "direct")

    def test_classify_none_without_plan(self) -> None:
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "none")

    def test_classify_survives_non_object_plan(self) -> None:
        # A weaker model can write a bare JSON scalar/list as plan.json. It must read
        # as "no plan", never crash with AttributeError("'str' object ... 'get'").
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        d = self._spec_dir(rid)
        (d / "plan.json").write_text(json.dumps("just a string"), encoding="utf-8")
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "none")

    def test_classify_survives_string_entries_in_lists(self) -> None:
        # creates/comments holding bare strings (not objects) must not crash.
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {"creates": ["make a feature"]})
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")

    def test_classify_non_list_section_gates_as_propose(self) -> None:
        """A section that is not even a list cannot be proven request-scoped."""
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {"comments": "oops"})
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")

    def test_classify_string_comment_gates_as_propose(self) -> None:
        # A non-object comment can't be proven to target the request -> gate it.
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {"comments": ["a clarifying question"]})
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")

    def test_work_run_enqueues_proposal_not_apply(self) -> None:
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {"creates": [{"title": "FEAT-0001"}]})
        out = self._run(rid)
        self.assertTrue(out.success)
        self.assertIn("proposal enqueued", out.detail)
        actions = self._actions(rid)
        self.assertIn("propose_spec_change", actions)

    def test_pre_approval_bar_no_work_posts_no_live_spec_write(self) -> None:
        """The whole point of TKT-1, driven end to end: a spec-change run that plans
        real work must reach the human having created NOTHING.

        Runs the scheduler path AND the proposal task it enqueues, then asserts the
        three things approval is supposed to be the only gate for: live ``spec/`` is
        untouched, no work post exists, and the request is parked awaiting_approval.
        """
        from specseed_runtime import storage_paths
        from specseed_runtime.db.database import LANE_CONTROL

        live_spec = storage_paths.spec_dir(self.ctx.storage)
        live_spec.mkdir(parents=True, exist_ok=True)
        (live_spec / "sad.md").write_text("# live, untouched\n", encoding="utf-8")

        # propose reads the REMOTE post; _run's load_entity reads the local mirror.
        # Seed both so the request exists on either side under one id.
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self.remote.create_label("spec-change:adapt")
        self.assertEqual(
            self.remote.add_entry("Adapt", labels=["spec-change:adapt"]).data.id, rid
        )
        self._write_plan(rid, {
            "creates": [{"title": "FEAT-0001", "body": "new work"}],
            "plan_summary": "adds one feature",
            "apr": {"id": "APR-0001", "summary": "approve to create FEAT-0001"},
        })
        self._stage_spec(rid)
        before = {e.title for e in self.remote.list_entries().data}

        self.assertIn("proposal enqueued", self._run(rid).detail)
        propose = self.db.claim_next(LANE_CONTROL)
        self.assertIsNotNone(propose, "the gate should enqueue propose_spec_change")
        self.assertEqual(propose["action"], "propose_spec_change")
        _o = dispatch(self.ctx, propose)
        self.assertTrue(_o.success, _o.error)

        self.assertEqual((live_spec / "sad.md").read_text(encoding="utf-8"), "# live, untouched\n")
        self.assertEqual({e.title for e in self.remote.list_entries().data}, before)
        labels = [str(getattr(l, "name", l)) for l in self.remote.get_entry(rid).data.labels]
        self.assertIn("spec-change:status:awaiting_approval", labels)

    def test_clarification_run_enqueues_direct_apply(self) -> None:
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {
            "comments": [{"post": str(rid), "body": "Q1?"}],
            "labels": [{"post": str(rid), "add": ["spec-change:status:awaiting_input"]}],
        })
        out = self._run(rid)
        self.assertTrue(out.success)
        self.assertIn("plan apply", out.detail)
        actions = self._actions(rid)
        self.assertIn("apply_spec_change_plan", actions)
        self.assertNotIn("propose_spec_change", actions)

    def test_classify_clarification_status_request_scoped_is_direct(self) -> None:
        # A clarification round that declares its intent AND stays request-scoped is
        # direct - the questions reach the human without a gate.
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {
            "status": "clarification_round",
            "comments": [{"post": str(rid), "body": "Q1?"}],
            "labels": [{"post": str(rid), "add": ["spec-change:status:awaiting_input"]}],
        })
        with unittest.mock.patch.object(dispatch_mod.platform_log, "log_event") as log:
            self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "direct")
        events = [c.args[0] for c in log.call_args_list]
        self.assertNotIn("spec_change_clarification_carries_work", events)

    def test_classify_clarification_with_leftover_creates_gates_and_logs(self) -> None:
        # The regression: a re-run that asks a question but copies the prior
        # proposal's creates forward. The leftover work masks the clarification, so
        # the runtime gates (never creates unapproved work) AND logs loud so the
        # dropped questions are not silent.
        rid = self._seed_local_entry("Adapt", ["spec-change:adapt"])
        self._write_plan(rid, {
            "status": "clarification_round",
            "comments": [{"post": str(rid), "body": "Q1?"}],
            "creates": [{"title": "FEAT-0001"}],
        })
        with unittest.mock.patch.object(dispatch_mod.platform_log, "log_event") as log:
            self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")
        events = [c.args[0] for c in log.call_args_list]
        self.assertIn("spec_change_clarification_carries_work", events)


class ProposeSpecChangeIdempotencyTest(DispatchTestBase):
    """propose_spec_change posts plan_summary + APR once, then no-ops on re-trigger.

    The no-op must NOT be silent when the re-run plan also carries comments: the
    propose path never posts plan.comments, so a misrouted clarification would
    vanish. That case logs a distinct dropped-comments event.
    """

    def _seed_remote_request(self):
        for label in ("spec-change:adapt", "spec-change:status:awaiting_approval"):
            self.remote.create_label(label)
        return self.remote.add_entry("Adapt", labels=["spec-change:adapt"]).data.id

    def _write_plan(self, rid, plan):
        from specseed_runtime.scheduling.spec_change import spec_change_dir
        d = spec_change_dir(str(rid), self.ctx.storage)
        d.mkdir(parents=True, exist_ok=True)
        plan.setdefault("request_id", str(rid))
        plan.setdefault("apr", {"id": "APR-0001", "summary": "do the thing"})
        plan.setdefault("plan_summary", "## Plan\n- thing")
        (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    def test_first_propose_posts_then_bare_retrigger_is_quiet_noop(self) -> None:
        rid = self._seed_remote_request()
        self._write_plan(rid, {"creates": [{"title": "FEAT-0001"}]})
        first = dispatch_mod.propose_spec_change(self.ctx, {"post_id": str(rid), "task_id": 1})
        self.assertTrue(first.success)
        # Re-trigger with the SAME (no-comments) plan: benign retry -> plain no-op.
        with unittest.mock.patch.object(dispatch_mod.platform_log, "log_event") as log:
            again = dispatch_mod.propose_spec_change(self.ctx, {"post_id": str(rid), "task_id": 2})
        self.assertTrue(again.success)
        events = [c.args[0] for c in log.call_args_list]
        self.assertIn("spec_change_propose_noop", events)
        self.assertNotIn("spec_change_propose_noop_dropped_comments", events)

    def test_retrigger_with_comments_logs_dropped(self) -> None:
        rid = self._seed_remote_request()
        self._write_plan(rid, {"creates": [{"title": "FEAT-0001"}]})
        dispatch_mod.propose_spec_change(self.ctx, {"post_id": str(rid), "task_id": 1})
        # Now a re-run leaves a question in the plan but still carries creates, so it
        # lands on propose again. The APR is already posted -> idempotent no-op, but
        # the question would be eaten: that must be logged loud.
        self._write_plan(rid, {
            "creates": [{"title": "FEAT-0001"}],
            "comments": [{"post": str(rid), "body": "Q1?"}],
        })
        with unittest.mock.patch.object(dispatch_mod.platform_log, "log_event") as log:
            out = dispatch_mod.propose_spec_change(self.ctx, {"post_id": str(rid), "task_id": 2})
        self.assertTrue(out.success)
        events = [c.args[0] for c in log.call_args_list]
        self.assertIn("spec_change_propose_noop_dropped_comments", events)


class RunAgentRoutingTest(DispatchTestBase):
    """``_run_agent`` picks the chain for the intent's function, and works with
    both a per-function RunnerChains and a bare runner."""

    def _ctx_with(self, runner):
        self.ctx.runner = runner
        return self.ctx

    def test_intent_maps_to_runner_function(self) -> None:
        self.assertEqual(dispatch_mod._INTENT_FUNCTION[AgentIntent.SPEC_CHANGE], "spec")
        self.assertEqual(dispatch_mod._INTENT_FUNCTION[AgentIntent.IMPLEMENT], "implementation")
        self.assertEqual(dispatch_mod._INTENT_FUNCTION[AgentIntent.REVIEW], "review")

    def test_chains_run_uses_function_chain(self) -> None:
        spec_runner = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        impl_runner = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        chains = RunnerChains({"spec": [spec_runner], "implementation": [impl_runner]})
        ctx = self._ctx_with(chains)
        dispatch_mod._run_agent(ctx, "do it", AgentIntent.IMPLEMENT)
        self.assertEqual(len(impl_runner.calls), 1)
        self.assertEqual(len(spec_runner.calls), 0)

    def test_chains_fallback_inside_dispatch_helper(self) -> None:
        primary = FakeAgentRunner(result=AgentResult(ok=False, returncode=1))
        fallback = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        chains = RunnerChains({"implementation": [primary, fallback]})
        ctx = self._ctx_with(chains)
        result = dispatch_mod._run_agent(ctx, "do it", AgentIntent.IMPLEMENT)
        self.assertTrue(result.ok)
        self.assertEqual(len(fallback.calls), 1)

    def test_bare_runner_still_supported(self) -> None:
        bare = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        ctx = self._ctx_with(bare)
        result = dispatch_mod._run_agent(ctx, "do it", AgentIntent.REVIEW)
        self.assertTrue(result.ok)
        self.assertEqual(len(bare.calls), 1)
        # a bare runner gets no function kwarg
        self.assertNotIn("function", bare.calls[0])


class PlatformErrorDispatchTest(DispatchTestBase):
    """handle_platform_error routing + the platform_error-post work shield."""

    def _error_post(self, *, body_marker="<!-- specseed:platform-error task=9 -->"):
        self.remote.create_label("platform_error")
        return self.remote.add_entry(
            "Platform error: handle_label_removed (post 5)",
            body="stub\n\n" + body_marker,
            labels=["platform_error"],
        ).data.id

    def test_handle_platform_error_runs_resolve_chain(self) -> None:
        post_id = self._error_post()
        out = dispatch(
            self.ctx,
            {
                "action": "handle_platform_error",
                "task_id": 42,
                "post_id": str(post_id),
                "payload": {"origin_task_id": 9, "origin_action": "handle_label_removed",
                            "origin_post_id": "5", "reason": "new"},
            },
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        prompt = self.runner.calls[0]["prompt"]
        self.assertIn("platform-error resolver", prompt)
        self.assertIn("specseed:platform-error task=9", prompt)  # body in prompt
        self.assertIn("PLATFORM_ERROR_REPORTED", prompt)

    def test_resolve_function_chain_is_used(self) -> None:
        post_id = self._error_post()
        resolve = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        other = FakeAgentRunner(result=AgentResult(ok=True, returncode=0))
        self.ctx.runner = RunnerChains(
            {"resolve_platform_errors": [resolve], "implementation": [other]}
        )
        out = dispatch(
            self.ctx,
            {"action": "handle_platform_error", "task_id": 1,
             "post_id": str(post_id), "payload": {"reason": "exhausted"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(resolve.calls), 1)
        self.assertEqual(len(other.calls), 0)

    def test_closed_post_is_cancelled_no_agent(self) -> None:
        post_id = self._error_post()
        self.remote.set_entry_closed(post_id)
        out = dispatch(
            self.ctx,
            {"action": "handle_platform_error", "task_id": 1,
             "post_id": str(post_id), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertIn("cancelled", out.detail)
        self.assertEqual(self.runner.calls, [])

    def test_resolve_agent_failure_is_not_retryable(self) -> None:
        post_id = self._error_post()
        self.ctx.runner = FakeAgentRunner(
            result=AgentResult(ok=False, returncode=1, error="agent exited with code 1")
        )
        out = dispatch(
            self.ctx,
            {"action": "handle_platform_error", "task_id": 1,
             "post_id": str(post_id), "payload": {}},
        )
        self.assertFalse(out.success)
        self.assertFalse(out.retryable)

    def test_human_comment_on_error_post_reengages_agent(self) -> None:
        # The post must exist on the remote (read fresh) AND the local mirror
        # (load_entity reads local).
        post_id = self._error_post()
        eid = self._seed_local_entry(
            "Platform error: handle_label_removed (post 5)", ["platform_error"]
        )
        self.assertEqual(str(eid), str(post_id))  # same id space, fresh dbs
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_comment_added", "task_id": 2,
             "post_id": str(eid), "payload": {"comment_id": 1, "author": "alice"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("The human replied", self.runner.calls[0]["prompt"])

    def test_other_events_on_error_post_are_bookkeeping(self) -> None:
        post_id = self._error_post()
        self._seed_local_entry(
            "Platform error: handle_label_removed (post 5)", ["platform_error"]
        )
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "task_id": 3,
             "post_id": str(post_id), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertIn("bookkeeping", out.detail)
        self.assertEqual(self.runner.calls, [])


class RetryGateTest(DispatchTestBase):
    """A queued retry whose error post was closed must not execute."""

    def _retried_task(self, action: str = "handle_label_removed") -> dict:
        task_id = self.db.enqueue(action, post_id="5", payload={})
        task = self.db.claim_next()
        return dict(task) | {"attempts": 2}

    def _error_post_for(self, task: dict, closed: bool) -> None:
        from specseed_runtime.executing import recovery

        self.remote.create_label(recovery.PLATFORM_ERROR_LABEL)
        created = self.remote.add_entry(
            recovery.error_post_title(task),
            body=recovery._marker_line(task["task_id"]),
            labels=[recovery.PLATFORM_ERROR_LABEL],
        )
        self.assertTrue(created.ok)
        if closed:
            self.assertTrue(self.remote.set_entry_closed(created.data.id).ok)

    def test_closed_error_post_cancels_queued_retry(self) -> None:
        task = self._retried_task()
        self._error_post_for(task, closed=True)
        out = dispatch(self.ctx, task)
        self.assertTrue(out.success)
        self.assertIn("retry cancelled", out.detail)
        self.assertEqual(self.runner.calls, [])  # handler never ran

    def test_open_error_post_lets_retry_run(self) -> None:
        task = self._retried_task(action="apply_spec_change_plan")
        self._error_post_for(task, closed=False)
        out = dispatch(self.ctx, task)
        # gate passed; the handler itself then rejects the empty payload
        self.assertFalse(out.success)
        self.assertIn("plan not readable", out.error)


class ApplySpecChangePlanTest(DispatchTestBase):
    def _plan_dir(self, rid, plan):
        from specseed_runtime.scheduling.spec_change import spec_change_dir
        d = spec_change_dir(str(rid), self.ctx.storage)
        d.mkdir(parents=True, exist_ok=True)
        plan.setdefault("request_id", str(rid))
        (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        return d

    def _task(self, rid, close_request=True):
        return {
            "task_id": 1,
            "action": "apply_spec_change_plan",
            "post_id": str(rid),
            "payload": {"request_id": str(rid), "route": "adapt", "close_request": close_request},
        }

    def test_finalizing_plan_creates_posts_and_closes_request(self) -> None:
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        self._plan_dir(rid, {
            "creates": [
                {"title": "EPIC-0001", "body": "root", "labels": ["epic"]},
                {"title": "FEAT-0001", "body": "Epic: #{id:EPIC-0001}", "labels": ["issue"]},
            ],
            "comments": [{"post": str(rid), "body": "created"}],
        })
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, True))
        self.assertTrue(out.success)
        entries = self.remote.list_entries().data
        titles = {e.title for e in entries}
        self.assertIn("EPIC-0001", titles)
        feat = next(e for e in entries if e.title == "FEAT-0001")
        self.assertIn("Epic: #", self.remote.get_entry(feat.id).data.body)
        self.assertFalse(self.remote.get_entry(rid).data.is_open)

    def test_plan_retry_skips_created_and_comment_ledgers(self) -> None:
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        self._plan_dir(rid, {
            "creates": [{"title": "FEAT-0001", "labels": ["issue"]}],
            "comments": [{"post_id": str(rid), "body": "once"}],
        })
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, True))
        self.assertTrue(out.success)
        again = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, True))
        self.assertTrue(again.success)
        titles = [e.title for e in self.remote.list_entries().data]
        self.assertEqual(titles.count("FEAT-0001"), 1)
        comments = self.remote.get_entry(rid).data.comments
        self.assertEqual(sum(1 for c in comments if "once" in c.body), 1)

    def test_direct_plan_rejects_non_request_mutation(self) -> None:
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        other = self.remote.add_entry("other", labels=[]).data.id
        self._plan_dir(rid, {"comments": [{"post": str(other), "body": "no"}]})
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, False))
        self.assertFalse(out.success)
        self.assertIn("only touch the request", out.error)

    def test_direct_plan_rejects_id_map_redirect(self) -> None:
        """The scope bar is the id the remote is CALLED with, not the id in the plan.

        A plan may name the request post and then map it elsewhere through
        ``id_map``; comparing only the written id would call that request-scoped and
        let an unapproved run comment on someone else's post.
        """
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        other = self.remote.add_entry("other", labels=[]).data.id
        self._plan_dir(rid, {
            "id_map": {str(rid): str(other)},
            "comments": [{"post": str(rid), "body": "redirected"}],
        })
        self.assertEqual(dispatch_mod._classify_spec_change(self.ctx, str(rid)), "propose")
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, False))
        self.assertFalse(out.success)
        self.assertIn("only touch the request", out.error)
        self.assertEqual(self.remote.get_entry(other).data.comments, [])

    def test_direct_plan_rejects_id_map_redirect_on_labels(self) -> None:
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        other = self.remote.add_entry("other", labels=["keep"]).data.id
        self._plan_dir(rid, {
            "id_map": {str(rid): str(other)},
            "labels": [{"post": str(rid), "remove": ["keep"], "add": ["spec-change:status:x"]}],
        })
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, False))
        self.assertFalse(out.success)
        self.assertIn("keep", [str(getattr(l, "name", l)) for l in self.remote.get_entry(other).data.labels])

    def test_scope_refusal_is_not_retryable(self) -> None:
        """A plan cannot re-read differently, so retrying a refusal only burns attempts."""
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        other = self.remote.add_entry("other", labels=[]).data.id
        self._plan_dir(rid, {"comments": [{"post": str(other), "body": "no"}]})
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, False))
        self.assertFalse(out.success)
        self.assertFalse(out.retryable)

    def test_malformed_plan_is_not_retryable(self) -> None:
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        self._plan_dir(rid, {"creates": "not-a-list"})
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, True))
        self.assertFalse(out.success)
        self.assertFalse(out.retryable)

    def test_bad_dependency_link_fails_the_handler_not_the_worker(self) -> None:
        """Dep validation must come back as an outcome; escaping the handler as a bare
        exception loses the task instead of routing it to recovery."""
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        self._plan_dir(rid, {"creates": [{"title": "X", "body": "Depends on: FEAT-1"}]})
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, True))
        self.assertFalse(out.success)
        self.assertFalse(out.retryable)
        self.assertIn("dependency links", out.error)
        self.assertEqual([e.title for e in self.remote.list_entries().data].count("X"), 0)

    def test_remote_failure_stays_retryable(self) -> None:
        """The retryable split must not swallow genuinely transient remote failures."""
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        self._plan_dir(rid, {"closes": [str(rid)]})
        def _boom(*a, **k):
            return type("R", (), {"ok": False, "error": "network down", "data": None})()
        self.remote.set_entry_closed = _boom
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, True))
        self.assertFalse(out.success)
        self.assertTrue(out.retryable)

    def test_plan_retry_does_not_repeat_closes_and_deletes(self) -> None:
        """Closes and deletes are one-way: replaying them against an already-gone post
        fails the retry, so a plan that dies part-way could never finish."""
        rid = self.remote.add_entry("adapt request", labels=[]).data.id
        victim = self.remote.add_entry("victim", labels=[]).data.id
        doomed = self.remote.add_entry("doomed", labels=[]).data.id
        self._plan_dir(rid, {"closes": [str(doomed)], "deletes": [str(victim)]})
        out = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, True))
        self.assertTrue(out.success, out.error)
        again = dispatch_mod.apply_spec_change_plan(self.ctx, self._task(rid, True))
        self.assertTrue(again.success, again.error)
        self.assertIn("closes=0", again.detail)
        self.assertIn("deletes=0", again.detail)

import shutil
from specseed_runtime.executing import git_ops as _git_ops

_HAS_GIT = shutil.which("git") is not None


class AssignmentGateTest(DispatchTestBase):
    """An issue is only worked once the agent is among its assignees. The agent here
    is the approver "alice" (no distinct platform_username -> agent IS the human)."""

    def _set_auto_assign(self, on) -> None:
        self.config["permissions"] = {"platform": {"auto_assign_agent": on}}
        self.ctx.permissions = Permissions(self.config)

    def test_held_silently_when_unassigned_and_auto_assign_off(self) -> None:
        self._set_auto_assign(False)
        eid = self._seed_local_entry("Do it", ["tier:issue", "status:todo"], assignees=())
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertFalse(out.requeue)  # silent hold: a later assignee change wakes it
        self.assertIn("not assigned", out.detail)
        self.assertEqual(len(self.runner.calls), 0)  # agent never ran

    def test_assigned_to_agent_off_runs(self) -> None:
        # auto_assign off, but a human already assigned the agent -> work proceeds.
        self._set_auto_assign(False)
        self.ctx.runner = FakeAgentRunner(AgentResult(
            ok=True, returncode=0,
            report={"status": "done", "summary": "did it", "files_changed": []},
        ))
        self.runner = self.ctx.runner
        eid = self._seed_local_entry("Do it", ["tier:issue", "status:todo"], assignees=("alice",))
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)

    def test_auto_assign_on_assigns_agent_then_proceeds(self) -> None:
        self.ctx.runner = FakeAgentRunner(AgentResult(
            ok=True, returncode=0,
            report={"status": "done", "summary": "did it", "files_changed": []},
        ))
        self.runner = self.ctx.runner
        # auto_assign defaults on. Seed BOTH (matching ids) so the remote assignee
        # write lands on a real entry.
        for label in ("tier:issue", "status:todo"):
            self.local.create_label(label)
            self.remote.create_label(label)
        rid = self.remote.add_entry("Do it", labels=["tier:issue", "status:todo"]).data.id
        lid = self.local.add_entry("Do it", labels=["tier:issue", "status:todo"]).data.id
        self.assertEqual(str(rid), str(lid))
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(lid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)  # assigned -> agent ran
        self.assertIn("alice", self.remote.get_entry(rid).data.assignees)


class DependencyGateTest(DispatchTestBase):
    """An issue is held until the issues it depends on are done."""

    def _seed_with_body(self, title, labels, body, assignees=("alice",)):
        for label in labels:
            self.local.create_label(label)
        return self.local.add_entry(
            title, body=body, labels=labels, assignees=list(assignees)
        ).data.id

    def _seed_both(self, title, labels, body, assignees=("alice",)):
        """Seed local AND remote in lockstep so ids match - needed when a transition
        (e.g. block-on-cancelled-dep) mutates the remote entry by its (local) id."""
        for label in labels:
            self.local.create_label(label)
            self.remote.create_label(label)
        eid = self.local.add_entry(
            title, body=body, labels=labels, assignees=list(assignees)
        ).data.id
        self.remote.add_entry(title, body=body, labels=labels, assignees=list(assignees))
        return eid

    def _remote_labels(self, eid):
        return {lbl.name for lbl in self.remote.get_entry(eid).data.labels}

    def _adapt_drafts(self):
        out = []
        for s in self.remote.list_entries(is_open=None).data or []:
            names = {lbl.name for lbl in self.remote.get_entry(s.id).data.labels}
            if "spec-change:adapt" in names and "draft" in names:
                out.append(s.id)
        return out

    def test_held_while_dependency_not_done(self) -> None:
        dep = self._seed_with_body("Dep", ["tier:issue", "status:todo"], "")
        issue = self._seed_with_body(
            "FEAT-0002 Dependent", ["tier:issue", "status:todo"],
            "Depends on: #{0}".format(dep),
        )
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(issue), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.requeue)
        self.assertIn("held", out.detail)
        self.assertEqual(len(self.runner.calls), 0)  # agent never ran

    def test_runs_when_dependency_done(self) -> None:
        dep = self._seed_with_body("Dep", ["tier:issue", "status:done"], "")
        issue = self._seed_with_body(
            "FEAT-0002 Dependent", ["tier:issue", "status:todo"],
            "Depends on: #{0}".format(dep),
        )
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(issue), "payload": {"label": "status:todo"}},
        )
        self.assertFalse(out.requeue)
        self.assertEqual(len(self.runner.calls), 1)  # dep done -> agent ran

    def test_held_while_dependency_awaiting_merge(self) -> None:
        # awaiting_merge is NOT done (code not on primary yet) -> dependent stays held.
        dep = self._seed_with_body("Dep", ["tier:issue", "status:awaiting_merge"], "")
        issue = self._seed_with_body(
            "FEAT-0002 Dependent", ["tier:issue", "status:todo"],
            "Depends on: #{0}".format(dep),
        )
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(issue), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.requeue)
        self.assertIn("held", out.detail)
        self.assertEqual(len(self.runner.calls), 0)

    def test_blocked_and_drafts_adapt_when_dependency_cancelled(self) -> None:
        # A dep that ended wont_do will never land -> the dependent is BLOCKED (not held
        # forever) and one draft spec-adapt is opened for the human to triage.
        dep = self._seed_both("Dep", ["tier:issue", "status:wont_do"], "")
        issue = self._seed_both(
            "FEAT-0002 Dependent", ["tier:issue", "status:todo"],
            "Depends on: #{0}".format(dep),
        )
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(issue), "payload": {"label": "status:todo"}},
        )
        self.assertFalse(out.requeue)
        self.assertEqual(len(self.runner.calls), 0)  # agent never ran
        self.assertIn("blocked", out.detail)
        self.assertIn("issue:status:blocked", self._remote_labels(issue))
        self.assertEqual(len(self._adapt_drafts()), 1)

    def test_cancelled_dep_draft_is_idempotent(self) -> None:
        # Two dependents on the same cancelled dep -> still ONE draft adapt.
        dep = self._seed_both("Dep", ["tier:issue", "status:deprecated"], "")
        a = self._seed_both("A", ["tier:issue", "status:todo"], "Depends on: #{0}".format(dep))
        b = self._seed_both("B", ["tier:issue", "status:todo"], "Depends on: #{0}".format(dep))
        for issue in (a, b):
            dispatch(
                self.ctx,
                {"action": "handle_label_added", "post_id": str(issue), "payload": {"label": "status:todo"}},
            )
        self.assertEqual(len(self._adapt_drafts()), 1)

    def test_held_while_parent_ticket_dependency_not_done(self) -> None:
        # ticket-tier: issue under ticket B; B depends on ticket A (not done).
        dep_ticket = self._seed_with_body("Ticket A", ["tier:ticket", "status:todo"], "")
        parent = self._seed_with_body(
            "Ticket B", ["tier:ticket", "status:todo"], "Depends on: #{0}".format(dep_ticket)
        )
        issue = self._seed_with_body(
            "FEAT-0003 Under B", ["tier:issue", "status:todo"], "Ticket: #{0}".format(parent)
        )
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(issue), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.requeue)
        self.assertIn("held", out.detail)
        self.assertEqual(len(self.runner.calls), 0)

    def test_runs_when_parent_ticket_dependency_done(self) -> None:
        dep_ticket = self._seed_with_body("Ticket A", ["tier:ticket", "status:done"], "")
        parent = self._seed_with_body(
            "Ticket B", ["tier:ticket", "status:todo"], "Depends on: #{0}".format(dep_ticket)
        )
        issue = self._seed_with_body(
            "FEAT-0003 Under B", ["tier:issue", "status:todo"], "Ticket: #{0}".format(parent)
        )
        out = self._dispatch_then_work(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(issue), "payload": {"label": "status:todo"}},
        )
        self.assertFalse(out.requeue)
        self.assertEqual(len(self.runner.calls), 1)


class _FileWritingRunner(FakeAgentRunner):
    """Fake runner that writes a file into cwd (simulating an agent's edits) then
    returns the given result."""

    def __init__(self, result, filename, content="x\n"):
        super().__init__(result)
        self._filename = filename
        self._content = content

    def run(self, prompt, *, cwd, **kwargs):
        (Path(cwd) / self._filename).write_text(self._content, encoding="utf-8")
        return super().run(prompt, cwd=cwd, **kwargs)


@unittest.skipUnless(_HAS_GIT, "git not available")
class RuntimeGitLifecycleTest(DispatchTestBase):
    """C1: the runtime branches + commits an implement run, agent never runs git."""

    def setUp(self) -> None:
        super().setUp()
        self.config["specseed_primary_branch"] = "main"
        # Real specseed gitignores storage; here keep the test's sqlite dbs out of
        # git so checkouts don't fight open file handles.
        (self.root / ".gitignore").write_text("*.db\n*.db-*\nstorage/\n", encoding="utf-8")
        self._git("init")
        self._git("symbolic-ref", "HEAD", "refs/heads/main")
        # COMMIT the .gitignore (real specseed scaffold does) so it is stable on
        # main and inherited by every branch. With an --allow-empty root the
        # .gitignore is untracked, `git add -A` stages it onto a feature branch, and
        # `checkout main` then deletes it - after which `git add -A` starts staging
        # the (now un-ignored) sqlite dbs and a later checkout wipes them mid-run.
        self._git("add", ".gitignore")
        self._git("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-m", "root")

    def _git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.root),
                              capture_output=True, text=True)

    def _seed_both(self, title, labels):
        # The done-transition + merge reads the entity's status from the REMOTE, so
        # seed it there too (matching ids: both dbs are fresh + autoincrement).
        for label in labels:
            self.local.create_label(label)
            self.remote.create_label(label)
        # Default-assigned to the agent ("alice") so the implement gate passes.
        rid = self.remote.add_entry(title, labels=labels, assignees=["alice"]).data.id
        lid = self.local.add_entry(title, labels=labels, assignees=["alice"]).data.id
        self.assertEqual(str(rid), str(lid))
        return lid

    def test_implement_branches_commits_and_returns_to_primary(self) -> None:
        self.ctx.runner = _FileWritingRunner(
            AgentResult(ok=True, returncode=0,
                        report={"status": "done", "summary": "did it", "files_changed": ["new.py"]}),
            filename="new.py",
        )
        eid = self._seed_local_entry("FEAT-0001 Do the thing", ["tier:issue", "status:todo"])
        out = self._run_full(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        # returned to primary, and primary has no agent file
        self.assertEqual(_git_ops.current_branch(self.root), "main")
        self.assertFalse((self.root / "new.py").exists())
        # the issue branch exists and carries the committed file
        branch = "feat-0001-do-the-thing-{0}".format(eid)
        self.assertEqual(
            self._git("rev-parse", "--verify", f"refs/heads/{branch}").returncode, 0
        )
        self._git("checkout", branch)
        self.assertTrue((self.root / "new.py").exists())
        log = self._git("log", "-1", "--pretty=%s").stdout.strip()
        self.assertIn("FEAT-0001", log)

    def test_merge_disabled_parks_merge_gate_unmerged(self) -> None:
        # default config: merge_to_primary off -> the issue parks at a merge gate
        # (awaiting_approval) with the branch intact; nothing lands on primary until
        # a human approves the merge. An unmerged issue never reads as done.
        self.ctx.runner = _FileWritingRunner(
            AgentResult(ok=True, returncode=0,
                        report={"status": "done", "summary": "did it", "files_changed": ["x.py"]}),
            filename="x.py",
        )
        eid = self._seed_both("FEAT-0002 Thing", ["tier:issue", "status:todo"])
        out = self._run_full(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertFalse((self.root / "x.py").exists())  # not on primary
        # the merge gate opened on the work lane; the awaiting_approval label proves it
        # (the "merge gate" note now lands on the work-lane outcome, not this one).
        labels = {l.name for l in self.remote.get_entry(eid).data.labels}
        self.assertIn("issue:status:awaiting_approval", labels)
        # the work branch still exists, intact for the approved merge (or a manual one)
        self.assertEqual(
            self._git("rev-parse", "--verify", "refs/heads/feat-0002-thing-{0}".format(eid)).returncode, 0
        )

    def test_merge_enabled_merges_branch_into_primary(self) -> None:
        self.config["permissions"]["git"] = {"merge_to_primary": True}
        self.ctx.permissions = Permissions(self.config)
        self.ctx.runner = _FileWritingRunner(
            AgentResult(ok=True, returncode=0,
                        report={"status": "done", "summary": "did it", "files_changed": ["y.py"]}),
            filename="y.py",
        )
        eid = self._seed_both("FEAT-0003 Thing", ["tier:issue", "status:todo"])
        out = self._run_full(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(_git_ops.current_branch(self.root), "main")
        # primary now carries the merged file
        self.assertTrue((self.root / "y.py").exists())
        # the merge ran on the work lane; the "Merged branch" remote comment proves it
        # (the "merged" note now lands on the work-lane outcome, not this one).
        bodies = "\n".join(c.body for c in self.remote.get_entry(eid).data.comments)
        self.assertIn("Merged branch", bodies)

    # -- readiness (prepare) + invalidation sweep ----------------------------- #
    def _commit(self, *args) -> None:
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", *args)

    def _conflicting_branch(self, branch, file, base, branch_side, main_side) -> None:
        """A branch and primary that BOTH edit ``file`` -> primary->branch prepare conflicts."""
        (self.root / file).write_text(base, encoding="utf-8")
        self._git("add", "-A")
        self._commit("-m", "seed {0}".format(file))
        self._git("checkout", "-b", branch)
        (self.root / file).write_text(branch_side, encoding="utf-8")
        self._git("add", "-A")
        self._commit("-m", "branch edit")
        self._git("checkout", "main")
        (self.root / file).write_text(main_side, encoding="utf-8")
        self._git("add", "-A")
        self._commit("-m", "main edit")

    def _recording_resolver(self, writes=None):
        """A RunnerChains whose merge_conflicts runner records its prompts and may
        write a file (to simulate resolving the conflict)."""
        root = self.root

        class _Rec(FakeAgentRunner):
            def __init__(self) -> None:
                super().__init__(AgentResult(ok=True, returncode=0))
                self.prompts = []

            def run(self, prompt, *, cwd, **kwargs):
                self.prompts.append(prompt)
                if writes is not None:
                    (root / writes[0]).write_text(writes[1], encoding="utf-8")
                return AgentResult(ok=True, returncode=0)

        rec = _Rec()
        return rec, RunnerChains({"implementation": [rec], "merge_conflicts": [rec]})

    def test_prepare_conflict_unresolved_parks_blocked(self) -> None:
        from specseed_runtime.executing import advance
        from specseed_runtime.executing.context import load_entity

        eid = self._seed_both("FEAT-0005 Conflict", ["tier:issue", "status:awaiting_approval"])
        branch = "feat-0005-conflict"
        self._conflicting_branch(branch, "base.txt", "base\n", "branch\n", "main\n")
        rec, chains = self._recording_resolver(writes=None)  # resolver leaves the markers
        self.ctx.runner = chains
        entity, _ = load_entity(self.ctx, str(eid))
        note = dispatch_mod._prepare_and_gate(
            self.ctx, entity, advance.WorkTransition("x", prepare=True), branch, {"task_id": None}
        )
        self.assertIn("parked blocked", note)
        self.assertIn("issue:status:blocked", {l.name for l in self.remote.get_entry(eid).data.labels})
        self.assertEqual(_git_ops.current_branch(self.root), "main")
        # the merge-conflicts chain (not implement) handled it
        self.assertTrue(any("MERGE CONFLICTS" in p for p in rec.prompts))
        self.assertEqual(self._git("rev-parse", "--verify", "refs/heads/" + branch).returncode, 0)

    def test_prepare_conflict_resolved_opens_gate(self) -> None:
        from specseed_runtime.executing import advance
        from specseed_runtime.executing.context import load_entity

        # in_review is the realistic pre-gate status (a finished review readies the merge).
        eid = self._seed_both("FEAT-0005 Conflict", ["tier:issue", "status:in_review"])
        branch = "feat-0005-conflict"
        self._conflicting_branch(branch, "base.txt", "base\n", "branch\n", "main\n")
        rec, chains = self._recording_resolver(writes=("base.txt", "resolved\n"))
        self.ctx.runner = chains
        entity, _ = load_entity(self.ctx, str(eid))
        note = dispatch_mod._prepare_and_gate(
            self.ctx, entity, advance.WorkTransition("x", prepare=True), branch, {"task_id": None}
        )
        self.assertIn("merge gate", note)
        labels = {l.name for l in self.remote.get_entry(eid).data.labels}
        self.assertIn("issue:status:awaiting_approval", labels)
        bodies = "\n".join(c.body for c in self.remote.get_entry(eid).data.comments)
        self.assertIn(advance.MERGE_GATE_MARKER, bodies)
        self.assertEqual(_git_ops.current_branch(self.root), "main")

    # -- Cat 4: branch isolation + base selection ----------------------------- #
    def test_each_issue_gets_own_branch_cut_from_primary_not_sibling(self) -> None:
        # Regression for the FEAT-0002-on-FEAT-0001-branch failure: two issues run
        # back-to-back must each land on their OWN branch, each freshly cut from
        # primary - NEVER reusing a sibling's branch. ensure_on_branch creates a new
        # branch off primary explicitly, so even though the repo was last left on
        # main, FEAT-0002's branch must not carry FEAT-0001's file.
        self.ctx.runner = _FileWritingRunner(
            AgentResult(ok=True, returncode=0,
                        report={"status": "done", "summary": "a", "files_changed": ["a.py"]}),
            filename="a.py",
        )
        first = self._seed_both("FEAT-0001 First", ["tier:issue", "status:todo"])
        self._run_full(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(first), "payload": {"label": "status:todo"}},
        )
        self.ctx.runner = _FileWritingRunner(
            AgentResult(ok=True, returncode=0,
                        report={"status": "done", "summary": "b", "files_changed": ["b.py"]}),
            filename="b.py",
        )
        second = self._seed_both("FEAT-0002 Second", ["tier:issue", "status:todo"])
        self._run_full(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(second), "payload": {"label": "status:todo"}},
        )
        # two distinct branches exist (post id folded on the end)
        first_branch = "feat-0001-first-{0}".format(first)
        second_branch = "feat-0002-second-{0}".format(second)
        self.assertEqual(self._git("rev-parse", "--verify", "refs/heads/" + first_branch).returncode, 0)
        self.assertEqual(self._git("rev-parse", "--verify", "refs/heads/" + second_branch).returncode, 0)
        # FEAT-0001's branch carries only its own file
        self._git("checkout", first_branch)
        self.assertTrue((self.root / "a.py").exists())
        self.assertFalse((self.root / "b.py").exists())
        # FEAT-0002 was cut from primary, NOT from the sibling: it has b.py and NOT a.py
        self._git("checkout", second_branch)
        self.assertTrue((self.root / "b.py").exists())
        self.assertFalse((self.root / "a.py").exists())

    def test_dependent_branch_cut_from_primary_carries_merged_dep_code(self) -> None:
        # Regression for "FEAT-0001 rooted at empty init, re-created the scaffold":
        # once a dependency is merged to primary, the dependent's branch (cut from
        # the up-to-date primary AFTER the dep gate passes) must already carry the
        # dep's code - so the agent builds ON it instead of recreating it.
        # Simulate the dep merged: its file is on primary, its status is done.
        (self.root / "scaffold.py").write_text("# scaffold from CHORE-0001\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "merge CHORE-0001")
        for label in ("tier:issue", "status:done", "status:todo"):
            self.local.create_label(label)
        dep = self.local.add_entry("CHORE-0001 Scaffold", labels=["tier:issue", "status:done"]).data.id
        feat = self.local.add_entry(
            "FEAT-0001 Build on scaffold",
            body="Depends on: #{0}".format(dep),
            labels=["tier:issue", "status:todo"],
            assignees=["alice"],  # assigned to the agent so the implement gate passes
        ).data.id
        self.ctx.runner = _FileWritingRunner(
            AgentResult(ok=True, returncode=0,
                        report={"status": "done", "summary": "f", "files_changed": ["feat.py"]}),
            filename="feat.py",
        )
        out = self._run_full(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(feat), "payload": {"label": "status:todo"}},
        )
        # dep done -> gate passed, agent ran (not held)
        self.assertFalse(out.requeue)
        # the dependent's branch carries the dep's merged scaffold AND the new work
        self._git("checkout", "feat-0001-build-on-scaffold-{0}".format(feat))
        self.assertTrue((self.root / "scaffold.py").exists())  # built ON the dep, not recreated
        self.assertTrue((self.root / "feat.py").exists())

    def test_merge_reprepares_open_sibling_gate(self) -> None:
        # Merging one issue re-readies every OTHER open merge gate with a FRESH gate
        # comment, so a standing approval can't ride a moved primary.
        from specseed_runtime.executing import advance
        from specseed_runtime.executing.context import load_entity

        (self.root / "base.txt").write_text("base\n", encoding="utf-8")
        self._git("add", "-A")
        self._commit("-m", "base")
        a = self._seed_both("FEAT-0006 A", ["tier:issue", "status:awaiting_approval"])
        b = self._seed_both("FEAT-0007 B", ["tier:issue", "status:awaiting_approval"])
        # branch names must match branch_name() (post id folded on the end) so the
        # reprepare sweep, which derives B's branch from its entity, finds it.
        branch_a = "feat-0006-a-{0}".format(a)
        branch_b = "feat-0007-b-{0}".format(b)
        for branch, fn in ((branch_a, "a.txt"), (branch_b, "b.txt")):
            self._git("checkout", "-b", branch)
            (self.root / fn).write_text("x\n", encoding="utf-8")
            self._git("add", "-A")
            self._commit("-m", "work on " + branch)
            self._git("checkout", "main")
        # B is parked at a merge gate (the comment lives in BOTH mirrors).
        gate = "Merge ready\n{0}\n<!-- specseed:approval-request APR-0001 -->".format(
            advance.MERGE_GATE_MARKER
        )
        self.local.add_entry_comment(b, gate)
        self.remote.add_entry_comment(b, gate)

        entity_a, _ = load_entity(self.ctx, str(a))
        note = dispatch_mod._execute_merge(self.ctx, entity_a, branch_a, {"task_id": None})
        self.assertIn("merged", note)
        # B got a SECOND gate comment (re-readied) -> its earlier approval is invalidated.
        gate_comments = [
            c for c in self.remote.get_entry(b).data.comments
            if advance.MERGE_GATE_MARKER in (c.body or "")
        ]
        self.assertGreaterEqual(len(gate_comments), 2)
        self.assertEqual(_git_ops.current_branch(self.root), "main")


if __name__ == "__main__":
    unittest.main()
