"""End-to-end test of the 0.20.0 skill->runtime wiring (no agent, no tokens).

The 0.20.0 rewiring (plan in `.playground/wiring_plan.md`) made every agent run
build its prompt from the assembled skill bundle (`generate_prompt_from_skill`)
plus thin execution facts, killed the target-root CLAUDE.md/AGENTS.md router
block, and turned the old `question` label into the read-only `ask` route.

This drives the wired path through the real Scheduler with a scripted
FakeAgentRunner that NEVER spawns a subprocess: it records the prompt it is
handed and returns a canned result. The assertions are on the REAL prompt the
runtime built and the REAL state the runtime reached, so a broken wiring fails
the test (wrong route, wrong subroute, missing instruction-file reads, a
read-only route gaining git policy, a spec run not classified as a proposal, a
scaffold writing CLAUDE.md, or a migration losing the marker / file).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from specseed_runtime.configuring import scaffold
from specseed_runtime.db.database import Database
from specseed_runtime.executing import cancellation
from specseed_runtime.executing.agent_runner import AgentResult, FakeAgentRunner
from specseed_runtime.executing.scheduler import Scheduler
from specseed_runtime.migrating import migrate
from specseed_runtime.scheduling.spec_change import spec_change_dir
from specseed_runtime.tracking.populate_defaults import populate_defaults
from specseed_runtime.tracking.tracking_local import TrackingLocal
from specseed_runtime.tracking.tracking_remote_local import TrackingRemoteLocal
from specseed_runtime.entities import issue as _issue  # noqa: F401


pytestmark = pytest.mark.integration


# The runtime resolves the bundle MODE from the tracking provider. The harness
# writes no remote.json, so the local stand-in maps to `specseed-ui` - this is
# the mode every assertion below expects in the prompt header.
_EXPECT_MODE = "specseed-ui"
_SPECSEED_DIR = "seedmeta"


class Harness:
    """A real Scheduler over TrackingRemoteLocal + a recording FakeAgentRunner.

    `runner.calls` captures every prompt the runtime built. `storage == repo_root`
    so spec-change dirs land where the runtime reads them.
    """

    def __init__(self, root: Path, runner: FakeAgentRunner) -> None:
        cancellation.reset()
        (root / ".gitignore").write_text("*.db\n*.db-*\nstorage/\nseedmeta/\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=str(root), capture_output=True, text=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                       cwd=str(root), capture_output=True, text=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "--allow-empty", "-m", "root"],
                       cwd=str(root), capture_output=True, text=True)
        self.root = root
        self.runner = runner
        self.remote = TrackingRemoteLocal(db_path=root / "remote.db", author="alice")
        self.local = TrackingLocal(db_path=root / "local.db", author="agent")
        self.db = Database(db_path=root / "queue.db")
        cfg = {
            "specseed_dir": _SPECSEED_DIR,
            "approvals": {"approver_usernames": ["alice"]},
            "permissions": {},
            "review": {"enabled": False},
        }
        self.sched = Scheduler(
            db=self.db, runner=runner, config=cfg, storage=root, repo_root=root,
            remote=self.remote, local=self.local, poll_interval=0,
        )
        # Seed the real label taxonomy + permanent posts the same way startup does
        # (`run.py` -> populate_defaults for a no-remote.json repo = kind
        # "remote_local"). The test never hand-creates labels: a user attaching the
        # `ask` / `spec-change:adapt` label below picks one the PLATFORM seeded, so a
        # broken seed set (e.g. a missed question->ask rename) would fail the run.
        populate_defaults("remote_local", tracker=self.remote, prune=False)

    def drain(self, passes: int) -> None:
        for _ in range(passes):
            self.sched.run_once()

    def labels(self, eid) -> set[str]:
        return {l.name for l in self.remote.get_entry(eid).data.labels}

    def comments(self, eid) -> list[str]:
        return [c.body for c in self.remote.get_entry(eid).data.comments]

    def prompts_with(self, needle: str) -> list[str]:
        return [c["prompt"] for c in self.runner.calls if needle in c["prompt"]]


# --- ask route: read-only Q&A through the `ask` skill bundle ----------------- #

def test_ask_post_runs_ask_bundle_read_only_and_posts_answer() -> None:
    answer_text = "Use the repository pattern; see the SAD for the data layer."

    def side_effect(call):
        # Only the ask run should reach the agent here; answer read-only.
        assert "Mode: {0}. Route: ask.".format(_EXPECT_MODE) in call["prompt"]
        return AgentResult(
            ok=True, returncode=0,
            report={"status": "answered", "answer": answer_text},
        )

    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(Path(tmp), FakeAgentRunner(side_effect=side_effect))
        eid = h.remote.add_entry("How should I structure storage?", labels=["ask"]).data.id
        h.drain(6)

        ask_prompts = h.prompts_with("Route: ask.")
        assert len(ask_prompts) >= 1, "ask label did not build an ask-route prompt"
        p = ask_prompts[0]
        # Bundle: the real ask route doc + its reply protocol read are present.
        assert "===== routes/ask.md =====" in p
        # The MANDATORY target-instruction reads for the ASK route (Phase 1/2 names).
        assert "{0}/AGENTS_INSTRUCTIONS_ASK.md".format(_SPECSEED_DIR) in p
        assert "{0}/CUSTOM_INSTRUCTIONS_ASK.md".format(_SPECSEED_DIR) in p
        # READ-ONLY: the ask prompt must NOT carry git policy or action gates.
        assert "Git rules (the runtime owns git" not in p
        assert "Action gates (honour BEFORE" not in p
        assert "You are READ-ONLY" in p

        # The runtime posted the answer as a platform comment; the post stays open.
        joined = "\n".join(h.comments(eid))
        assert answer_text in joined
        assert h.remote.get_entry(eid).data.is_open is True
        # It never gained a workflow status label (read-only, no state machine).
        assert not any(":status:" in name for name in h.labels(eid))


# --- spec route: spec-change:adapt -> bundle(spec, adapt) -> propose --------- #

def test_spec_change_adapt_runs_spec_bundle_and_classifies_as_proposal() -> None:
    plan_summary = "PLAN: add a caching epic with one ticket and two issues."
    apr_id = "APR-0001"
    request = {}  # the spec-change post id, filled in once the post exists

    def side_effect(call):
        p = call["prompt"]
        assert "Route: spec. Subroute: adapt." in p, "spec suffix not translated to subroute"
        # The agent writes plan.json + apply.py into the request's spec-change dir,
        # then STOPS. A `creates` plan must classify as a proposal (gated), not a
        # direct apply. The dir is keyed on the request post id - which the test
        # already knows (no need to scrape it back out of the prompt).
        d = spec_change_dir(request["id"], h.root)
        d.mkdir(parents=True, exist_ok=True)
        (d / "plan.json").write_text(json.dumps({
            "plan_summary": plan_summary,
            "apr": {"id": apr_id, "summary": "Approve the caching epic?"},
            "creates": [{"title": "Caching epic", "body": "Speed up reads."}],
        }), encoding="utf-8")
        (d / "apply.py").write_text("# deferred; runs only on approval\n", encoding="utf-8")
        return AgentResult(ok=True, returncode=0)

    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(Path(tmp), FakeAgentRunner(side_effect=side_effect))
        eid = h.remote.add_entry(
            "Cache hot reads", body="We need caching.", labels=["spec-change:adapt"]
        ).data.id
        request["id"] = eid
        h.drain(8)

        spec_prompts = h.prompts_with("Route: spec.")
        assert len(spec_prompts) >= 1, "spec-change:adapt did not build a spec-route prompt"
        p = spec_prompts[0]
        assert "Mode: {0}. Route: spec. Subroute: adapt.".format(_EXPECT_MODE) in p
        # Bundle carries the spec route doc AND the adapt subroute doc.
        assert "===== routes/spec.md =====" in p
        assert "===== spec_subroutes/adapt.md =====" in p
        # MANDATORY SPEC instruction-file reads (subroute inherits the parent route).
        assert "{0}/AGENTS_INSTRUCTIONS_SPEC.md".format(_SPECSEED_DIR) in p
        assert "{0}/CUSTOM_INSTRUCTIONS_SPEC.md".format(_SPECSEED_DIR) in p
        # Per-request execution fact, staged under the request dir, STOP after.
        assert "Run the spec 'adapt' subroute for spec-change request {0}".format(eid) in p

        # Proposal classification: the request is parked awaiting approval and the
        # plan summary + APR request were posted - nothing created until a human
        # approves. (`propose_spec_change` is deterministic, no agent.)
        assert "spec-change:status:awaiting_approval" in h.labels(eid)
        joined = "\n".join(h.comments(eid))
        assert plan_summary in joined
        assert apr_id in joined


# --- scaffold adds NO target-root CLAUDE.md/AGENTS.md ------------------------ #

def test_scaffold_writes_no_target_root_claude_or_agents() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = scaffold.scaffold_target(root, _SPECSEED_DIR, primary_branch="main")
        # No router-block scaffolding survives (Phase 2 removed it).
        assert "router" not in result
        assert not (root / "CLAUDE.md").exists()
        assert not (root / "AGENTS.md").exists()
        # The canonical per-route guardrail + custom stubs ARE created in <sd>/.
        sd = root / _SPECSEED_DIR
        for name in (
            "AGENTS_INSTRUCTIONS_IMPL.md", "AGENTS_INSTRUCTIONS_SPEC.md",
            "AGENTS_INSTRUCTIONS_REVIEW.md", "AGENTS_INSTRUCTIONS_ASK.md",
            "CUSTOM_INSTRUCTIONS.md", "CUSTOM_INSTRUCTIONS_IMPL.md",
            "CUSTOM_INSTRUCTIONS_SPEC.md", "CUSTOM_INSTRUCTIONS_REVIEW.md",
            "CUSTOM_INSTRUCTIONS_ASK.md",
        ):
            assert (sd / name).exists(), "missing canonical stub {0}".format(name)


# --- migration strips the router block, keeps the files, marks 0.20.0 -------- #

def test_migration_0_20_0_strips_router_block_keeps_files_marks_marker() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        sd = repo / _SPECSEED_DIR
        storage = sd / "storage"
        storage.mkdir(parents=True, exist_ok=True)
        # Already-configured target: marker at 0.19.0 + a CLAUDE.md carrying the
        # router block surrounded by the user's own content.
        migrate.write_storage_version("0.19.0", storage)
        user_top = "# My project\n\nProject notes the human wrote.\n"
        user_bottom = "## Conventions\n\nKeep PRs small.\n"
        (repo / "CLAUDE.md").write_text(
            user_top
            + "\n<!-- specseed:router:start -->\nspecseed router junk\n<!-- specseed:router:end -->\n\n"
            + user_bottom,
            encoding="utf-8",
        )

        applied = migrate.run_migrations(storage, sd)
        assert "m_0_19_0__0_20_0" in applied

        text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
        assert (repo / "CLAUDE.md").exists()          # file kept, not deleted
        assert "specseed router junk" not in text     # block stripped
        assert "<!-- specseed:router:start -->" not in text
        assert "Project notes the human wrote." in text   # user content kept
        assert "Keep PRs small." in text
        # Marker advanced to the engine version.
        assert migrate.storage_version(storage) == "0.20.0"
        # New ASK-route stubs seeded by the hop.
        assert (sd / "AGENTS_INSTRUCTIONS_ASK.md").exists()
        assert (sd / "CUSTOM_INSTRUCTIONS_ASK.md").exists()

        # Idempotent: a second run is a no-op and keeps the marker at 0.20.0.
        assert migrate.run_migrations(storage, sd) == []
        assert migrate.storage_version(storage) == "0.20.0"
