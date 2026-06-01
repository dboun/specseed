"""
review_gate.py [issue_id] [--apply]

The code-review decision seam. After the reviewer agent writes
`.specseed/project_management/issues/<id>/review.json`, this script reads that
file + the `review` block of config.json + the issue's `difficulty` and decides
what happens to the issue's status:

  - auto_approve  → advance to `done` (or `awaiting_approval` if the issue ALSO
                    carries the human `approval_required` gate — review never
                    bypasses a mandated human sign-off)
  - needs_human   → land in `awaiting_approval` (a human resolves via the
                    approve route / remote CONTROL channel)
  - changes       → back to `in_progress` (reviewer requested changes; the
                    implementing agent addresses findings — that IS "changes
                    requested", no separate state)
  - skip          → review does not apply to this issue (scope/disabled); no-op

This is the "some actor advances in_review → …" seam the status model already
anticipates (references/work-breakdown.md "Transitions are flexible"). It is the
NON-implementer, so it MAY advance past a `review_required` gate — but never past
a `approval_required` human gate.

Gate logic (confidence is primary, difficulty is a modifier):
  applies?  scope=both → all; hard → difficulty==hard; easy → difficulty==easy;
            none/disabled → never.
  verdict==pass AND confidence>=min_confidence AND difficulty in auto_approve.difficulty
            → auto_approve. `hard` is excluded from auto_approve.difficulty by
            default, so hard issues always need a human even at high confidence.
  verdict in {fail, changes_requested} → changes.
  otherwise → needs_human.

review.json shape (written by the reviewer; this script only READS it):
  {"confidence": 0-100, "verdict": "pass"|"fail"|"changes_requested",
   "difficulty_assessment": "easy"|"hard"|null, "findings": [...],
   "model": "...", "reviewed_at": "<ISO>"}

Output: JSON decision to stdout. With --apply, also mutates issues.json under a
flock (same critical-section discipline as claim_issue.py). Stdlib only.

Exit 0 normal; 2 IO/lock/schema error.
"""

import argparse
import fcntl
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg_mod  # noqa: E402
import approvals_render  # noqa: E402

RESOLVED = {"done", "wont_do", "deprecated"}
_HEAD_RE = re.compile(r"^##\s+A(\d+)\b")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_approval_n(approval_file):
    n = 0
    if approval_file.exists():
        for ln in approval_file.read_text(encoding="utf-8").splitlines():
            m = _HEAD_RE.match(ln)
            if m:
                n = max(n, int(m.group(1)))
    return n + 1


