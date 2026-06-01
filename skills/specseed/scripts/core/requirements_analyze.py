"""Validate requirement DAG: cycles, dangling deps, orphans, topo order."""
from __future__ import annotations

import json
import sys
from graphlib import TopologicalSorter, CycleError


def analyze(reqs: dict, tickets: dict | None = None) -> dict:
    """Validate requirements DAG and surface common issues.

    Input schema (reqs):
        {
            "<req_id>": {
                "text": "<requirement statement>",
                "type": "functional|non_functional|constraint",
                "priority": "must|should|could|wont",
                "depends_on": ["<req_id>", ...]   # other reqs this one needs
            },
            ...
        }

    Note: `verified_by` is intentionally NOT part of the reqs schema anymore.
    Verification traceability is derived on demand by `verification_map.py`
    from `tickets.json` (`satisfies_reqs` x `artifacts.tests`).

    Optional tickets arg (same schema as ticket analysis) used to detect
    orphan reqs (reqs not satisfied by any ticket). Each ticket must carry
    a `satisfies_reqs: [<req_id>, ...]` field.

    Returns:
        {
            "ok": <bool>,                          # true if no errors
            "errors": [<str>, ...],                # cycles, dangling deps
            "warnings": [<str>, ...],              # orphans
            "topo_order": ["<req_id>", ...],       # empty if cycle
            "stats": {"n_reqs": ..., "n_roots": ..., "n_leaves": ...}
        }
    """
    errors, warnings = [], []

    # 1. Dangling deps
    for rid, r in reqs.items():
        for d in r.get("depends_on", []):
            if d not in reqs:
                errors.append(f"{rid} depends on unknown {d}")

    # 2. Topo sort (cycle detection)
    topo = []
    try:
        ts = TopologicalSorter({rid: set(r.get("depends_on", [])) for rid, r in reqs.items()})
        topo = list(ts.static_order())
    except CycleError as e:
        errors.append(f"cycle detected: {e.args[1]}")

    # 3. Orphan reqs (no ticket satisfies them) — only if tickets given
    if tickets is not None:
        satisfied = set()
        for t in tickets.values():
            satisfied.update(t.get("satisfies_reqs", []))
        for rid in reqs:
            if rid not in satisfied:
                warnings.append(f"{rid} not satisfied by any ticket")
        # And reverse: ticket references unknown req
        for tid, t in tickets.items():
            for rid in t.get("satisfies_reqs", []):
                if rid not in reqs:
                    errors.append(f"ticket {tid} satisfies unknown req {rid}")

    # 4. Stats
    roots = [rid for rid, r in reqs.items() if not r.get("depends_on")]
    has_dependents = {d for r in reqs.values() for d in r.get("depends_on", [])}
    leaves = [rid for rid in reqs if rid not in has_dependents]

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "topo_order": topo,
        "stats": {"n_reqs": len(reqs), "n_roots": len(roots), "n_leaves": len(leaves)},
    }


if __name__ == "__main__":
    # Usage:
    #   python3 requirements_analyze.py <reqs.json> [tickets.json]
    #   cat reqs.json | python3 requirements_analyze.py
    if len(sys.argv) == 1 and not sys.stdin.isatty():
        reqs = json.load(sys.stdin)
        tickets = None
    elif len(sys.argv) == 2:
        reqs = json.load(open(sys.argv[1]))
        tickets = None
    elif len(sys.argv) == 3:
        reqs = json.load(open(sys.argv[1]))
        tickets = json.load(open(sys.argv[2]))
    else:
        print("Usage: python3 requirements_analyze.py <reqs.json> [tickets.json]  (or pipe reqs via stdin)",
              file=sys.stderr)
        sys.exit(2)

    result = analyze(reqs, tickets)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)
