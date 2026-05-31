"""
issues_validate.py

Validates the ISSUE tier (.specseed/project_management/issues.json), assembled
by issues_assemble.py from the per-issue folders. Issues are the TECHNICAL,
claimable/executable layer: they carry artifacts (touches/tests/migrations),
agent-time effort_hours, claim fields, and optional intra-ticket depends_on.
Requirements live on the parent TICKET, not here.

Kept SEPARATE from tickets_validate.py — see that file's note on separate
stores. Cross-tier ref (issue.ticket → tickets.json) is checked when tickets.json
is present and skipped gracefully otherwise.

Checks:
 1. ID format /^[A-Z]+-\\d{4,}$/, unique; prefix should match type (warn)
 2. Required fields: title, type, component, effort_hours, status,
    claimed_at, claimed_by, artifacts
 3. Enums: type ∈ {feature,bug,chore,spike}; status ∈ {todo,in_progress,
    blocked,done,deprecated}
 4. effort_hours: positive number (agent-time estimate)
 5. depends_on refs exist in issues.json; no cycles
 6. ticket ref (if non-null) exists in tickets.json (skipped if absent)
 7. artifacts.touches/tests/migrations are string lists; tests + migrations
    existence on disk → warnings
 8. Claim invariants: status × claimed_at × claimed_by

Exit: 0 OK (or warnings only), 1 errors, 2 missing input.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from graphlib import TopologicalSorter, CycleError

ID_RE = re.compile(r"^[A-Z]+-\d{4,}$")
VALID_TYPES = {"feature", "bug", "chore", "spike"}
VALID_STATUSES = {"todo", "in_progress", "blocked", "done", "deprecated"}
TYPE_PREFIX = {"feature": "FEAT", "bug": "BUG", "chore": "CHORE", "spike": "SPIKE"}
REQUIRED_FIELDS = ["title", "type", "component", "effort_hours", "status",
                   "claimed_at", "claimed_by", "artifacts"]


def parse_iso(s):
    if not isinstance(s, str):
        return None
    try:
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        return datetime.fromisoformat(s2)
    except ValueError:
        return None


def validate_one(iid, e, all_ids, tickets, repo_root):
    errors, warnings = [], []

    if not ID_RE.match(iid):
        errors.append({"id": iid, "kind": "id_format",
                       "detail": "must match /^[A-Z]+-\\d{4,}$/"})
    if not isinstance(e, dict):
        errors.append({"id": iid, "kind": "schema", "detail": "entry not an object"})
        return errors, warnings

    for field in REQUIRED_FIELDS:
        if field not in e:
            errors.append({"id": iid, "kind": "missing_field", "field": field})
    if any(x["kind"] == "missing_field" for x in errors):
        return errors, warnings

    if e["type"] not in VALID_TYPES:
        errors.append({"id": iid, "kind": "enum", "field": "type", "value": e["type"]})
    elif not iid.startswith(TYPE_PREFIX[e["type"]] + "-"):
        warnings.append({"id": iid, "kind": "prefix_mismatch",
                         "detail": f"id prefix doesn't match type {e['type']!r} "
                                   f"(expected {TYPE_PREFIX[e['type']]}-NNNN)"})
    if e["status"] not in VALID_STATUSES:
        errors.append({"id": iid, "kind": "enum", "field": "status", "value": e["status"]})

    eh = e["effort_hours"]
    if isinstance(eh, bool) or not isinstance(eh, (int, float)) or eh <= 0:
        errors.append({"id": iid, "kind": "effort",
                       "detail": f"effort_hours must be a positive number, got {eh!r}"})

    deps = e.get("depends_on", []) or []
    if not isinstance(deps, list):
        errors.append({"id": iid, "kind": "schema", "field": "depends_on",
                       "detail": "must be a list"})
    else:
        for d in deps:
            if d not in all_ids:
                errors.append({"id": iid, "kind": "dangling_ref",
                               "field": "depends_on", "value": d})

    tk = e.get("ticket")
    if tk and tickets is not None and tk not in tickets:
        errors.append({"id": iid, "kind": "dangling_ref", "field": "ticket",
                       "value": tk, "detail": "not in tickets.json"})

    artifacts = e.get("artifacts", {})
    if not isinstance(artifacts, dict):
        errors.append({"id": iid, "kind": "schema", "field": "artifacts",
                       "detail": "must be an object"})
    else:
        for key in ("touches", "tests", "migrations"):
            if key not in artifacts:
                continue
            val = artifacts[key]
            if not isinstance(val, list) or any(not isinstance(x, str) for x in val):
                errors.append({"id": iid, "kind": "schema",
                               "field": f"artifacts.{key}",
                               "detail": "must be a list of strings"})
                continue
            if key in ("tests", "migrations"):
                for path in val:
                    if not (repo_root / path).exists():
                        warnings.append({"id": iid, "kind": f"missing_{key}_file",
                                         "field": f"artifacts.{key}", "value": path})

    ca, cb, status = e["claimed_at"], e["claimed_by"], e.get("status")
    if ca is not None and (not isinstance(ca, str) or parse_iso(ca) is None):
        errors.append({"id": iid, "kind": "claim_invariant", "field": "claimed_at",
                       "detail": f"must be null or ISO-8601, got {ca!r}"})
    if cb is not None and (not isinstance(cb, str) or not cb.strip()):
        errors.append({"id": iid, "kind": "claim_invariant", "field": "claimed_by",
                       "detail": "must be null or non-empty string"})
    if (ca is None) != (cb is None):
        errors.append({"id": iid, "kind": "claim_invariant",
                       "field": "claimed_at/claimed_by",
                       "detail": "both must be null or both populated"})
    else:
        has_claim = ca is not None
        if status == "in_progress" and not has_claim:
            errors.append({"id": iid, "kind": "claim_invariant",
                           "detail": "status=in_progress but claim fields null"})
        elif status in ("todo", "done", "deprecated") and has_claim:
            errors.append({"id": iid, "kind": "claim_invariant",
                           "detail": f"status={status} but claim fields populated"})
        # status=blocked: either is acceptable
    return errors, warnings


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--issues-path", default=None)
    p.add_argument("--tickets-path", default=None)
    p.add_argument("--repo-root", default=".")
    args = p.parse_args()

    pm = Path(args.pm_dir)
    issues_path = Path(args.issues_path) if args.issues_path else pm / "issues.json"
    tickets_path = Path(args.tickets_path) if args.tickets_path else pm / "tickets.json"
    repo_root = Path(args.repo_root)

    if not issues_path.exists():
        print(f"ERROR: {issues_path} not found (run issues_assemble.py)", file=sys.stderr)
        sys.exit(2)
    try:
        issues = json.loads(issues_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed {issues_path}: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(issues, dict):
        print("ERROR: issues.json root must be an object", file=sys.stderr)
        sys.exit(2)

    tickets = None
    if tickets_path.exists():
        try:
            tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            tickets = None

    all_ids = set(issues.keys())
    all_errors, all_warnings = [], []
    for iid, e in issues.items():
        errs, warns = validate_one(iid, e, all_ids, tickets, repo_root)
        all_errors.extend(errs)
        all_warnings.extend(warns)

    try:
        TopologicalSorter({iid: set(e.get("depends_on", []) or [])
                           for iid, e in issues.items()
                           if isinstance(e, dict)}).prepare()
    except CycleError as e:
        all_errors.append({"id": "*", "kind": "cycle", "detail": str(e.args[1])})

    if all_errors:
        print(json.dumps({"valid": False, "errors": all_errors,
                          "warnings": all_warnings}, indent=2))
        sys.exit(1)
    if all_warnings:
        print(f"OK with {len(all_warnings)} warning(s) on {len(issues)} issue(s):")
        for w in all_warnings:
            print(f"  {w['id']} [{w['kind']}]: {w.get('detail', '')} "
                  f"{w.get('value', '')}".strip())
        sys.exit(0)
    print(f"OK: {len(issues)} issues validated")
    sys.exit(0)


if __name__ == "__main__":
    main()
