"""test_dispatch.py - action -> handler routing + intent decisions.

FakeAgentRunner only; TrackingRemoteLocal/TrackingLocal mirrors. No GitHub/GitLab.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
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

    def _seed_local_entry(self, title, labels):
        # Put an entry into the LOCAL mirror (load_entity reads local).
        for label in labels:
            self.local.create_label(label)
        return self.local.add_entry(title, labels=labels).data.id


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

    def test_spec_change_rejected_status_is_not_rerun(self) -> None:
        _, intent = self._intent_for(
            ["spec-change:adapt", "spec-change:status:rejected"], action="handle_entry_updated"
        )
        self.assertEqual(intent, AgentIntent.NONE)

    def test_spec_change_awaiting_approval_only_wakes_on_comment(self) -> None:
        labels = ["spec-change:adapt", "spec-change:status:awaiting_approval"]
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
        eid = self._seed_local_entry("Do the thing", ["tier:issue", "status:todo"])
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("implementing a specseed work issue", self.runner.calls[0]["prompt"])
        self.assertEqual(self.runner.calls[0]["cwd"], str(self.root))

    def test_review_runs_review_prompt(self) -> None:
        eid = self._seed_local_entry("Review me", ["tier:issue", "status:in_review"])
        out = dispatch(
            self.ctx,
            {"action": "handle_comment_added", "post_id": str(eid), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("reviewing completed work", self.runner.calls[0]["prompt"])

    def test_spec_change_label_runs_spec_change_prompt(self) -> None:
        eid = self._seed_local_entry("Adopt request", ["spec-change:adopt"])
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "spec-change:adopt"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("adopt", self.runner.calls[0]["prompt"])
        self.assertIn("spec-change worker", self.runner.calls[0]["prompt"])
        # Skill docs come from the engine (absolute path), not the target's specseed dir.
        from specseed_runtime.storage_paths import default_specseed_dir
        skill_dir = str(default_specseed_dir() / "skills" / "specseed")
        self.assertIn(f"{skill_dir}/SKILL.md", self.runner.calls[0]["prompt"])
        # spec/ and apply.py still live under the target's specseed dir
        self.assertIn("seedmeta/spec/", self.runner.calls[0]["prompt"])

    def test_inject_label_runs_inject_route_prompt(self) -> None:
        eid = self._seed_local_entry("Manual hotfix", ["spec-change:inject"])
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "spec-change:inject"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("routes/inject.md", self.runner.calls[0]["prompt"])
        self.assertIn("run the 'inject' route", self.runner.calls[0]["prompt"])

    def test_draft_removed_runs_spec_change_prompt(self) -> None:
        eid = self._seed_local_entry("Adapt request", ["spec-change:adapt"])
        out = dispatch(
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
        out = dispatch(
            self.ctx,
            {"action": "handle_comment_added", "post_id": str(eid), "payload": {}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("run the 'adapt' route", self.runner.calls[0]["prompt"])

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

    def test_spec_change_skipped_when_apply_already_queued(self) -> None:
        # A prior run already wrote+enqueued apply.py; a second trigger for the
        # same request must not re-run the (expensive) worker.
        from specseed_runtime.scheduling.spec_change import (
            enqueue_spec_change_run,
        )

        eid = self._seed_local_entry("Adapt request", ["spec-change:adapt"])
        script = self.root / "apply.py"
        script.write_text("print('noop')\n", encoding="utf-8")
        enqueue_spec_change_run(script, request_id=str(eid), route="adapt", db=self.db)

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

    def test_agent_timeout_requeues(self) -> None:
        self.ctx.runner = FakeAgentRunner(
            result=AgentResult(ok=False, timed_out=True, error="timed out")
        )
        eid = self._seed_local_entry("Slow", ["tier:issue", "status:todo"])
        out = dispatch(
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
        out = dispatch(
            self.ctx,
            {"action": "handle_entry_created", "post_id": str(eid), "payload": {}},
        )
        self.assertFalse(out.success)
        self.assertFalse(out.requeue)
        self.assertIsNotNone(out.error)


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


if __name__ == "__main__":
    unittest.main()
