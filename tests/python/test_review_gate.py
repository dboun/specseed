"""review_gate.py — the code-review decision seam + approval surfacing."""

import json

import config
import review_gate as G

RC = config.review_config(config.default_config())          # scope=hard, easy auto>=90
RC_BOTH = config.review_config({"review": {"scope": "both"}})


def test_scope_matches():
    assert G.scope_matches("hard", "hard") is True
    assert G.scope_matches("hard", "easy") is False
    assert G.scope_matches("both", "easy") is True
    assert G.scope_matches("none", "hard") is False


def test_review_required_forces_review_even_out_of_scope():
    # easy issue under scope=hard normally skipped, but review_required overrides
    assert G.review_applies(RC, {"difficulty": "easy"}) is False
    assert G.review_applies(RC, {"difficulty": "easy", "review_required": True}) is True


def test_easy_high_confidence_auto_approves():
    d, _ = G.decide({"difficulty": "easy"}, {"verdict": "pass", "confidence": 95}, RC_BOTH)
    assert d == "auto_approve"


def test_hard_never_auto_approves_even_at_99():
    d, _ = G.decide({"difficulty": "hard"}, {"verdict": "pass", "confidence": 99}, RC)
    assert d == "needs_human"


def test_low_confidence_needs_human():
    d, _ = G.decide({"difficulty": "easy"}, {"verdict": "pass", "confidence": 50}, RC_BOTH)
    assert d == "needs_human"


def test_changes_requested_bounces_back():
    d, _ = G.decide({"difficulty": "easy"}, {"verdict": "changes_requested", "confidence": 10}, RC_BOTH)
    assert d == "changes"


def test_missing_review_json_needs_human():
    d, _ = G.decide({"difficulty": "hard"}, None, RC)
    assert d == "needs_human"


def test_target_status_respects_approval_required():
    # auto-approve on an approval_required issue must NOT close to done
    assert G.target_status("auto_approve", {"approval_required": True}) == "awaiting_approval"
    assert G.target_status("auto_approve", {"approval_required": False}) == "done"
    assert G.target_status("changes", {}) == "in_progress"


def _setup_in_review(repo, iid, difficulty, **issue_over):
    issue = {"status": "in_review", "difficulty": difficulty,
             "approval_required": False, "claimed_by": "a",
             "claimed_at": "2026-06-01T00:00:00Z"}
    issue.update(issue_over)
    (repo.pm / "issues.json").write_text(json.dumps({iid: issue}, indent=2))
    (repo.pm / "issues" / iid).mkdir(parents=True, exist_ok=True)


def test_apply_auto_close_releases_claim(repo):
    cfg = config.default_config()
    cfg["review"]["scope"] = "both"
    (repo.mem / "config.json").parent.mkdir(parents=True, exist_ok=True)
    (repo.mem / "config.json").write_text(json.dumps(cfg))
    _setup_in_review(repo, "FEAT-0001", "easy")
    (repo.pm / "issues" / "FEAT-0001" / "review.json").write_text(
        json.dumps({"verdict": "pass", "confidence": 96}))
    r = repo.core("review_gate.py", "FEAT-0001", "--apply")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["decision"] == "auto_approve" and out["to_status"] == "done"
    issue = json.loads((repo.pm / "issues.json").read_text())["FEAT-0001"]
    assert issue["status"] == "done"
    assert issue["claimed_by"] is None          # claim released on close


def test_apply_needs_human_writes_approval_and_index(repo):
    # default config: scope=hard → a hard issue needs human
    (repo.mem).mkdir(parents=True, exist_ok=True)
    (repo.mem / "config.json").write_text(json.dumps(config.default_config()))
    _setup_in_review(repo, "FEAT-0001", "hard")
    (repo.pm / "issues" / "FEAT-0001" / "review.json").write_text(
        json.dumps({"verdict": "pass", "confidence": 99}))
    r = repo.core("review_gate.py", "FEAT-0001", "--apply")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["to_status"] == "awaiting_approval"
    # approval.md entry + APPROVALS index written so a human can find it
    appr = (repo.pm / "issues" / "FEAT-0001" / "approval.md").read_text()
    assert "**Kind:** entity-approval" in appr and "**Status:** open" in appr
    assert "FEAT-0001" in (repo.pm / "APPROVALS.md").read_text()
    records = json.loads((repo.pm / "approvals.json").read_text())
    assert any(rec["issue"] == "FEAT-0001" for rec in records)
    # claim retained (work still in flight)
    assert json.loads((repo.pm / "issues.json").read_text())["FEAT-0001"]["claimed_by"] == "a"