def write_review_approval(pm, iid, decision, reason, review_json):
    """Append an entity-approval entry to the issue's approval.md so a review that
    lands in awaiting_approval surfaces in APPROVALS.md / the approve route, then
    refresh the generated index. Returns the entry number."""
    issue_dir = pm / "issues" / iid
    issue_dir.mkdir(parents=True, exist_ok=True)
    af = issue_dir / "approval.md"
    n = _next_approval_n(af)
    conf = (review_json or {}).get("confidence")
    verdict = (review_json or {}).get("verdict")
    summary = ("review passed, sign-off required" if decision == "auto_approve"
               else "code review needs your sign-off")
    block = [
        f"## A{n} — {summary}",
        f"- **Opened:** {_now_iso()}",
        "- **Kind:** entity-approval",
        "- **Status:** open",
        f"- **What I need / am about to do:** Approve completion of `{iid}` after code review "
        f"(verdict {verdict!r}, confidence {conf}).",
        f"- **Why it's gated:** {reason}",
        f"- **Links / details:** `.specseed/project_management/issues/{iid}/review.json`",
        "- **Options:** A) approve → done (recommended) · B) request changes → back to in_progress",
        f"- **Resolve:** `/specseed approve {iid} A` (local), or comment `approve {iid} A` on CONTROL.",
        "",
    ]
    prefix = af.read_text(encoding="utf-8").rstrip() + "\n\n" if af.exists() else ""
    af.write_text(prefix + "\n".join(block) + "\n", encoding="utf-8")
    # refresh the generated index (same as approvals_render CLI)
    records = approvals_render.collect(pm)
    (pm / "APPROVALS.md").write_text(approvals_render.render_md(records), encoding="utf-8")
    (pm / "approvals.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return n


def scope_matches(scope, difficulty):
    if scope == "none":
        return False
    if scope == "both":
        return True
    if scope == "hard":
        return difficulty == "hard"
    if scope == "easy":
        return difficulty == "easy"
    return False


def review_applies(review_cfg, issue):
    """An issue gets reviewed if the per-issue `review_required` flag is set, OR the
    config scope covers its difficulty (and review is enabled). The explicit flag
    wins even when the config scope would exclude it."""
    if issue.get("review_required"):
        return True
    if not review_cfg.get("enabled") or review_cfg.get("scope") == "none":
        return False
    return scope_matches(review_cfg.get("scope", "hard"), issue.get("difficulty"))


def decide(issue, review_json, review_cfg):
    """Return (decision, reason). decision ∈ {auto_approve, needs_human, changes, skip}."""
    difficulty = issue.get("difficulty")
    if not review_applies(review_cfg, issue):
        return "skip", (f"scope={review_cfg.get('scope')} excludes difficulty="
                        f"{difficulty!r} and review_required is not set")

    verdict = (review_json or {}).get("verdict")
    confidence = (review_json or {}).get("confidence")
    if verdict in ("fail", "changes_requested"):
        return "changes", f"reviewer verdict={verdict}"
    if verdict != "pass":
        return "needs_human", f"missing/unknown verdict={verdict!r}"

    aa = review_cfg.get("auto_approve", {})
    min_conf = aa.get("min_confidence", 90)
    allowed = aa.get("difficulty", [])
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return "needs_human", f"non-numeric confidence={confidence!r}"
    if difficulty not in allowed:
        return "needs_human", f"difficulty={difficulty!r} not in auto_approve set {allowed}"
    if confidence < min_conf:
        return "needs_human", f"confidence {confidence} < min {min_conf}"
    return "auto_approve", f"confidence {confidence} >= {min_conf}, difficulty {difficulty} allowed"


def target_status(decision, issue):
    """Map a decision to the status the issue should take. auto_approve respects a
    human approval_required gate (review never bypasses it)."""
    if decision == "auto_approve":
        return "awaiting_approval" if issue.get("approval_required") else "done"
    if decision == "needs_human":
        return "awaiting_approval"
    if decision == "changes":
        return "in_progress"
    return None  # skip


def acquire_lock(path, timeout):
    try:
        f = open(path, "r+", encoding="utf-8")
    except OSError as e:
        print(f"ERROR: cannot open {path}: {e}", file=sys.stderr)
        return None
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except BlockingIOError:
            if time.monotonic() >= deadline:
                f.close()
                return None
            time.sleep(0.05)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("issue_id")
    p.add_argument("--apply", action="store_true",
                   help="mutate issues.json to the decided status (else dry-run)")
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--review-path", default=None,
                   help="override path to review.json (default: issues/<id>/review.json)")
    p.add_argument("--lock-timeout", type=float, default=10.0)
    args = p.parse_args()

    pm = Path(args.pm_dir)
    issues_path = pm / "issues.json"
    iid = args.issue_id
    review_path = Path(args.review_path) if args.review_path \
        else pm / "issues" / iid / "review.json"

    cfg = cfg_mod.load_config() or cfg_mod.default_config()
    review_cfg = cfg_mod.review_config(cfg)

    if not issues_path.exists():
        print(f"ERROR: {issues_path} not found", file=sys.stderr)
        sys.exit(2)
    try:
        issues = json.loads(issues_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed issues.json: {e}", file=sys.stderr)
        sys.exit(2)
    issue = issues.get(iid)
    if issue is None:
        print(json.dumps({"issue_id": iid, "decision": "error",
                          "reason": "issue not found"}))
        sys.exit(2)

    review_json = None
    if review_path.exists():
        try:
            review_json = json.loads(review_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"ERROR: malformed {review_path}: {e}", file=sys.stderr)
            sys.exit(2)

    decision, reason = decide(issue, review_json, review_cfg)
    new_status = target_status(decision, issue)
    out = {"issue_id": iid, "decision": decision, "reason": reason,
           "from_status": issue.get("status"), "to_status": new_status,
           "applied": False}

    if args.apply and new_status and new_status != issue.get("status"):
        f = acquire_lock(issues_path, args.lock_timeout)
        if f is None:
            print(json.dumps({**out, "error": "lock-timeout"}), file=sys.stderr)
            sys.exit(2)
        try:
            f.seek(0)
            live = json.load(f)
            le = live.get(iid)
            if le is None:
                print(json.dumps({**out, "error": "issue vanished"}), file=sys.stderr)
                sys.exit(2)
            le["status"] = new_status
            # auto_approve to a clean close releases the claim; changes/needs_human
            # keep work in flight (claim retained per the claim model).
            if new_status in RESOLVED:
                le["claimed_at"] = None
                le["claimed_by"] = None
            f.seek(0)
            f.truncate()
            f.write(json.dumps(live, indent=2) + "\n")
            f.flush()
            out["applied"] = True
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            f.close()
        # surface a human sign-off in APPROVALS.md when the review lands in awaiting_approval
        if new_status == "awaiting_approval":
            try:
                out["approval_n"] = write_review_approval(pm, iid, decision, reason, review_json)
            except Exception as e:
                print(f"WARNING: could not write approval entry: {e}", file=sys.stderr)

    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
