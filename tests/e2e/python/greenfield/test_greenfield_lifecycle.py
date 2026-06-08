"""Greenfield end-to-end lifecycle: bootstrap -> spec -> sprints -> done.

Opt-in, slow: ``python3 -m pytest tests/e2e/python -m e2e``.

User perspective only. The test drives exactly what a human can do:

* the ``src/specseed`` CLI (``configure``, ``run``), and
* the remote-local tracker (``TrackingRemoteLocal``, author ``boss``) - the same
  seam the ``remote_local --ui`` command edits.

Everything else happens inside the listener subprocess. Agent runs are real
subprocesses too: a scripted ``claude`` stand-in (``data/fake_agent/claude``) is
first on PATH, so no tokens are ever spent and replies are deterministic.

Scenario (every approval step covered):

1. configure a fresh target repo; start the listener (it seeds labels + posts).
2. fill the seeded draft adapt post with a bootstrap prompt, drop ``draft``.
3. two discussion rounds (worker parks ``awaiting_input``; answers wake it).
4. sprint-1 breakdown is PROPOSED (plan-first, runtime-owned gate): the worker only
   STAGES the spec + writes plan.json/apply.py; the runtime posts the plan summary
   + APR-0001 and parks the request ``awaiting_approval`` - NO work posts, live spec
   untouched.
5. approve the request on its APR comment (promotes the staged spec into live spec/,
   settles it, request -> done); only now does the deferred apply.py create the
   2 epics, 4 tickets, 4 sprint-1 issues (born todo).
6. each sprint-1 issue passes two gates approved on the gate COMMENT: implement
   (``auto_implement_issue`` off) then merge (``merge_to_primary`` off). Issues merge
   in dependency order; one fails review once first; roll-up closes tickets + epic.
7. a manual ``todo`` hotfix issue runs the same implement + merge gate sequence.
8. ``spec-change:plan-next-sprint`` post: propose + APR (number floats, gates mint
   APR ids), approve on the APR comment (apply creates sprint-2 issues todo),
   drive each issue to done; second epic rolls up; SCHEDULE shows sprint 2 ongoing.
9. CONTROL: STATUS gets a reply; STOP shuts the listener down cleanly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal

pytestmark = pytest.mark.e2e

ENGINE_ROOT = Path(__file__).resolve().parents[4]
SPECSEED_CLI = ENGINE_ROOT / "src" / "specseed"
DATA = Path(__file__).resolve().parent / "data"
SCRIPT = json.loads((DATA / "fake_agent" / "script.json").read_text(encoding="utf-8"))

DRAFT_TITLE = "Describe what you want specseed to do"
BOOTSTRAP_BODY = (
    "# Bootstrap: taskling\n\n"
    "Build a tiny todo CLI: capture tasks, list them, mark done, remove. "
    "Stdlib only, no daemon. Spec it and break the work down."
)

WAIT_S = 240.0
POLL_S = 0.25


# --------------------------------------------------------------------------- #
# user-side read helpers (all through the remote tracker, like the UI would)
# --------------------------------------------------------------------------- #
def _ok(result, what: str):
    assert getattr(result, "ok", False), f"{what} failed: {getattr(result, 'error', '?')}"
    return result.data


def find_post(boss, title: str):
    for summary in _ok(boss.list_entries(is_open=None), "list entries"):
        if summary.title == title:
            return summary
    return None


def details_of(boss, post_id):
    return _ok(boss.get_entry(post_id), f"get entry {post_id}")


def labels_of(boss, post_id) -> set[str]:
    return {label.name for label in details_of(boss, post_id).labels}


def comments_of(boss, post_id) -> list:
    return list(details_of(boss, post_id).comments or [])


def comment_with(boss, post_id, text: str, author: str | None = None):
    for comment in comments_of(boss, post_id):
        if text in (comment.body or "") and (author is None or comment.author == author):
            return comment
    return None


def approve_apr(harness, boss, request_id, desc: str):
    """Approve a spec-change request by reacting 👍 on its APR request COMMENT.

    The APR plan gate counts reactions on the latest approval-request COMMENT, not
    on the post (a standing post reaction can't re-fire a superseded gate). So a
    human approves by reacting on that comment, exactly like the UI gate button.
    """
    comment = harness.wait_for(
        lambda: comment_with(boss, request_id, "approval-request"),
        f"APR request comment on {desc}",
    )
    _ok(boss.add_entry_comment_reaction(request_id, comment.id, "thumbs_up"),
        f"approve {desc} via APR comment")


def entry_done(boss, post_id, tier: str) -> bool:
    data = details_of(boss, post_id)
    names = {label.name for label in data.labels}
    return f"{tier}:status:done" in names and not data.is_open


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
class Greenfield:
    """One configured target repo + the running listener + log access."""

    def __init__(self, tmp_path: Path) -> None:
        self.target = tmp_path / "taskling"
        shutil.copytree(DATA / "target_repo", self.target)
        subprocess.run(["git", "init", "-q"], cwd=self.target, check=True)
        self.storage = self.target / ".specseed" / "storage"
        self.env = dict(os.environ)
        self.env["PATH"] = f"{DATA / 'fake_agent'}{os.pathsep}{self.env.get('PATH', '')}"
        self.run_log = tmp_path / "specseed_run.log"
        self.proc: subprocess.Popen | None = None
        self._log_fh = None

    def configure(self) -> None:
        result = subprocess.run(
            [
                str(SPECSEED_CLI), "configure",
                "--target", str(self.target),
                "--defaults",
                "--use-config-file", str(DATA / "configuration.json"),
            ],
            capture_output=True, text=True, env=self.env, timeout=60,
        )
        assert result.returncode == 0, f"configure failed:\n{result.stdout}\n{result.stderr}"

    def start(self) -> None:
        self._log_fh = self.run_log.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [
                str(SPECSEED_CLI), "run",
                "--target", str(self.target),
                "--interval", "0.5",
            ],
            stdout=self._log_fh, stderr=subprocess.STDOUT, env=self.env, text=True,
        )

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None

    def boss(self) -> TrackingRemoteLocal:
        """The human operator: same seam `specseed remote_local --ui` edits."""
        return TrackingRemoteLocal(
            db_path=self.storage / "tracking_remote_local.db", author="boss"
        )

    # -- diagnostics ----------------------------------------------------- #
    def _tail(self, path: Path, lines: int = 40) -> str:
        try:
            content = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return f"  ({path.name} missing)"
        return "\n".join("  " + line for line in content[-lines:])

    def diagnostics(self) -> str:
        return (
            f"\n--- listener stdout tail ({self.run_log}) ---\n{self._tail(self.run_log)}"
            f"\n--- platform.log tail ---\n{self._tail(self.storage / 'platform.log')}"
            f"\n--- fake_agent.log tail ---\n{self._tail(self.storage / 'fake_agent.log')}"
        )

    def wait_for(self, condition, desc: str, timeout: float = WAIT_S):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            if self.proc is not None and self.proc.poll() is not None:
                pytest.fail(
                    f"listener exited (rc={self.proc.returncode}) while waiting for: "
                    f"{desc}{self.diagnostics()}"
                )
            time.sleep(POLL_S)
        pytest.fail(f"timed out waiting for: {desc}{self.diagnostics()}")


@pytest.fixture
def harness(tmp_path):
    instance = Greenfield(tmp_path)
    try:
        yield instance
    finally:
        instance.stop()


# --------------------------------------------------------------------------- #
# scenario helpers
# --------------------------------------------------------------------------- #
def _issue_titles(sprint: int) -> list[str]:
    return [issue["title"] for issue in SCRIPT["issues"] if issue["sprint"] == sprint]


def _ticket_titles(sprint: int) -> list[str]:
    return [ticket["title"] for ticket in SCRIPT["tickets"] if ticket["sprint"] == sprint]


def _approve_gate_comments(boss, post_id, reacted: set) -> None:
    """React 👍 on every approval-request COMMENT on this post not yet acted on.

    One helper covers each gate an issue passes through - the implement-approval gate
    AND the merge gate - because both are a fresh APR-marker comment, and a
    re-prepared merge gate is a new comment with no reaction. Reacting on the comment
    (not the post) is how a gate is approved, exactly like the UI gate button.
    """
    for comment in comments_of(boss, post_id):
        if "approval-request" in (comment.body or "") and comment.id not in reacted:
            _ok(boss.add_entry_comment_reaction(post_id, comment.id, "thumbs_up"),
                f"approve gate comment {comment.id} on post {post_id}")
            reacted.add(comment.id)


def _drive_issues_to_done(harness: Greenfield, boss, ids: dict, desc: str) -> None:
    """Approve every gate for these issues until the whole batch is done (on primary).

    `auto_implement_issue` off and `merge_to_primary` off, so each issue passes an
    implement-approval gate and then a merge gate. Issues with `Depends on:` links
    implement and merge in dependency order (a dependent is held until its dep merges
    to primary), so we can't approve up front - we poll, approving whichever gate each
    issue currently presents, until every issue is done.
    """
    reacted: set = set()
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        for pid in ids.values():
            _approve_gate_comments(boss, pid, reacted)
        if all(entry_done(boss, pid, "issue") for pid in ids.values()):
            return
        if harness.proc is not None and harness.proc.poll() is not None:
            pytest.fail(f"listener exited while driving {desc}{harness.diagnostics()}")
        time.sleep(POLL_S)
    not_done = [str(pid) for pid in ids.values() if not entry_done(boss, pid, "issue")]
    pytest.fail(f"timed out driving {desc} to done; still open: {not_done}{harness.diagnostics()}")


def _approve_issue_batch(harness: Greenfield, boss, titles: list[str]) -> dict[str, object]:
    """Drive a batch of issues through implement + merge gates to done.

    Plan-first: issues are created `todo` and the runtime parks each
    `awaiting_approval` because `auto_implement_issue` is off. The human approves on
    the gate COMMENT (not the post); after implement + review each issue readies a
    merge gate (merge_to_primary off), approved the same way, then merges to primary
    and closes. Returns {title: post_id}.
    """
    ids: dict[str, object] = {}
    for title in titles:
        post = harness.wait_for(lambda t=title: find_post(boss, t), f"issue post {title!r}")
        ids[title] = post.id
        harness.wait_for(
            lambda pid=post.id: "issue:status:awaiting_approval" in labels_of(boss, pid),
            f"issue {title!r} parked awaiting implement approval",
        )
        harness.wait_for(
            lambda pid=post.id: comment_with(boss, pid, "requires human approval"),
            f"issue {title!r} carries the implement-approval note",
        )
    _drive_issues_to_done(harness, boss, ids, f"sprint issues {titles}")
    return ids


# --------------------------------------------------------------------------- #
# the scenario
# --------------------------------------------------------------------------- #
def test_greenfield_full_lifecycle(harness):
    harness.configure()
    config = json.loads((harness.storage / "configuration.json").read_text(encoding="utf-8"))
    assert config["approvals"]["approver_usernames"] == ["boss"]
    assert config["review"]["enabled"] is True
    assert config["permissions"]["platform"]["auto_implement_issue"] is False
    # The runtime authors its tracker comments as the configured platform username
    # (configure defaults it to "specseed" when none is given).
    platform_user = config.get("platform_username") or "remote"

    harness.start()
    boss = harness.boss()

    # ---- 1. seeded remote: dashboards + the draft adapt post ------------- #
    draft = harness.wait_for(lambda: find_post(boss, DRAFT_TITLE), "seeded draft adapt post")
    request_id = draft.id
    for title in ("CONTROL", "ROADMAP", "SCHEDULE", "CURRENT SPRINT"):
        assert find_post(boss, title), f"seeded post {title!r} missing"

    # ---- 2. bootstrap prompt: fill the draft, drop the draft label ------- #
    _ok(boss.edit_entry(request_id, title="Bootstrap: taskling todo CLI", body=BOOTSTRAP_BODY),
        "write bootstrap prompt")
    _ok(boss.remove_entry_label(request_id, "draft"), "drop draft label")

    # ---- 3. discussion round 1 ------------------------------------------ #
    harness.wait_for(lambda: comment_with(boss, request_id, "Round 1"), "round 1 question")
    assert (harness.storage / "fake_agent.log").is_file(), (
        "fake agent log missing: a real agent CLI may have run!" + harness.diagnostics()
    )
    harness.wait_for(
        lambda: "spec-change:status:awaiting_input" in labels_of(boss, request_id),
        "request parked awaiting_input after round 1",
    )
    _ok(boss.add_entry_comment(request_id, "1. OK. One JSON file is plenty."), "answer round 1")

    # ---- 4. discussion round 2 ------------------------------------------ #
    harness.wait_for(lambda: comment_with(boss, request_id, "Round 2"), "round 2 question")
    _ok(boss.add_entry_comment(request_id, "OK"), "answer round 2")

    # ---- 5. sprint-1 breakdown is PROPOSED (plan-first: nothing created yet) #
    harness.wait_for(lambda: comment_with(boss, request_id, "APR-0001"), "APR-0001 request")
    harness.wait_for(
        lambda: "spec-change:status:awaiting_approval" in labels_of(boss, request_id),
        "request parked awaiting_approval after breakdown",
    )
    # The plan only proposes work; the epics/tickets/issues do NOT exist until the
    # human approves the request below.
    assert find_post(boss, SCRIPT["epics"][0]["title"]) is None, "no work created before approval"
    # Plan-first staging: the spec edits are STAGED, not written to live spec/. Live
    # spec/ stays untouched until approval, so a buggy/unapproved run can't corrupt it.
    spec_dir = harness.target / ".specseed" / "spec"
    assert not (spec_dir / "vision.md").exists(), "spec must NOT land in live spec/ before approval"

    # ---- 6. approve the request: spec promotes + settles, apply creates the work #
    approve_apr(harness, boss, request_id, "spec-change request")
    harness.wait_for(
        lambda: "spec-change:status:done" in labels_of(boss, request_id),
        "request settled to done",
    )
    harness.wait_for(lambda: comment_with(boss, request_id, "Spec promoted"),
                     "promote+settle note on the request")
    # Promotion landed the staged docs into live spec/ on approval.
    assert (spec_dir / "vision.md").is_file() and (spec_dir / "srs.md").is_file()
    vision = (spec_dir / "vision.md").read_text(encoding="utf-8")
    assert "settled: true" in vision and "settled_at:" in vision
    # Now the deferred apply.py has created the breakdown.
    for title in [epic["title"] for epic in SCRIPT["epics"]] + _ticket_titles(1) + _ticket_titles(2):
        harness.wait_for(lambda t=title: find_post(boss, t), f"work post {title!r}")
    epic1_id = find_post(boss, SCRIPT["epics"][0]["title"]).id
    epic2_id = find_post(boss, SCRIPT["epics"][1]["title"]).id
    assert "epic:status:todo" in labels_of(boss, epic1_id)
    schedule_id = find_post(boss, "SCHEDULE").id
    harness.wait_for(
        lambda: "Core sprint  (ongoing)" in (details_of(boss, schedule_id).body or ""),
        "SCHEDULE lists sprint 1 as ongoing",
    )

    # ---- 7. approve + complete the four sprint-1 issues ------------------ #
    sprint1_ids = _approve_issue_batch(harness, boss, _issue_titles(1))

    # the flaky issue went changes -> reimplement -> approve (two reviews)
    flaky_id = sprint1_ids[SCRIPT["review"]["flaky_title"]]
    reviews = [c for c in comments_of(boss, flaky_id) if "**Code review**" in (c.body or "")]
    assert len(reviews) == 2, f"expected 2 review attempts, saw {len(reviews)}"
    assert "verdict `changes`" in reviews[0].body
    assert "verdict `approve`" in reviews[1].body
    smooth_id = sprint1_ids[_issue_titles(1)[1]]
    smooth_reviews = [c for c in comments_of(boss, smooth_id) if "**Code review**" in (c.body or "")]
    assert len(smooth_reviews) == 1

    # roll-up: sprint-1 tickets close, then their epic
    for title in _ticket_titles(1):
        ticket_id = find_post(boss, title).id
        harness.wait_for(lambda pid=ticket_id: entry_done(boss, pid, "ticket"),
                         f"ticket {title!r} rolled up to done")
    harness.wait_for(lambda: entry_done(boss, epic1_id, "epic"), "epic 1 rolled up to done")
    assert not entry_done(boss, epic2_id, "epic"), "epic 2 must stay open (no issues yet)"

    # ---- 8. manual hotfix issue: implement-approval gate ------------------ #
    hotfix_id = _ok(
        boss.add_entry(
            "Hotfix: crash on empty list",
            body="Steps: run list with no task file present. Fix and guard it.",
            labels=["issue", "issue:status:todo", "type:bug"],
        ),
        "create hotfix issue",
    ).id
    harness.wait_for(
        lambda: "issue:status:awaiting_approval" in labels_of(boss, hotfix_id),
        "hotfix parked for implement approval",
    )
    harness.wait_for(lambda: comment_with(boss, hotfix_id, "requires human approval"),
                     "hotfix approval-needed note")
    # Same gate sequence as any issue: implement gate + merge gate, approved on the
    # comment, then merged to primary and closed.
    _drive_issues_to_done(harness, boss, {"hotfix": hotfix_id}, "hotfix issue")

    # ---- 9. sprint 2 via plan-next-sprint -------------------------------- #
    sprint2_request = _ok(
        boss.add_entry(
            "Plan sprint 2",
            body="Sprint 1 shipped. Plan the workflow sprint next.",
            labels=["spec-change:plan-next-sprint", "spec-change:status:open"],
        ),
        "create plan-next-sprint request",
    ).id
    # The APR number is NOT 0002: implement + merge gates mint APR ids from the same
    # monotonic counter, so the plan-next-sprint APR is whatever comes next. Wait on
    # the parked state, then approve on the APR comment by marker (not by number).
    harness.wait_for(
        lambda: "spec-change:status:awaiting_approval" in labels_of(boss, sprint2_request),
        "sprint-2 request parked awaiting approval",
    )
    approve_apr(harness, boss, sprint2_request, "sprint-2 request")
    harness.wait_for(
        lambda: "spec-change:status:done" in labels_of(boss, sprint2_request),
        "sprint-2 request done",
    )
    harness.wait_for(
        lambda: "Workflow sprint  (ongoing)" in (details_of(boss, schedule_id).body or ""),
        "SCHEDULE flips sprint 2 to ongoing",
    )

    _approve_issue_batch(harness, boss, _issue_titles(2))
    for title in _ticket_titles(2):
        ticket_id = find_post(boss, title).id
        harness.wait_for(lambda pid=ticket_id: entry_done(boss, pid, "ticket"),
                         f"ticket {title!r} rolled up to done")
    harness.wait_for(lambda: entry_done(boss, epic2_id, "epic"), "epic 2 rolled up to done")

    # ---- 10. runtime dashboards reflect the finished tree ----------------- #
    roadmap_id = find_post(boss, "ROADMAP").id
    harness.wait_for(
        lambda: SCRIPT["epics"][0]["title"] in (details_of(boss, roadmap_id).body or ""),
        "ROADMAP lists the epics",
    )
    sprint_board_id = find_post(boss, "CURRENT SPRINT").id
    harness.wait_for(
        lambda: "No active issues" in (details_of(boss, sprint_board_id).body or ""),
        "CURRENT SPRINT board drains",
    )

    # ---- 11. CONTROL: STATUS reply, then STOP ----------------------------- #
    control_id = find_post(boss, "CONTROL").id
    _ok(boss.add_entry_comment(control_id, "STATUS"), "ask STATUS")
    harness.wait_for(
        lambda: comment_with(boss, control_id, "specseed scheduler STATUS", author=platform_user),
        "STATUS reply on CONTROL",
    )
    _ok(boss.add_entry_comment(control_id, "STOP"), "STOP the listener")
    try:
        harness.proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail("listener did not exit on STOP" + harness.diagnostics())
    assert harness.proc.returncode == 0, (
        f"listener exit rc={harness.proc.returncode}" + harness.diagnostics()
    )

    # every agent job ran through the scripted stand-in, never a real CLI
    agent_log = (harness.storage / "fake_agent.log").read_text(encoding="utf-8")
    kinds = {json.loads(line)["kind"] for line in agent_log.splitlines() if line.strip()}
    assert kinds == {"spec", "implement", "review"}
