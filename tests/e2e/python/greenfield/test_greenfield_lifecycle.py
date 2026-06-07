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
3. two discussion rounds (worker parks ``awaiting_approval``; answers wake it).
4. sprint-1 breakdown: 2 epics, 4 tickets, 4 gated issues + APR-0001; spec docs.
5. approve the request (settles spec docs, request -> done).
6. approve each sprint-1 issue (thumbs-up and ``approve APR-0001``); each runs
   implement -> review -> done; one issue fails review once first; roll-up
   closes tickets + epic.
7. a manual ``todo`` hotfix issue parks for implement approval
   (``auto_implement_issue`` off), is approved by id comment, completes.
8. ``spec-change:plan-next-sprint`` post: sprint-2 issues + APR-0002, approve,
   complete; second epic rolls up; SCHEDULE shows sprint 2 ongoing.
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

DRAFT_TITLE = "Draft: describe what you want specseed to do"
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


def _approve_issue_batch(harness: Greenfield, boss, titles: list[str], apr_id: str) -> dict[str, object]:
    """Approve a gated issue batch: half by thumbs-up, half by APR comment.

    Returns {title: post_id}. Waits for the gated state first so the approval
    lands on an `awaiting_approval` issue, then for every issue to finish
    (implement -> review -> done, closed).
    """
    ids: dict[str, object] = {}
    for title in titles:
        post = harness.wait_for(lambda t=title: find_post(boss, t), f"issue post {title!r}")
        ids[title] = post.id
        harness.wait_for(
            lambda pid=post.id: "issue:status:awaiting_approval" in labels_of(boss, pid),
            f"issue {title!r} born awaiting_approval",
        )
        harness.wait_for(
            lambda pid=post.id: comment_with(boss, pid, apr_id),
            f"issue {title!r} carries the {apr_id} gate comment",
        )

    for index, title in enumerate(titles):
        if index % 2 == 0:
            _ok(boss.add_entry_reaction(ids[title], "thumbs_up"), f"thumbs up {title!r}")
        else:
            _ok(boss.add_entry_comment(ids[title], f"approve {apr_id}"), f"approve {title!r}")

    for title in titles:
        harness.wait_for(
            lambda pid=ids[title]: entry_done(boss, pid, "issue"),
            f"issue {title!r} implemented, reviewed and closed",
        )
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
        lambda: "spec-change:status:awaiting_approval" in labels_of(boss, request_id),
        "request parked awaiting_approval after round 1",
    )
    _ok(boss.add_entry_comment(request_id, "1. OK. One JSON file is plenty."), "answer round 1")

    # ---- 4. discussion round 2 ------------------------------------------ #
    harness.wait_for(lambda: comment_with(boss, request_id, "Round 2"), "round 2 question")
    _ok(boss.add_entry_comment(request_id, "OK"), "answer round 2")

    # ---- 5. sprint-1 breakdown lands ------------------------------------ #
    harness.wait_for(lambda: comment_with(boss, request_id, "APR-0001"), "APR-0001 request")
    for title in [epic["title"] for epic in SCRIPT["epics"]] + _ticket_titles(1) + _ticket_titles(2):
        harness.wait_for(lambda t=title: find_post(boss, t), f"work post {title!r}")
    epic1_id = find_post(boss, SCRIPT["epics"][0]["title"]).id
    epic2_id = find_post(boss, SCRIPT["epics"][1]["title"]).id
    assert "epic:status:todo" in labels_of(boss, epic1_id)
    spec_dir = harness.target / ".specseed" / "spec"
    assert (spec_dir / "vision.md").is_file() and (spec_dir / "srs.md").is_file()
    schedule_id = find_post(boss, "SCHEDULE").id
    harness.wait_for(
        lambda: "Core sprint  (ongoing)" in (details_of(boss, schedule_id).body or ""),
        "SCHEDULE lists sprint 1 as ongoing",
    )

    # ---- 6. approve the request: spec settles, request -> done ----------- #
    _ok(boss.add_entry_reaction(request_id, "thumbs_up"), "approve spec-change request")
    harness.wait_for(
        lambda: "spec-change:status:done" in labels_of(boss, request_id),
        "request settled to done",
    )
    harness.wait_for(lambda: comment_with(boss, request_id, "Spec settled"),
                     "settle note on the request")
    vision = (spec_dir / "vision.md").read_text(encoding="utf-8")
    assert "settled: true" in vision and "settled_at:" in vision

    # ---- 7. approve + complete the four sprint-1 issues ------------------ #
    sprint1_ids = _approve_issue_batch(harness, boss, _issue_titles(1), "APR-0001")

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
    _ok(boss.add_entry_comment(hotfix_id, f"approve {hotfix_id}"), "approve hotfix by id")
    harness.wait_for(lambda: entry_done(boss, hotfix_id, "issue"), "hotfix done")

    # ---- 9. sprint 2 via plan-next-sprint -------------------------------- #
    sprint2_request = _ok(
        boss.add_entry(
            "Plan sprint 2",
            body="Sprint 1 shipped. Plan the workflow sprint next.",
            labels=["spec-change:plan-next-sprint", "spec-change:status:open"],
        ),
        "create plan-next-sprint request",
    ).id
    harness.wait_for(lambda: comment_with(boss, sprint2_request, "APR-0002"), "APR-0002 request")
    _ok(boss.add_entry_reaction(sprint2_request, "thumbs_up"), "approve sprint-2 request")
    harness.wait_for(
        lambda: "spec-change:status:done" in labels_of(boss, sprint2_request),
        "sprint-2 request done",
    )
    harness.wait_for(
        lambda: "Workflow sprint  (ongoing)" in (details_of(boss, schedule_id).body or ""),
        "SCHEDULE flips sprint 2 to ongoing",
    )

    _approve_issue_batch(harness, boss, _issue_titles(2), "APR-0002")
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
        lambda: comment_with(boss, control_id, "specseed scheduler STATUS", author="remote"),
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
