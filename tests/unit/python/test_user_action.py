"""test_user_action.py - the UA-NNNN user-action path (TKT-14).

Covers the pure request vocabulary (state_machines/user_action), the runtime side
(id allocation, running a check, running a one-click setup, granting one directory),
the agent-report schema gate, the implement transition that parks an issue
``needs_user_action``, and the UI server's Check / Run setup / Approve endpoint end
to end.

No GitHub/GitLab: TrackingRemoteLocal mirrors only.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from specseed_runtime.db.database import Database
from specseed_runtime.executing import advance
from specseed_runtime.executing import agent_report
from specseed_runtime.executing import user_action as rt_user_action
from specseed_runtime.executing.agent_runner import AgentResult, FakeAgentRunner
from specseed_runtime.executing.context import ExecutionContext, load_entity
from specseed_runtime.executing.dispatch import dispatch
from specseed_runtime.executing.permissions import Permissions
from specseed_runtime.executing.prompts import render_action_gates
from specseed_runtime.entities import issue as _issue  # noqa: F401
from specseed_runtime.state_machines import user_action as ua
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal

_SERVER_PATH = Path(__file__).resolve().parents[3] / "src" / "ui" / "server.py"
_spec = importlib.util.spec_from_file_location("specseed_web_server_ua_test", _SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


_ENV_REQUEST = {
    "kind": "environment",
    "title": "Install a JDK and Maven",
    "instructions": "Run `apt-get install -y default-jdk maven`.",
    "check": {"command": "which mvn", "timeout_seconds": 30},
    "hint": "Make sure /usr/bin is on PATH.",
}
# The same request, but able to fix itself in one click (T-32).
_SETUP_REQUEST = {
    **_ENV_REQUEST,
    "setup": {"command": "apt-get install -y default-jdk maven",
              "description": "Installs a JDK and Maven from apt."},
}
_DIR_REQUEST = {
    "kind": "directory",
    "title": "Grant the build cache directory",
    "instructions": "Approve the directory so the build can cache artifacts.",
    "path": "/var/tmp/nooks-build",
    "reason": "Maven writes its local repository outside the project.",
}


def _impl_report(status="needs_user_action", summary="paused", request=None):
    """An implement AgentResult carrying a report the runtime will accept."""
    report = {
        "status": status, "summary": summary, "files_changed": [],
        "recommend_spec_change": False,
    }
    if request is not None:
        report["user_action"] = request
    return AgentResult(ok=True, returncode=0, report=report)


class VocabularyTest(unittest.TestCase):
    def test_format_and_number_round_trip(self) -> None:
        self.assertEqual(ua.format_ua(1), "UA-0001")
        self.assertEqual(ua.format_ua(12345), "UA-12345")
        self.assertEqual(ua.ua_number("UA-0007"), 7)
        self.assertIsNone(ua.ua_number("APR-0007"))

    def test_environment_request_validates(self) -> None:
        request, error = ua.validate_request(_ENV_REQUEST)
        self.assertIsNone(error)
        self.assertEqual(request["check"], {"command": "which mvn", "timeout_seconds": 30})

    def test_bare_string_check_is_accepted(self) -> None:
        request, error = ua.validate_request({**_ENV_REQUEST, "check": "which mvn"})
        self.assertIsNone(error)
        self.assertEqual(request["check"]["timeout_seconds"], ua.DEFAULT_CHECK_TIMEOUT)

    def test_check_timeout_is_clamped(self) -> None:
        request, _ = ua.validate_request(
            {**_ENV_REQUEST, "check": {"command": "x", "timeout_seconds": 99999}}
        )
        self.assertEqual(request["check"]["timeout_seconds"], ua.MAX_CHECK_TIMEOUT)

    def test_environment_request_without_a_check_is_rejected(self) -> None:
        # The whole point is that it self-clears; no check = "human types done".
        request, error = ua.validate_request({**_ENV_REQUEST, "check": None})
        self.assertIsNone(request)
        self.assertIn("check", error)

    def test_setup_is_optional(self) -> None:
        # Not everything can be scripted; without a setup the card is instructions + Check.
        request, error = ua.validate_request(_ENV_REQUEST)
        self.assertIsNone(error)
        self.assertNotIn("setup", request)

    def test_setup_validates_and_defaults_its_timeout(self) -> None:
        request, error = ua.validate_request(_SETUP_REQUEST)
        self.assertIsNone(error)
        self.assertEqual(request["setup"]["command"], "apt-get install -y default-jdk maven")
        self.assertEqual(request["setup"]["timeout_seconds"], ua.DEFAULT_SETUP_TIMEOUT)

    def test_setup_gets_far_longer_than_a_check(self) -> None:
        # An install legitimately spends minutes; the check is a probe. Same number here
        # would mean one of the two is wrong.
        self.assertGreater(ua.DEFAULT_SETUP_TIMEOUT, ua.DEFAULT_CHECK_TIMEOUT)
        request, _ = ua.validate_request(
            {**_SETUP_REQUEST, "setup": {"command": "x", "timeout_seconds": 999999}}
        )
        self.assertEqual(request["setup"]["timeout_seconds"], ua.MAX_SETUP_TIMEOUT)

    def test_bare_string_setup_is_accepted(self) -> None:
        request, error = ua.validate_request({**_ENV_REQUEST, "setup": "apt-get install -y maven"})
        self.assertIsNone(error)
        self.assertEqual(request["setup"]["command"], "apt-get install -y maven")

    def test_a_malformed_setup_is_an_error_not_a_silent_drop(self) -> None:
        # Dropping it would look exactly like an agent that never offered one.
        request, error = ua.validate_request({**_ENV_REQUEST, "setup": {"description": "installs it"}})
        self.assertIsNone(request)
        self.assertIn("setup", error)
        self.assertIsNone(ua.validate_request({**_ENV_REQUEST, "setup": {}})[0])
        self.assertIsNone(ua.validate_request({**_ENV_REQUEST, "setup": ["apt-get"]})[0])

    def test_an_absent_setup_is_spelled_null_or_empty(self) -> None:
        # "I have no command for this" is a legitimate answer and must not cost a rerun.
        for value in (None, ""):
            request, error = ua.validate_request({**_ENV_REQUEST, "setup": value})
            self.assertIsNone(error)
            self.assertNotIn("setup", request)

    def test_a_directory_request_carries_no_setup(self) -> None:
        request, error = ua.validate_request({**_DIR_REQUEST, "setup": {"command": "rm -rf /"}})
        self.assertIsNone(error)
        self.assertNotIn("setup", request)

    def test_setup_comment_shows_the_command_and_round_trips(self) -> None:
        body = ua.request_comment("UA-0006", ua.validate_request(_SETUP_REQUEST)[0])
        self.assertIn("apt-get install -y default-jdk maven", body)
        self.assertIn("Run setup", body)
        self.assertIn("Installs a JDK and Maven from apt.", body)
        parsed = ua.parse_request(body)
        self.assertEqual(parsed["setup"]["command"], "apt-get install -y default-jdk maven")

    def test_a_request_without_setup_does_not_promise_a_button(self) -> None:
        body = ua.request_comment("UA-0007", ua.validate_request(_ENV_REQUEST)[0])
        self.assertNotIn("Run setup", body)
        self.assertIn("Check", body)

    def test_directory_request_needs_a_concrete_path(self) -> None:
        self.assertIsNone(ua.validate_request({**_DIR_REQUEST, "path": ""})[0])
        request, error = ua.validate_request(_DIR_REQUEST)
        self.assertIsNone(error)
        self.assertEqual(request["path"], "/var/tmp/nooks-build")

    def test_missing_instructions_are_rejected(self) -> None:
        self.assertIsNone(ua.validate_request({**_ENV_REQUEST, "instructions": " "})[0])

    def test_comment_round_trips_through_the_marker(self) -> None:
        body = ua.request_comment("UA-0004", ua.validate_request(_ENV_REQUEST)[0])
        self.assertIn("Install a JDK and Maven", body)
        self.assertIn("which mvn", body)
        parsed = ua.parse_request(body)
        self.assertEqual(parsed["id"], "UA-0004")
        self.assertEqual(parsed["check"]["command"], "which mvn")

    def test_directory_comment_states_what_approving_does(self) -> None:
        body = ua.request_comment("UA-0005", ua.validate_request(_DIR_REQUEST)[0])
        self.assertIn("/var/tmp/nooks-build", body)
        self.assertIn("allowed_directories", body)
        self.assertEqual(ua.parse_request(body)["kind"], "directory")

    def test_prose_mentioning_a_token_is_not_a_request(self) -> None:
        self.assertIsNone(ua.parse_request("UA-0001 is still open, I think"))
        self.assertIsNone(ua.parse_request("<!-- specseed:user-action UA-0001 not-json -->"))

    def test_open_requests_dedupes_by_id_keeping_the_latest(self) -> None:
        first = ua.request_comment("UA-0001", ua.validate_request(_ENV_REQUEST)[0])
        restated = ua.request_comment(
            "UA-0001", ua.validate_request({**_ENV_REQUEST, "title": "Install Maven"})[0]
        )
        second = ua.request_comment("UA-0002", ua.validate_request(_DIR_REQUEST)[0])
        found = ua.open_requests([{"body": first}, {"body": "chatter"}, {"body": restated}, {"body": second}])
        self.assertEqual([r["id"] for r in found], ["UA-0001", "UA-0002"])
        self.assertEqual(found[0]["title"], "Install Maven")


class IdAllocationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)

    def test_ids_are_monotonic_from_one(self) -> None:
        self.assertEqual(rt_user_action.next_ua_id(self.storage), "UA-0001")
        self.assertEqual(rt_user_action.next_ua_id(self.storage), "UA-0002")

    def test_concurrent_allocation_never_collides(self) -> None:
        out: list[str] = []
        lock = threading.Lock()

        def grab() -> None:
            token = rt_user_action.next_ua_id(self.storage)
            with lock:
                out.append(token)

        threads = [threading.Thread(target=grab) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(out)), 12)


class RunCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _request(self, command, timeout=30):
        return ua.validate_request({**_ENV_REQUEST, "check": {"command": command, "timeout_seconds": timeout}})[0]

    def test_passing_check_reports_ok(self) -> None:
        result = rt_user_action.run_check(self._request("exit 0"), self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)

    def test_failing_check_captures_output_and_exit_code(self) -> None:
        result = rt_user_action.run_check(self._request("echo nope >&2; exit 3"), self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 3)
        self.assertIn("nope", result["output"])

    def test_check_runs_in_the_repo_root(self) -> None:
        (self.root / "marker.txt").write_text("x", encoding="utf-8")
        self.assertTrue(rt_user_action.run_check(self._request("test -f marker.txt"), self.root)["ok"])

    def test_timeout_is_a_failed_check_not_an_exception(self) -> None:
        result = rt_user_action.run_check(self._request("sleep 5", timeout=1), self.root)
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["error"])

    def test_failure_comment_carries_the_hint_and_the_output(self) -> None:
        request = ua.validate_request(_ENV_REQUEST)[0]
        result = rt_user_action.run_check(self._request("echo boom >&2; exit 1"), self.root)
        body = rt_user_action.check_result_comment("UA-0001", request, result)
        self.assertIn("Check failed", body)
        self.assertIn("boom", body)
        self.assertIn(request["hint"], body)

    def test_pass_comment_says_the_issue_resumes(self) -> None:
        request = ua.validate_request(_ENV_REQUEST)[0]
        body = rt_user_action.check_result_comment(
            "UA-0001", request, rt_user_action.run_check(self._request("exit 0"), self.root)
        )
        self.assertIn("Check passed", body)
        self.assertIn("todo", body)


class RunSetupTest(unittest.TestCase):
    """The one-click setup command (T-32): it mutates, the check still decides."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _request(self, command, timeout=30, check="exit 0"):
        return ua.validate_request({
            **_ENV_REQUEST,
            "check": {"command": check},
            "setup": {"command": command, "timeout_seconds": timeout},
        })[0]

    def test_has_setup_distinguishes_the_two_shapes(self) -> None:
        self.assertTrue(rt_user_action.has_setup(ua.validate_request(_SETUP_REQUEST)[0]))
        self.assertFalse(rt_user_action.has_setup(ua.validate_request(_ENV_REQUEST)[0]))
        self.assertFalse(rt_user_action.has_setup(ua.validate_request(_DIR_REQUEST)[0]))

    def test_setup_runs_and_can_change_the_tree(self) -> None:
        result = rt_user_action.run_setup(self._request("touch installed.txt"), self.root)
        self.assertTrue(result["ok"])
        self.assertTrue((self.root / "installed.txt").exists())

    def test_a_failing_setup_captures_its_transcript(self) -> None:
        result = rt_user_action.run_setup(self._request("echo boom >&2; exit 9"), self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 9)
        self.assertIn("boom", result["output"])

    def test_timeout_is_a_failed_setup_not_an_exception(self) -> None:
        result = rt_user_action.run_setup(self._request("sleep 5", timeout=1), self.root)
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["error"])

    def test_a_request_without_a_setup_says_so_rather_than_running_the_check(self) -> None:
        result = rt_user_action.run_setup(ua.validate_request(_ENV_REQUEST)[0], self.root)
        self.assertFalse(result["ok"])
        self.assertIn("no setup command", result["error"])

    def test_run_check_will_not_run_a_setup_command(self) -> None:
        # The two must not converge: a check is contractually read-only, and reaching for
        # `setup` from run_check is exactly how that guarantee would rot away.
        request = ua.validate_request({**_SETUP_REQUEST, "check": {"command": "exit 0"}})[0]
        result = rt_user_action.run_check(request, self.root)
        self.assertEqual(result["command"], "exit 0")

    def test_setup_keeps_more_output_than_a_check(self) -> None:
        noisy = "python3 -c \"print('x' * 8000)\""
        self.assertGreater(
            len(rt_user_action.run_setup(self._request(noisy), self.root)["output"]),
            len(rt_user_action.run_check(self._request("x", check=noisy), self.root)["output"]),
        )

    def test_combined_comment_reports_setup_then_check(self) -> None:
        request = self._request("echo installing", check="exit 0")
        setup = rt_user_action.run_setup(request, self.root)
        check = rt_user_action.run_check(request, self.root)
        body = rt_user_action.setup_result_comment("UA-0001", request, setup, check)
        self.assertIn("Setup ran and the check passed", body)
        self.assertIn("installing", body)  # the transcript, not just an exit code
        self.assertIn("todo", body)

    def test_a_setup_that_does_not_satisfy_the_check_stays_parked(self) -> None:
        request = self._request("echo did nothing", check="exit 1")
        setup = rt_user_action.run_setup(request, self.root)
        check = rt_user_action.run_check(request, self.root)
        body = rt_user_action.setup_result_comment("UA-0001", request, setup, check)
        self.assertIn("check still fails", body)
        self.assertIn(_ENV_REQUEST["hint"], body)

    def test_a_failed_setup_says_the_check_was_not_run(self) -> None:
        request = self._request("exit 4")
        body = rt_user_action.setup_result_comment(
            "UA-0001", request, rt_user_action.run_setup(request, self.root), None
        )
        self.assertIn("Setup failed", body)
        self.assertIn("check was not run", body)


class GrantDirectoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)

    def _config(self) -> dict:
        from specseed_runtime.storage_paths import config_file
        return json.loads(config_file(self.storage).read_text(encoding="utf-8"))

    def test_grant_creates_the_list_and_is_idempotent(self) -> None:
        rt_user_action.grant_directory(self.storage, "/var/tmp/a")
        out = rt_user_action.grant_directory(self.storage, "/var/tmp/a")
        self.assertEqual(out["allowed_directories"], ["/var/tmp/a"])
        self.assertEqual(
            self._config()["permissions"]["agents"]["allowed_directories"], ["/var/tmp/a"]
        )

    def test_grant_preserves_existing_gates(self) -> None:
        from specseed_runtime.storage_paths import config_file
        path = config_file(self.storage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"permissions": {"agents": {"outside_repo": "block"}}}), encoding="utf-8")
        rt_user_action.grant_directory(self.storage, "/var/tmp/b")
        agents = self._config()["permissions"]["agents"]
        self.assertEqual(agents["outside_repo"], "block")
        self.assertEqual(agents["allowed_directories"], ["/var/tmp/b"])

    def test_empty_directory_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            rt_user_action.grant_directory(self.storage, "  ")


class PermissionsAndPromptTest(unittest.TestCase):
    def test_allowed_directories_are_read_and_cleaned(self) -> None:
        perms = Permissions({"permissions": {"agents": {"allowed_directories": ["/a", "  ", " /b "]}}})
        self.assertEqual(perms.agent_allowed_directories(), ["/a", "/b"])

    def test_a_carve_out_does_not_relax_the_gate(self) -> None:
        perms = Permissions({"permissions": {"agents": {"allowed_directories": ["/a"]}}})
        self.assertEqual(perms.agent_gate("outside_repo"), "block")

    def test_non_list_config_degrades_to_empty(self) -> None:
        self.assertEqual(
            Permissions({"permissions": {"agents": {"allowed_directories": "/a"}}}).agent_allowed_directories(),
            [],
        )

    def test_prompt_states_the_escape_hatch_and_the_grants(self) -> None:
        class _Ctx:
            config = {"permissions": {"agents": {"allowed_directories": ["/var/tmp/nooks"]}}}

        rendered = render_action_gates(_Ctx())
        self.assertIn("/var/tmp/nooks", rendered)
        self.assertIn("needs_user_action", rendered)
        self.assertIn("Run setup", rendered)

    def test_prompt_omits_the_grant_line_when_nothing_is_granted(self) -> None:
        class _Ctx:
            config = {"permissions": {"agents": {}}}

        self.assertNotIn("Approved directories", render_action_gates(_Ctx()))


class AgentReportSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "result.json"

    def _parse(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return agent_report.parse_result_file(self.path, agent_report.IMPLEMENT)

    def test_valid_request_is_kept_on_the_report(self) -> None:
        report, error = self._parse(
            {"status": "needs_user_action", "summary": "no jdk", "user_action": _ENV_REQUEST}
        )
        self.assertIsNone(error)
        self.assertEqual(report["user_action"]["check"]["command"], "which mvn")

    def test_status_without_a_request_is_rejected(self) -> None:
        report, error = self._parse({"status": "needs_user_action", "summary": "no jdk"})
        self.assertIsNone(report)
        self.assertIn("needs_user_action", error)

    def test_malformed_request_is_rejected_with_the_reason(self) -> None:
        report, error = self._parse(
            {"status": "needs_user_action", "user_action": {**_ENV_REQUEST, "check": {}}}
        )
        self.assertIsNone(report)
        self.assertIn("check.command", error)

    def test_other_statuses_carry_no_request(self) -> None:
        report, error = self._parse({"status": "blocked", "summary": "nope"})
        self.assertIsNone(error)
        self.assertNotIn("user_action", report)

    def test_a_setup_command_survives_onto_the_report(self) -> None:
        report, error = self._parse(
            {"status": "needs_user_action", "summary": "no jdk", "user_action": _SETUP_REQUEST}
        )
        self.assertIsNone(error)
        self.assertEqual(
            report["user_action"]["setup"]["command"], "apt-get install -y default-jdk maven"
        )

    def test_a_malformed_setup_is_rejected_rather_than_dropped(self) -> None:
        report, error = self._parse(
            {"status": "needs_user_action", "user_action": {**_ENV_REQUEST, "setup": {}}}
        )
        self.assertIsNone(report)
        self.assertIn("setup", error)

    def test_the_schema_block_documents_the_status(self) -> None:
        text = agent_report.result_instructions(agent_report.IMPLEMENT)
        self.assertIn("needs_user_action", text)
        self.assertIn("user_action", text)

    def test_the_schema_block_asks_for_a_setup_command(self) -> None:
        # A request that could have carried a command and only described one is worse.
        text = agent_report.result_instructions(agent_report.IMPLEMENT)
        self.assertIn('"setup"', text)
        self.assertIn("Run setup", text)


_COMMENT = "handle_comment_added"


class _SR:
    """Minimal state-machine result stand-in for resolve_blocked."""

    def __init__(self, approvers):
        self.approvers = approvers
        self.review_required = False
        self.hitl_required = False


class _RuntimeBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".gitignore").write_text("*.db\n*.db-*\nstorage/\n", encoding="utf-8")
        self._git("init")
        self._git("symbolic-ref", "HEAD", "refs/heads/main")
        self._git("add", ".gitignore")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "root")
        self.remote = TrackingRemoteLocal(db_path=self.root / "remote.db", author="alice")
        self.local = TrackingLocal(db_path=self.root / "local.db", author="agent")
        self.db = Database(db_path=self.root / "queue.db")

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True)

    def _config(self):
        return {
            "specseed_dir": "seedmeta",
            "specseed_primary_branch": "main",
            "approvals": {"approver_usernames": ["alice"]},
            "permissions": {},
        }

    def _ctx(self, runner):
        config = self._config()
        return ExecutionContext(
            db=self.db, local=self.local, remote=self.remote, config=config,
            permissions=Permissions(config), runner=runner, repo_root=self.root,
            storage=self.root / "storage", cancel=threading.Event(),
        )

    def _seed(self, title, labels, comments=()):
        # Entities load from the LOCAL mirror; transitions mutate the REMOTE. Seed both,
        # exactly as the live sync leaves them (see test_advance._Base).
        for label in labels:
            self.local.create_label(label)
        eid = self.local.add_entry(title, labels=list(labels)).data.id
        for body in comments:
            self.local.add_entry_comment(eid, body)
        for label in labels:
            self.remote.create_label(label)
        self.remote.add_entry(title, labels=list(labels))
        return eid

    def _labels(self, eid):
        return {l.name for l in self.remote.get_entry(eid).data.labels}

    def _bodies(self, eid):
        return [c.body for c in self.remote.get_entry(eid).data.comments]

    def _drain(self, ctx, max_iter=100):
        """Drive both lanes to quiescence like the two-lane scheduler."""
        from specseed_runtime.db.database import LANE_CONTROL, LANE_WORK

        for _ in range(max_iter):
            task = self.db.claim_next(LANE_CONTROL) or self.db.claim_next(LANE_WORK)
            if task is None:
                return
            out = dispatch(ctx, task)
            if out is not None and out.requeue and not out.quota:
                self.db.requeue(task["task_id"], not_before="2999-01-01T00:00:00Z")
            else:
                self.db.complete(task["task_id"], bool(out and out.success), out.error if out else None)
        raise AssertionError("drain did not quiesce within max_iter")

    def _run(self, ctx, task):
        """Dispatch a control event, then drain the work it schedules."""
        out = dispatch(ctx, task)
        self._drain(ctx)
        return out


