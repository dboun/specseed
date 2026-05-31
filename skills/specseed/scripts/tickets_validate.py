"""
tickets_validate.py

Validates the TICKET tier (.specseed/project_management/tickets.json), assembled
by tickets_assemble.py from the per-ticket folders. Tickets are the PM /
non-technical layer: they carry satisfies_reqs + the critical-path depends_on
DAG, group issues, and roll up to epics.

Kept SEPARATE from issues_validate.py on purpose — tickets and issues may live
in different stores once tool integrations land, so each tier validates the
refs it can resolve and degrades gracefully when the other tier is absent.

Checks:
 1. ID format /^[A-Z]+-\\d{4,}$/, unique, == folder name (assemble enforces the
    last; re-checked here)
 2. Required fields: title, type, priority, status
 3. Enums: type ∈ {feature,bug,chore,spike}; priority ∈ {high,medium,low};
    status ∈ {todo,in_progress,blocked,done,deprecated}
 4. depends_on refs exist in tickets.json; no cycles
 5. satisfies_reqs refs exist in reqs.json (warn if empty)
 6. epic ref (if non-null) has a folder under <pm-dir>/epics/ (skipped if absent)
 7. issues refs exist in issues.json AND each issue's `ticket` points back here
    (skipped if issues.json absent)

Exit: 0 OK (or warnings only), 1 errors, 2 missing input.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from graphlib import TopologicalSorter, CycleError

ID_RE = re.compile(r"^[A-Z]+-\d{4,}$")
VALID_TYPES = {"feature", "bug", "chore", "spike"}
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_STATUSES = {"todo", "in_progress", "blocked", "done", "deprecated"}
REQUIRED_FIELDS = ["title", "type", "priority", "status"]


def validate_one(tid, t, all_ids, reqs, epics_dir, issues):
    errors, warnings = [], []

    if not ID_RE.match(tid):
        errors.append({"id": tid, "kind": "id_format",
                       "detail": "must match /^[A-Z]+-\\d{4,}$/"})
    if not isinstance(t, dict):
        errors.append({"id": tid, "kind": "schema", "detail": "entry not an object"})
        return errors, warnings

    for field in REQUIRED_FIELDS:
        if field not in t:
            errors.append({"id": tid, "kind": "missing_field", "field": field})
    if any(e["kind"] == "missing_field" for e in errors):
        return errors, warnings

    if t["type"] not in VALID_TYPES:
        errors.append({"id": tid, "kind": "enum", "field": "type", "value": t["type"]})
    if str(t["priority"]).lower() not in VALID_PRIORITIES:
        errors.append({"id": tid, "kind": "enum", "field": "priority", "value": t["priority"]})
    if t["status"] not in VALID_STATUSES:
        errors.append({"id": tid, "kind": "enum", "field": "status", "value": t["status"]})

    deps = t.get("depends_on", []) or []
    if not isinstance(deps, list):
        errors.append({"id": tid, "kind": "schema", "field": "depends_on",
                       "detail": "must be a list"})
    else:
        for d in deps:
            if d not in all_ids:
                errors.append({"id": tid, "kind": "dangling_ref",
                               "field": "depends_on", "value": d})

    sat = t.get("satisfies_reqs", []) or []
    if not isinstance(sat, list):
        errors.append({"id": tid, "kind": "schema", "field": "satisfies_reqs",
                       "detail": "must be a list"})
    elif not sat:
        warnings.append({"id": tid, "kind": "empty_satisfies_reqs",
                         "detail": "ticket satisfies no requirement"})
    else:
        for r in sat:
            if r not in reqs:
                errors.append({"id": tid, "kind": "dangling_ref",
                               "field": "satisfies_reqs", "value": r})

    epic = t.get("epic")
    if epic and epics_dir is not None:
        if not (epics_dir / str(epic)).is_dir():
            errors.append({"id": tid, "kind": "dangling_ref", "field": "epic",
                           "value": epic, "detail": "no folder under epics/"})

    listed = t.get("issues", []) or []
    if listed and issues is not None:
        for iid in listed:
            if iid not in issues:
                errors.append({"id": tid, "kind": "dangling_ref", "field": "issues",
                               "value": iid, "detail": "not in issues.json"})
            elif issues[iid].get("ticket") != tid:
                errors.append({"id": tid, "kind": "back_ref",
                               "field": "issues", "value": iid,
                               "detail": f"issue.ticket={issues[iid].get('ticket')!r}, "
                                         f"expected {tid!r}"})
    return errors, warnings


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm-dir", default=".specseed/project_management")
    p.add_argument("--tickets-path", default=None)
    p.add_argument("--issues-path", default=None)
    p.add_argument("--reqs-path", default=".specseed/spec/reqs.json")
    args = p.parse_args()

    pm = Path(args.pm_dir)
    tickets_path = Path(args.tickets_path) if args.tickets_path else pm / "tickets.json"
    issues_path = Path(args.issues_path) if args.issues_path else pm / "issues.json"
    reqs_path = Path(args.reqs_path)
    epics_dir = pm / "epics"

    if not tickets_path.exists():
        print(f"ERROR: {tickets_path} not found (run tickets_assemble.py)", file=sys.stderr)
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
        print("ERROR: tickets.json root must be an object", file=sys.stderr)
        sys.exit(2)

    issues = None
    if issues_path.exists():
        try:
            issues = json.loads(issues_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issues = None
    epics = epics_dir if epics_dir.exists() else None

    all_ids = set(tickets.keys())
    all_errors, all_warnings = [], []
    for tid, t in tickets.items():
        errs, warns = validate_one(tid, t, all_ids, reqs, epics, issues)
        all_errors.extend(errs)
        all_warnings.extend(warns)

    # Cycle check across the ticket DAG
    try:
        TopologicalSorter({tid: set(t.get("depends_on", []) or [])
                           for tid, t in tickets.items()
                           if isinstance(t, dict)}).prepare()
    except CycleError as e:
        all_errors.append({"id": "*", "kind": "cycle", "detail": str(e.args[1])})

    if all_errors:
        print(json.dumps({"valid": False, "errors": all_errors,
                          "warnings": all_warnings}, indent=2))
        sys.exit(1)
    if all_warnings:
        print(f"OK with {len(all_warnings)} warning(s) on {len(tickets)} ticket(s):")
        for w in all_warnings:
            print(f"  {w['id']} [{w['kind']}]: {w.get('detail', '')} "
                  f"{w.get('value', '')}".strip())
        sys.exit(0)
    print(f"OK: {len(tickets)} tickets validated")
    sys.exit(0)


if __name__ == "__main__":
    main()
