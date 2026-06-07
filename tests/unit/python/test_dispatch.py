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
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("implementing a specseed work issue", self.runner.calls[0]["prompt"])
        self.assertEqual(self.runner.calls[0]["cwd"], str(self.root))

    def test_review_runs_review_prompt(self) -> None:
        self.ctx.runner = FakeAgentRunner(AgentResult(
            ok=True, returncode=0,
            report={"verdict": "approve", "confidence": 0.99, "summary": "ok"},
        ))
        self.runner = self.ctx.runner
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

    def test_implement_missing_report_is_retryable_failure(self) -> None:
        # rc==0 but no result file -> not really done -> retryable failure (this is
        # the "Blocked: cannot make changes", exit 0 case).
        self.ctx.runner = FakeAgentRunner(AgentResult(ok=True, returncode=0, report=None,
                                                      report_error="no result file"))
        eid = self._seed_local_entry("Do it", ["tier:issue", "status:todo"])
        out = dispatch(self.ctx, {"action": "handle_entry_created", "post_id": str(eid), "payload": {}})
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
        out = dispatch(self.ctx, {"action": "handle_entry_created", "post_id": str(eid), "payload": {}})
        self.assertFalse(out.success)
        self.assertTrue(out.requeue)
        self.assertTrue(out.quota)

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
        out = dispatch(
            self.ctx,
            {"action": "handle_entry_created", "post_id": str(eid), "payload": {}},
        )
        self.assertFalse(out.success)
        self.assertIn("agent exited with code 1", out.error)
        self.assertIn("Not logged in", out.error)


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
        out = dispatch(
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
        task = self._retried_task(action="run_spec_change_script")
        self._error_post_for(task, closed=False)
        out = dispatch(self.ctx, task)
        # gate passed; the handler itself then rejects the empty payload
        self.assertFalse(out.success)
        self.assertIn("payload missing", out.error)


class RunSpecChangeScriptFinalizeTest(DispatchTestBase):
    """The approval-path apply (close_request flag) closes the request post on a
    successful run; a mechanical run (no flag) or a failed run leaves it open. The
    request's close is the runtime's job, not the agent-emitted plan.json.closes."""

    def _apply_dir(self, rid, script="print('noop')\n"):
        d = Path(self.ctx.storage) / "spec-change" / str(rid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "apply.py").write_text(script, encoding="utf-8")
        return d

    def _request(self):
        return self.remote.add_entry("adapt request", labels=[]).data.id

    def _task(self, rid, d, close_request):
        return {
            "task_id": 1,
            "action": "run_spec_change_script",
            "post_id": str(rid),
            "payload": {
                "dir": str(d), "script": "apply.py", "route": "adapt",
                "request_id": str(rid), "close_request": close_request,
            },
        }

    def test_finalizing_apply_closes_request(self) -> None:
        rid = self._request()
        d = self._apply_dir(rid)
        out = dispatch_mod.run_spec_change_script(self.ctx, self._task(rid, d, True))
        self.assertTrue(out.success)
        self.assertFalse(self.remote.get_entry(rid).data.is_open)

    def test_mechanical_run_leaves_request_open(self) -> None:
        rid = self._request()
        d = self._apply_dir(rid)
        out = dispatch_mod.run_spec_change_script(self.ctx, self._task(rid, d, False))
        self.assertTrue(out.success)
        self.assertTrue(self.remote.get_entry(rid).data.is_open)

    def test_already_closed_request_is_noop(self) -> None:
        rid = self._request()
        self.remote.set_entry_closed(rid)
        d = self._apply_dir(rid)
        out = dispatch_mod.run_spec_change_script(self.ctx, self._task(rid, d, True))
        self.assertTrue(out.success)
        self.assertFalse(self.remote.get_entry(rid).data.is_open)

    def test_failed_apply_does_not_close_request(self) -> None:
        rid = self._request()
        d = self._apply_dir(rid, script="import sys; sys.exit(1)\n")
        out = dispatch_mod.run_spec_change_script(self.ctx, self._task(rid, d, True))
        self.assertFalse(out.success)
        self.assertTrue(self.remote.get_entry(rid).data.is_open)


import shutil
import subprocess
from specseed_runtime.executing import git_ops as _git_ops

_HAS_GIT = shutil.which("git") is not None


class DependencyGateTest(DispatchTestBase):
    """An issue is held until the issues it depends on are done."""

    def _seed_with_body(self, title, labels, body):
        for label in labels:
            self.local.create_label(label)
        return self.local.add_entry(title, body=body, labels=labels).data.id

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
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(issue), "payload": {"label": "status:todo"}},
        )
        self.assertFalse(out.requeue)
        self.assertEqual(len(self.runner.calls), 1)  # dep done -> agent ran

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
        out = dispatch(
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
        self._git("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "--allow-empty", "-m", "root")

    def _git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.root),
                              capture_output=True, text=True)

    def _seed_both(self, title, labels):
        # The done-transition + merge reads the entity's status from the REMOTE, so
        # seed it there too (matching ids: both dbs are fresh + autoincrement).
        for label in labels:
            self.local.create_label(label)
            self.remote.create_label(label)
        rid = self.remote.add_entry(title, labels=labels).data.id
        lid = self.local.add_entry(title, labels=labels).data.id
        self.assertEqual(str(rid), str(lid))
        return lid

    def test_implement_branches_commits_and_returns_to_primary(self) -> None:
        self.ctx.runner = _FileWritingRunner(
            AgentResult(ok=True, returncode=0,
                        report={"status": "done", "summary": "did it", "files_changed": ["new.py"]}),
            filename="new.py",
        )
        eid = self._seed_local_entry("FEAT-0001 Do the thing", ["tier:issue", "status:todo"])
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        # returned to primary, and primary has no agent file
        self.assertEqual(_git_ops.current_branch(self.root), "main")
        self.assertFalse((self.root / "new.py").exists())
        # the issue branch exists and carries the committed file
        branch = "feat-0001-do-the-thing"
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
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertFalse((self.root / "x.py").exists())  # not on primary
        self.assertIn("merge gate", out.detail)
        labels = {l.name for l in self.remote.get_entry(eid).data.labels}
        self.assertIn("issue:status:awaiting_approval", labels)
        # the work branch still exists, intact for the approved merge (or a manual one)
        self.assertEqual(
            self._git("rev-parse", "--verify", "refs/heads/feat-0002-thing").returncode, 0
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
        out = dispatch(
            self.ctx,
            {"action": "handle_label_added", "post_id": str(eid), "payload": {"label": "status:todo"}},
        )
        self.assertTrue(out.success)
        self.assertEqual(_git_ops.current_branch(self.root), "main")
        # primary now carries the merged file
        self.assertTrue((self.root / "y.py").exists())
        self.assertIn("merged", out.detail)


if __name__ == "__main__":
    unittest.main()
