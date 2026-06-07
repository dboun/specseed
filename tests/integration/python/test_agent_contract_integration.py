"""End-to-end agent-contract flow through the Scheduler.

Real flow: TrackingRemoteLocal (truth) -> sync -> dispatch -> advance, driven by
scheduler.run_once with a scripted FakeAgentRunner. Exercises the post-incident
contract: the review summary that lands on the tracker comes from the structured
report (not head-truncated stdout); a blocked implement parks `blocked` instead of
sailing into review; a provider-quota result requeues WITHOUT spawning a
platform_error post or a resolver run. No agents, no network, no tokens.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from specseed_runtime.db.database import Database
from specseed_runtime.executing import cancellation
from specseed_runtime.executing.agent_runner import AgentResult, FakeAgentRunner
from specseed_runtime.executing.recovery import PLATFORM_ERROR_LABEL
from specseed_runtime.executing.scheduler import Scheduler
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal
from specseed_runtime.entities import issue as _issue  # noqa: F401


pytestmark = pytest.mark.integration


class Harness:
    def __init__(self, root: Path, runner, review) -> None:
        cancellation.reset()
        self.remote = TrackingRemoteLocal(db_path=root / "remote.db", author="alice")
        self.local = TrackingLocal(db_path=root / "local.db", author="agent")
        self.db = Database(db_path=root / "queue.db")
        cfg = {
            "specseed_dir": "seedmeta",
            "approvals": {"approver_usernames": ["alice"]},
            "permissions": {},
            "review": review,
        }
        self.sched = Scheduler(
            db=self.db, runner=runner, config=cfg, storage=root, repo_root=root,
            remote=self.remote, local=self.local, poll_interval=0,
        )
        for lbl in ("issue", "issue:status:todo"):
            self.remote.create_label(lbl)
        self.eid = self.remote.add_entry("Add greeting", labels=["issue", "issue:status:todo"]).data.id

    def drain(self, passes: int) -> None:
        for _ in range(passes):
            self.sched.run_once()

    def status(self):
        d = self.remote.get_entry(self.eid).data
        st = next((l.name.split(":status:")[1] for l in d.labels if ":status:" in l.name), None)
        return st, d.is_open

    def comments(self):
        return [c.body for c in self.remote.get_entry(self.eid).data.comments]

    def error_posts(self):
        listed = self.remote.list_entries(is_open=None, labels=[PLATFORM_ERROR_LABEL])
        return list(getattr(listed, "data", None) or [])


def _scripted(implement_report, review_report=None):
    def side_effect(call):
        if "reviewing completed work" in call["prompt"]:
            return AgentResult(ok=True, returncode=0,
                               stdout="banner noise first\nactual findings last",
                               report=review_report)
        return AgentResult(ok=True, returncode=0, report=implement_report)
    return FakeAgentRunner(side_effect=side_effect)


def test_review_comment_carries_report_summary_not_banner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(
            Path(tmp),
            _scripted(
                {"status": "done", "summary": "implemented", "files_changed": []},
                {"verdict": "approve", "confidence": 0.99, "summary": "PARSER LGTM, criteria met"},
            ),
            {"enabled": True, "confidence_threshold": 0.95},
        )
        h.drain(6)
        st, is_open = h.status()
        assert st == "done" and is_open is False
        joined = "\n".join(h.comments())
        assert "PARSER LGTM, criteria met" in joined   # report.summary landed
        assert "banner noise first" not in joined       # raw stdout did not


def test_blocked_implement_parks_blocked_not_review() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(
            Path(tmp),
            _scripted({"status": "blocked", "summary": "could not write: sandbox"}),
            {"enabled": True, "confidence_threshold": 0.95},
        )
        h.drain(4)
        st, _ = h.status()
        assert st == "blocked"
        assert any("could not write: sandbox" in c for c in h.comments())


def test_quota_requeues_without_error_post() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        quota_runner = FakeAgentRunner(result=AgentResult(
            ok=False, returncode=1, error="You've hit your usage limit. try again in 15 minutes",
            quota_exhausted=True, quota_reset_hint=None,
        ))
        h = Harness(Path(tmp), quota_runner, {"enabled": False})
        h.drain(2)
        st, _ = h.status()
        assert st == "todo"                     # not advanced
        assert h.error_posts() == []            # no platform_error spam
        assert h.sched._quota_paused_until > 0  # circuit is open
