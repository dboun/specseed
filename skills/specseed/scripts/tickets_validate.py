"""
tickets_validate.py

Validates spec/tickets.json. See module-level spec in skill notes.

Checks:
 1. ID format /^[A-Z]+-\\d{4,}$/, unique
 2. Required fields present
 3. Enum values valid (type, status)
 4. effort_hours positive number
 5. depends_on refs exist in tickets.json
 6. satisfies_reqs refs exist in reqs.json
 7. acceptance_criteria non-empty list of strings
 8. artifacts.touches/tests/migrations lists of strings; existence as warnings
 9. milestone (if present) exists in milestones.md
10. Claim invariants (status × claimed_at × claimed_by)

Exit: 0 OK (or warnings only), 1 errors found, 2 missing input.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ID_RE = re.compile(r"^[A-Z]+-\d{4,}$")
VALID_TYPES = {"feature", "bug", "chore", "spike"}
VALID_STATUSES = {"todo", "in_progress", "blocked", "done", "deprecated"}
REQUIRED_FIELDS = [
    "title", "description", "type", "status", "effort_hours",
    "satisfies_reqs", "acceptance_criteria",
]


def parse_iso_timestamp(s):
    if not isinstance(s, str):
        return None
    try:
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        return datetime.fromisoformat(s2)
    except ValueError:
        return None


def parse_milestones(milestones_path):
    """Parse spec/milestones.md to find milestone names like M1, M2.
    Returns set of names, or None if file doesn't exist (milestones not in use).
    """
    if not milestones_path.exists():
        return None
    text = milestones_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^#+\s+(M\d+)\b", re.MULTILINE)
    return set(pattern.findall(text))


def validate_one(tid, t, all_ids, reqs, milestones, repo_root):
    errors, warnings = [], []

    if not ID_RE.match(tid):
        errors.append({"ticket_id": tid, "kind": "id_format",
                       "detail": "ID must match /^[A-Z]+-\\d{4,}$/"})

    if not isinstance(t, dict):
        errors.append({"ticket_id": tid, "kind": "missing_field",
                       "detail": "ticket entry is not an object"})
        return errors, warnings

    # 2. Required fields
    for field in REQUIRED_FIELDS:
        if field not in t:
            errors.append({"ticket_id": tid, "kind": "missing_field", "field": field})
    # claim fields are also required keys
    if "claimed_at" not in t:
        errors.append({"ticket_id": tid, "kind": "missing_field", "field": "claimed_at"})
    if "claimed_by" not in t:
        errors.append({"ticket_id": tid, "kind": "missing_field", "field": "claimed_by"})

    if any(e["kind"] == "missing_field" for e in errors):
        return errors, warnings

    # 3. Enums
    if t["type"] not in VALID_TYPES:
        errors.append({"ticket_id": tid, "kind": "enum_invalid", "field": "type",
                       "value": t["type"]})
    if t["status"] not in VALID_STATUSES:
        errors.append({"ticket_id": tid, "kind": "enum_invalid", "field": "status",
                       "value": t["status"]})

    # 4. effort_hours
    eh = t["effort_hours"]
    if not isinstance(eh, (int, float)) or isinstance(eh, bool) or eh <= 0:
        errors.append({"ticket_id": tid, "kind": "effort_invalid",
                       "detail": f"effort_hours must be positive number, got {eh!r}"})

    # 5. depends_on
    deps = t.get("depends_on", [])
    if not isinstance(deps, list):
        errors.append({"ticket_id": tid, "kind": "missing_field",
                       "field": "depends_on", "detail": "must be a list"})
    else:
        for d in deps:
            if d not in all_ids:
                errors.append({"ticket_id": tid, "kind": "dangling_ref",
                               "field": "depends_on", "value": d})

    # 6. satisfies_reqs
    sat = t["satisfies_reqs"]
    if not isinstance(sat, list) or not sat:
        errors.append({"ticket_id": tid, "kind": "acceptance_invalid",
                       "field": "satisfies_reqs",
                       "detail": "must be non-empty list"})
    else:
        for r in sat:
            if r not in reqs:
                errors.append({"ticket_id": tid, "kind": "dangling_ref",
                               "field": "satisfies_reqs", "value": r})

    # 7. acceptance_criteria
    ac = t["acceptance_criteria"]
    if not isinstance(ac, list) or not ac:
        errors.append({"ticket_id": tid, "kind": "acceptance_invalid",
                       "detail": "must be non-empty list"})
    else:
        for c in ac:
            if not isinstance(c, str) or not c.strip():
                errors.append({"ticket_id": tid, "kind": "acceptance_invalid",
                               "detail": "each entry must be non-empty string"})
                break

    # 8. artifacts
    artifacts = t.get("artifacts", {})
    if not isinstance(artifacts, dict):
        errors.append({"ticket_id": tid, "kind": "artifacts_invalid",
                       "detail": "artifacts must be an object"})
    else:
        for key in ("touches", "tests", "migrations"):
            if key not in artifacts:
                continue
            val = artifacts[key]
            if not isinstance(val, list):
                errors.append({"ticket_id": tid, "kind": "artifacts_invalid",
                               "field": f"artifacts.{key}",
                               "detail": "must be a list"})
                continue
            type_ok = True
            for path in val:
                if not isinstance(path, str):
                    errors.append({"ticket_id": tid, "kind": "artifacts_invalid",
                                   "field": f"artifacts.{key}",
                                   "detail": "entries must be strings"})
                    type_ok = False
                    break
            if not type_ok:
                continue
            # Existence checks → warnings (not errors)
            if key == "migrations":
                for path in val:
                    if not (repo_root / path).exists():
                        warnings.append({
                            "ticket_id": tid, "kind": "missing_migration_file",
                            "field": "artifacts.migrations", "value": path,
                            "detail": "path does not exist on disk",
                        })
            elif key == "tests":
                for path in val:
                    if not (repo_root / path).exists():
                        warnings.append({
                            "ticket_id": tid, "kind": "missing_test_file",
                            "field": "artifacts.tests", "value": path,
                            "detail": "path does not exist on disk",
                        })

    # 9. milestone
    if "milestone" in t and milestones is not None:
        ms = t["milestone"]
        if ms not in milestones:
            errors.append({"ticket_id": tid, "kind": "milestone_unknown",
                           "field": "milestone", "value": ms})

    # 10. Claim invariants
    ca = t["claimed_at"]
    cb = t["claimed_by"]
    status = t.get("status")

    if ca is not None:
        if not isinstance(ca, str) or parse_iso_timestamp(ca) is None:
            errors.append({"ticket_id": tid, "kind": "claim_invariant",
                           "field": "claimed_at",
                           "detail": f"must be null or ISO-8601 timestamp, got {ca!r}"})
    if cb is not None:
        if not isinstance(cb, str) or not cb.strip():
            errors.append({"ticket_id": tid, "kind": "claim_invariant",
                           "field": "claimed_by",
                           "detail": "must be null or non-empty string"})

    if (ca is None) != (cb is None):
        errors.append({"ticket_id": tid, "kind": "claim_invariant",
                       "field": "claimed_at/claimed_by",
                       "detail": "both must be null or both populated"})
    else:
        has_claim = ca is not None
        if status == "in_progress" and not has_claim:
            errors.append({"ticket_id": tid, "kind": "claim_invariant",
                           "field": "claimed_at",
                           "detail": "status=in_progress but claim fields are null"})
        elif status in ("todo", "done", "deprecated") and has_claim:
            errors.append({"ticket_id": tid, "kind": "claim_invariant",
                           "field": "claimed_at",
                           "detail": f"status={status} but claim fields are populated"})
        # status=blocked: either OK

    return errors, warnings


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickets-path", default="spec/tickets.json")
    p.add_argument("--reqs-path", default="spec/reqs.json")
    p.add_argument("--milestones-path", default="spec/milestones.md")
    p.add_argument("--repo-root", default=".")
    args = p.parse_args()

    tickets_path = Path(args.tickets_path)
    reqs_path = Path(args.reqs_path)
    milestones_path = Path(args.milestones_path)
    repo_root = Path(args.repo_root)

    if not tickets_path.exists():
        print(f"ERROR: {tickets_path} not found", file=sys.stderr)
        sys.exit(2)
    if not reqs_path.exists():
        print(f"ERROR: {reqs_path} not found — cannot validate satisfies_reqs",
              file=sys.stderr)
        sys.exit(2)

    try:
        tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
        reqs = json.loads(reqs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed JSON: {e}", file=sys.stderr)
        sys.exit(2)

    if not isinstance(tickets, dict):
        print(f"ERROR: tickets.json root must be an object", file=sys.stderr)
        sys.exit(2)

    milestones = parse_milestones(milestones_path)
    all_ids = set(tickets.keys())

    all_errors, all_warnings = [], []
    for tid, t in tickets.items():
        errs, warns = validate_one(tid, t, all_ids, reqs, milestones, repo_root)
        all_errors.extend(errs)
        all_warnings.extend(warns)

    if all_errors:
        print(json.dumps({
            "valid": False,
            "errors": all_errors,
            "warnings": all_warnings,
        }, indent=2))
        sys.exit(1)

    if all_warnings:
        print(f"OK with {len(all_warnings)} warning(s) on {len(tickets)} ticket(s):")
        for w in all_warnings:
            val = w.get("value", "")
            detail = w.get("detail", "")
            print(f"  {w['ticket_id']} [{w['kind']}]: {detail} {val}".strip())
        sys.exit(0)

    print(f"OK: {len(tickets)} tickets validated")
    sys.exit(0)


if __name__ == "__main__":
    main()
