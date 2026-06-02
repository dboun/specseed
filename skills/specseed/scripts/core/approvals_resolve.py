"""
approvals_resolve.py — deterministic approve / reject / hold of a HITL gate.

The strong, model-free other half of HITL resolution. Where the impl agent PARKS a
gate (writes `issues/<id>/approval.md`, sets the issue `awaiting_approval`), this
script RESOLVES one — addressed by its global `APR-NNNN` id (stamped by
`approvals_render.py`) — by appending a `## Resolved A<N>` marker and flipping the
issue status in `issues.json`. No agent, no natural-language guessing about which
gate or what terminal status: the decision table below is the whole contract.

It is the single mutation path for resolution. The interactive `approve` route
presents + asks, then delegates its WRITE here (DRY); the remote CONTROL
`approve`/`reject` verbs call it headless from `agents_runner.execute_actions`.

CLI:
  approvals_resolve.py <APR-NNNN|issue-id> approve|reject|hold
        [--note "..."] [--option A] [--wont-do] [--run-verify]
        [--pm-dir ...] [--lock-timeout N]

Addressing: an `APR-NNNN` id is exact. A bare issue id is accepted as a convenience
only when that issue has EXACTLY ONE open gate (else: ambiguous → error, use the id).

Decision table (mirrors routes/approve.md Stage 3 — that doc is the spec):

  kind                       approve            reject                       hold
  completion (entity-approval) done (+rollup)   in_progress (changes)        blocked
  action (gate:* / run-action  todo             wont_do if --wont-do         blocked
    / handoff / git-conflict)  (claim cleared)  else blocked (claim cleared)

  - approve clears the claim (next agent re-claims + resumes on the existing branch);
    a completion approve closes the issue (claim cleared) and re-rolls the ticket.
  - reject of a completion gate = "changes requested" → in_progress, claim kept.
  - reject of an action gate clears the claim; default `blocked` (reversible, keeps a
    rescope note) unless `--wont-do` (the gate WAS the issue's whole point).
  - hold → blocked, claim kept (paused; same agent resumes).

Multiple open gates on one issue: this resolves the named one, but only flips the
ISSUE status once it is the LAST open gate (siblings checked after the marker write).

`--run-verify` (handoff, opt-in): run the entry's `Verify:` command first; non-zero
→ refuse to approve, exit non-zero, leave the gate open. Off by default (no silent
shell-exec; the remote path generally won't pass it).

Side effects after a good resolve: re-run `approvals_render.py` (drop the resolved
entry from the index); a completion close also re-runs `tickets_assemble.py` +
`roadmap_render.py` so the ticket rolls up.

Exit: 0 ok; 2 on unknown/ambiguous id, already-resolved, verify-failed, or IO/lock
error (so the runner can report a concrete reason). Stdlib only.
"""

import argparse
import fcntl
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import approvals_render as A  # noqa: E402

APR_VALUE_RE = A.APR_VALUE_RE
COMPLETION_KINDS = {"entity-approval"}


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_id(entry):
    return (entry["fields"].get("id") or "").strip()


def find_by_apr(pm_dir, apr):
    """Scan every issue's approval.md for the entry whose `Id:` == apr (open OR
    resolved). Return (issue, entry) or (None, None)."""
    issues_dir = pm_dir / "issues"
    if not issues_dir.is_dir():
        return None, None
    for issue_dir in sorted(p for p in issues_dir.iterdir() if p.is_dir()):
        af = issue_dir / "approval.md"
        if not af.exists():
            continue
        for e in A._parse_approval_file(af.read_text(encoding="utf-8")):
            if _entry_id(e) == apr:
                return issue_dir.name, e
    return None, None


def open_entries_for_issue(pm_dir, issue):
    """All OPEN (unresolved, status:open) entries on one issue, as parsed dicts."""
    af = pm_dir / "issues" / issue / "approval.md"
    if not af.exists():
        return []
    out = []
    for e in A._parse_approval_file(af.read_text(encoding="utf-8")):
        status = e["fields"].get("status", "open").lower()
        if not e["resolved"] and status == "open":
            out.append(e)
    return out


def resolve_target(pm_dir, token):
    """Map the CLI handle to (issue, entry, error).
    - APR-NNNN → exact lookup.
    - bare issue id → its single open gate (ambiguous if it has >1)."""
    if APR_VALUE_RE.match(token):
        issue, entry = find_by_apr(pm_dir, token)
        if issue is None:
            return None, None, f"unknown id {token}"
        return issue, entry, None
    # treat as an issue id
    issue_dir = pm_dir / "issues" / token
    if not (issue_dir / "approval.md").exists():
        return None, None, f"unknown id {token}"
    opens = open_entries_for_issue(pm_dir, token)
    if not opens:
        return None, None, f"{token} has no open gate"
    if len(opens) > 1:
        ids = ", ".join(_entry_id(e) or f"A{e['n']}" for e in opens)
        return None, None, f"{token} has multiple open gates ({ids}); name one by its APR id"
    return token, opens[0], None


def is_completion(kind):
    return (kind or "").strip().lower() in COMPLETION_KINDS


def decide_status(kind, decision, wont_do):
    """Return (new_status, claim) where claim ∈ {'clear','keep'}. None status = no-op."""
    comp = is_completion(kind)
    if decision == "hold":
        return "blocked", "keep"
    if decision == "approve":
        return ("done" if comp else "todo"), "clear"
    if decision == "reject":
        if comp:
            return "in_progress", "keep"        # changes requested
        return ("wont_do" if wont_do else "blocked"), "clear"
    return None, "keep"