class ImplementParksTest(_RuntimeBase):
    def test_needs_user_action_parks_and_posts_the_request(self) -> None:
        eid = self._seed("Scaffold", ["issue", "issue:status:todo"])
        ctx = self._ctx(FakeAgentRunner(_impl_report(request=_ENV_REQUEST)))
        out = self._run(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(out.success)
        labels = self._labels(eid)
        self.assertIn("issue:status:needs_user_action", labels)
        self.assertNotIn("issue:status:in_review", labels)
        self.assertNotIn("issue:status:blocked", labels)
        request = ua.open_requests([{"body": b} for b in self._bodies(eid)])
        self.assertEqual(len(request), 1)
        self.assertEqual(request[0]["id"], "UA-0001")
        self.assertEqual(request[0]["check"]["command"], "which mvn")

    def test_the_summary_is_posted_alongside_the_request(self) -> None:
        eid = self._seed("Scaffold", ["issue", "issue:status:todo"])
        ctx = self._ctx(FakeAgentRunner(_impl_report(summary="wrote the pom, no jdk", request=_ENV_REQUEST)))
        self._run(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertTrue(any("wrote the pom, no jdk" in b for b in self._bodies(eid)))

    def test_a_directory_request_parks_the_same_way(self) -> None:
        eid = self._seed("Scaffold", ["issue", "issue:status:todo"])
        ctx = self._ctx(FakeAgentRunner(_impl_report(request=_DIR_REQUEST)))
        self._run(ctx, {"action": "handle_label_added", "post_id": str(eid), "payload": {}})
        self.assertIn("issue:status:needs_user_action", self._labels(eid))
        self.assertEqual(ua.open_requests([{"body": b} for b in self._bodies(eid)])[0]["kind"], "directory")

    def test_a_request_that_lost_its_payload_degrades_to_blocked(self) -> None:
        # Never park in a state nothing can clear: no request => plain blocked.
        eid = self._seed("Scaffold", ["issue", "issue:status:todo"])
        ctx = self._ctx(FakeAgentRunner(AgentResult(ok=True)))
        entity, _ = load_entity(ctx, str(eid))
        transition = advance._park_needs_user_action(ctx, entity, {"status": "needs_user_action"})
        self.assertIn("blocked", transition.detail)
        self.assertIn("issue:status:blocked", self._labels(eid))

    def test_a_human_can_still_bypass_a_parked_issue(self) -> None:
        # The Check button is the normal exit, but guidance must not be locked out by
        # a probe the agent wrote.
        eid = self._seed("Scaffold", ["issue", "issue:status:needs_user_action"],
                         comments=["skip the toolchain, stub it out"])
        ctx = self._ctx(FakeAgentRunner(AgentResult(ok=True)))
        entity, conversation = load_entity(ctx, str(eid))
        transition = advance.resolve_blocked(ctx, entity, _SR([]), conversation, _COMMENT)
        self.assertIn("todo", transition.detail)
        self.assertIn("issue:status:todo", self._labels(eid))


class ServerFlagsTest(unittest.TestCase):
    def test_needs_user_action_is_detected_from_the_label(self) -> None:
        post = {"labels": [{"name": "issue:status:needs_user_action"}]}
        self.assertTrue(server._needs_user_action(post))
        self.assertTrue(server._needs_approval(post))  # the Need feedback tab is the union

    def test_ordinary_states_are_not_in_the_tab(self) -> None:
        post = {"labels": [{"name": "issue:status:in_progress"}]}
        self.assertFalse(server._needs_user_action(post))
        self.assertFalse(server._needs_approval(post))

    def test_approval_gates_still_count(self) -> None:
        self.assertTrue(server._needs_approval({"labels": [{"name": "issue:status:awaiting_approval"}]}))
        self.assertTrue(server._needs_approval({"labels": [{"name": "issue:status:awaiting_merge"}]}))

    def test_tier_is_recovered_for_the_status_swap(self) -> None:
        self.assertEqual(server._post_tier({"labels": [{"name": "ticket:status:needs_user_action"}]}), "ticket")
        self.assertEqual(server._post_tier({"labels": [{"name": "issue"}]}), "issue")
        self.assertEqual(server._post_tier({"labels": []}), "issue")


class ServerEndpointTest(unittest.TestCase):
    """The Check button, end to end over the real local tracker."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.storage = self.root / "storage"
        self.target = self.root / "target"
        self.target.mkdir(parents=True)
        self.record = {"id": "r1", "storage": str(self.storage), "target": str(self.target),
                       "provider": "local"}
        self.tracker = TrackingRemoteLocal(db_path=server._tracker_db(self.storage), author="alice")

    def _park(self, request, ua_id="UA-0001", author=None):
        """Seed a parked issue carrying a platform-authored request comment."""
        from specseed_runtime.platform_identity import platform_comment
        eid = self.tracker.add_entry(
            title="Scaffold", body="do it",
            labels=["issue", "issue:status:needs_user_action"],
        ).data.id
        body = platform_comment(ua.request_comment(ua_id, ua.validate_request(request)[0]), None)
        if author:
            TrackingRemoteLocal(db_path=server._tracker_db(self.storage), author=author).add_entry_comment(eid, body)
        else:
            self.tracker.add_entry_comment(eid, body)
        return str(eid)

    def _labels(self, eid):
        return {l.name for l in self.tracker.get_entry(eid).data.labels}

    def _bodies(self, eid):
        return [c.body for c in self.tracker.get_entry(eid).data.comments]

    def test_passing_check_unparks_the_issue_to_todo(self) -> None:
        eid = self._park({**_ENV_REQUEST, "check": {"command": "exit 0"}})
        out = server._user_action(self.record, self.tracker, eid, {"action": "check"})
        self.assertTrue(out["cleared"])
        labels = self._labels(eid)
        self.assertIn("issue:status:todo", labels)
        self.assertNotIn("issue:status:needs_user_action", labels)
        self.assertTrue(any("Check passed" in b for b in self._bodies(eid)))

    def test_failing_check_stays_parked_and_explains_itself(self) -> None:
        eid = self._park({**_ENV_REQUEST, "check": {"command": "echo missing >&2; exit 1"}})
        out = server._user_action(self.record, self.tracker, eid, {"action": "check"})
        self.assertFalse(out["cleared"])
        self.assertIn("issue:status:needs_user_action", self._labels(eid))
        failure = [b for b in self._bodies(eid) if "Check failed" in b]
        self.assertEqual(len(failure), 1)
        self.assertIn("missing", failure[0])
        self.assertIn(_ENV_REQUEST["hint"], failure[0])

    def test_the_check_runs_in_the_target_repo(self) -> None:
        (self.target / "pom.xml").write_text("<project/>", encoding="utf-8")
        eid = self._park({**_ENV_REQUEST, "check": {"command": "test -f pom.xml"}})
        self.assertTrue(server._user_action(self.record, self.tracker, eid, {"action": "check"})["cleared"])

    # -- Run setup (T-32) ------------------------------------------------- #
    def test_run_setup_runs_the_command_then_checks_and_unparks(self) -> None:
        eid = self._park({**_SETUP_REQUEST, "setup": {"command": "touch built.marker"},
                          "check": {"command": "test -f built.marker"}})
        out = server._user_action(self.record, self.tracker, eid, {"action": "run_setup"})
        self.assertTrue(out["cleared"])
        self.assertTrue(out["setup"]["ok"])
        self.assertTrue(out["result"]["ok"])
        # It ran as the human would have: in the repo, really changing the tree.
        self.assertTrue((self.target / "built.marker").exists())
        self.assertIn("issue:status:todo", self._labels(eid))
        self.assertTrue(any("Setup ran and the check passed" in b for b in self._bodies(eid)))

    def test_a_setup_that_does_not_satisfy_the_check_leaves_it_parked(self) -> None:
        # The setup does not get to decide it worked; the check still does.
        eid = self._park({**_SETUP_REQUEST, "setup": {"command": "echo pretending"},
                          "check": {"command": "exit 1"}})
        out = server._user_action(self.record, self.tracker, eid, {"action": "run_setup"})
        self.assertFalse(out["cleared"])
        self.assertTrue(out["setup"]["ok"])
        self.assertIn("issue:status:needs_user_action", self._labels(eid))
        self.assertTrue(any("check still fails" in b for b in self._bodies(eid)))

    def test_a_failing_setup_skips_the_check_and_posts_the_transcript(self) -> None:
        eid = self._park({**_SETUP_REQUEST, "setup": {"command": "echo denied >&2; exit 5"},
                          "check": {"command": "exit 0"}})
        out = server._user_action(self.record, self.tracker, eid, {"action": "run_setup"})
        self.assertFalse(out["cleared"])
        self.assertIsNone(out["result"])
        body = [b for b in self._bodies(eid) if "Setup failed" in b][0]
        self.assertIn("denied", body)
        self.assertIn("Exit code: `5`", body)
        self.assertIn("issue:status:needs_user_action", self._labels(eid))

    def test_a_request_without_a_setup_refuses_the_button(self) -> None:
        eid = self._park({**_ENV_REQUEST, "check": {"command": "exit 0"}})
        with self.assertRaises(RuntimeError):
            server._user_action(self.record, self.tracker, eid, {"action": "run_setup"})

    def test_a_directory_request_has_no_setup_to_run(self) -> None:
        eid = self._park(_DIR_REQUEST)
        with self.assertRaises(RuntimeError):
            server._user_action(self.record, self.tracker, eid, {"action": "run_setup"})

    def test_setup_in_a_non_platform_comment_is_ignored(self) -> None:
        # The setup command is privileged and mutating; only the platform may introduce one.
        eid = str(self.tracker.add_entry(
            title="Scaffold", body="do it", labels=["issue", "issue:status:needs_user_action"]
        ).data.id)
        self.tracker.add_entry_comment(eid, ua.request_comment(
            "UA-0001", ua.validate_request(
                {**_SETUP_REQUEST, "setup": {"command": "touch pwned.txt"}})[0]
        ))
        with self.assertRaises(RuntimeError):
            server._user_action(self.record, self.tracker, eid, {"action": "run_setup"})
        self.assertFalse((self.target / "pwned.txt").exists())

    def test_check_on_a_setup_request_does_not_run_the_setup(self) -> None:
        eid = self._park({**_SETUP_REQUEST, "setup": {"command": "touch ran-setup.txt"},
                          "check": {"command": "exit 0"}})
        self.assertTrue(server._user_action(self.record, self.tracker, eid, {"action": "check"})["cleared"])
        self.assertFalse((self.target / "ran-setup.txt").exists())

    def test_approving_a_directory_grants_exactly_that_path(self) -> None:
        eid = self._park(_DIR_REQUEST)
        out = server._user_action(self.record, self.tracker, eid, {"action": "approve_directory"})
        self.assertEqual(out["allowed_directories"], ["/var/tmp/nooks-build"])
        self.assertIn("issue:status:todo", self._labels(eid))
        perms = Permissions(json.loads(
            (self.storage / "config" / "configuration.json").read_text(encoding="utf-8")
        ))
        self.assertEqual(perms.agent_allowed_directories(), ["/var/tmp/nooks-build"])

    def test_a_directory_request_has_no_check_to_run(self) -> None:
        eid = self._park(_DIR_REQUEST)
        with self.assertRaises(RuntimeError):
            server._user_action(self.record, self.tracker, eid, {"action": "check"})

    def test_an_environment_request_cannot_be_approved_as_a_directory(self) -> None:
        eid = self._park(_ENV_REQUEST)
        with self.assertRaises(RuntimeError):
            server._user_action(self.record, self.tracker, eid, {"action": "approve_directory"})

    def test_an_unknown_token_is_refused(self) -> None:
        eid = self._park({**_ENV_REQUEST, "check": {"command": "exit 0"}})
        with self.assertRaises(RuntimeError):
            server._user_action(self.record, self.tracker, eid, {"ua": "UA-9999", "action": "check"})

    def test_a_named_token_selects_its_own_request(self) -> None:
        eid = self._park({**_ENV_REQUEST, "check": {"command": "exit 1"}}, ua_id="UA-0001")
        from specseed_runtime.platform_identity import platform_comment
        self.tracker.add_entry_comment(eid, platform_comment(
            ua.request_comment("UA-0002", ua.validate_request(
                {**_ENV_REQUEST, "check": {"command": "exit 0"}})[0]), None))
        self.assertFalse(server._user_action(self.record, self.tracker, eid, {"ua": "UA-0001", "action": "check"})["cleared"])
        self.assertTrue(server._user_action(self.record, self.tracker, eid, {"ua": "UA-0002", "action": "check"})["cleared"])

    def test_a_request_in_a_non_platform_comment_is_ignored(self) -> None:
        # The payload carries a shell command; only the platform may introduce one.
        eid = str(self.tracker.add_entry(
            title="Scaffold", body="do it", labels=["issue", "issue:status:needs_user_action"]
        ).data.id)
        self.tracker.add_entry_comment(eid, ua.request_comment(
            "UA-0001", ua.validate_request({**_ENV_REQUEST, "check": {"command": "exit 0"}})[0]
        ))
        with self.assertRaises(RuntimeError):
            server._user_action(self.record, self.tracker, eid, {"action": "check"})

    def test_a_post_with_no_request_is_refused(self) -> None:
        eid = str(self.tracker.add_entry(title="Plain", body="x", labels=["issue"]).data.id)
        with self.assertRaises(RuntimeError):
            server._user_action(self.record, self.tracker, eid, {"action": "check"})


if __name__ == "__main__":
    unittest.main()