def append_resolved(pm_dir, issue, n, decision, detail):
    """Append `## Resolved A<n> (<ISO>): <decision> — <detail>` to the issue's
    approval.md. Never edits the original question (same rule as the approve route)."""
    af = pm_dir / "issues" / issue / "approval.md"
    text = af.read_text(encoding="utf-8")
    line = f"## Resolved A{n} ({_now_iso()}): {decision}"
    if detail:
        line += f" — {detail}"
    af.write_text(text.rstrip() + "\n\n" + line + "\n", encoding="utf-8")


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


def apply_status(issues_path, issue, new_status, claim, timeout):
    """Flip the issue status under a flock (same discipline as claim_issue /
    review_gate). claim='clear' drops the claim fields. Returns (ok, from_status)."""
    f = acquire_lock(issues_path, timeout)
    if f is None:
        return False, None
    try:
        f.seek(0)
        live = json.load(f)
        ie = live.get(issue)
        if ie is None:
            return False, None
        from_status = ie.get("status")
        ie["status"] = new_status
        if claim == "clear":
            ie["claimed_at"] = None
            ie["claimed_by"] = None
        f.seek(0)
        f.truncate()
        f.write(json.dumps(live, indent=2) + "\n")
        f.flush()
        return True, from_status
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def _run(script, pm_dir, root):
    core = Path(__file__).resolve().parent
    r = subprocess.run([sys.executable, str(core / script), "--pm-dir", str(pm_dir)],
                       cwd=str(root), check=False, capture_output=True, text=True)
    if r.returncode == 0:
        return True, ""
    msg = (r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")
    return False, f"{script} failed: {msg}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("handle", help="APR-NNNN id (or a bare issue id with one open gate)")
    p.add_argument("decision", choices=["approve", "reject", "hold"])
    p.add_argument("--note", default="")
    p.add_argument("--option", default="", help="decided option letter (e.g. A)")
    p.add_argument("--wont-do", action="store_true",
                   help="action-gate reject → wont_do (the gate was the issue's whole "
                        "point) instead of the default blocked")
    p.add_argument("--run-verify", action="store_true",
                   help="handoff only: run the entry's Verify: command before approving")
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--lock-timeout", type=float, default=10.0)
    args = p.parse_args()

    pm = Path(args.pm_dir)
    if not pm.is_dir():
        print(f"ERROR: {pm} not found", file=sys.stderr)
        sys.exit(2)
    issues_path = pm / "issues.json"
    if not issues_path.exists():
        print(f"ERROR: {issues_path} not found", file=sys.stderr)
        sys.exit(2)

    issue, entry, err = resolve_target(pm, args.handle)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(2)
    if entry["resolved"] or entry["fields"].get("status", "open").lower() != "open":
        print(f"ERROR: {args.handle} is already resolved", file=sys.stderr)
        sys.exit(2)

    kind = entry["fields"].get("kind", "")
    n = entry["n"]
    apr = _entry_id(entry) or f"A{n}"

    # --run-verify (handoff approve): refuse if the Verify: command fails. Run from
    # the repo root (pm = <root>/.specseed/project_management) so relative paths work.
    if args.run_verify and args.decision == "approve":
        verify = entry["fields"].get("verify", "").strip()
        if verify:
            r = subprocess.run(verify, shell=True, cwd=str(pm.resolve().parent.parent),
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f"ERROR: verify failed for {apr} (`{verify}` exit "
                      f"{r.returncode}); gate left open", file=sys.stderr)
                sys.exit(2)

    new_status, claim = decide_status(kind, args.decision, args.wont_do)

    detail = args.note.strip()
    if args.option.strip():
        opt = f"option {args.option.strip()}"
        detail = f"{opt} — {detail}" if detail else opt
    # Last-gate rule: only flip the issue once no other open gate remains on it. Check
    # siblings BEFORE appending the resolved marker so a failed status flip cannot close
    # the gate and strand the issue with no open approval to retry.
    siblings = [e for e in open_entries_for_issue(pm, issue) if e["n"] != n]
    flipped, from_status = False, None
    if not siblings and new_status:
        flipped, from_status = apply_status(issues_path, issue, new_status, claim,
                                            args.lock_timeout)
        if not flipped:
            print(f"ERROR: could not flip {issue} status (lock or vanished)",
                  file=sys.stderr)
            sys.exit(2)

    append_resolved(pm, issue, n, args.decision, detail)

    # Side effects: refresh the index; a completion close re-rolls the ticket.
    root = Path.cwd()
    warnings = []
    ok, msg = _run("approvals_render.py", pm, root)
    if not ok:
        warnings.append(msg)
    if flipped and is_completion(kind) and args.decision == "approve":
        for script in ("tickets_assemble.py", "roadmap_render.py"):
            ok, msg = _run(script, pm, root)
            if not ok:
                warnings.append(msg)

    out = {"apr": apr, "issue": issue, "n": n, "kind": kind,
           "decision": args.decision, "flipped": flipped,
           "from_status": from_status, "to_status": new_status if flipped else None,
           "open_siblings": [(_entry_id(e) or f"A{e['n']}") for e in siblings],
           "warnings": warnings}
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
