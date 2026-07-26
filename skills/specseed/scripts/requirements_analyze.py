"""requirements_analyze.py - validate the requirement DAG (stdlib only).

A skill helper. Detects cycles, dangling deps, and (optionally) orphan reqs over a
``reqs.json`` produced by ``requirements_generate_json.py``. When it reports a cycle,
the route resolves it with one of the standard moves (split node / extract interface /
reorder) from ``references/work-breakdown.md`` and re-runs until clean.

CLI: ``python3 requirements_analyze.py <reqs.json> [tickets.json]`` or pipe reqs on
stdin. Passing ``tickets.json`` (a map ``{ticket_id: {"satisfies_reqs": [...]}}``)
additionally checks coverage. Prints a result JSON:

    {"ok": bool, "errors": [str], "warnings": [str], "topo_order": [req_id, ...],
     "stats": {"n_reqs": int, "n_roots": int, "n_leaves": int}}

``errors`` = cycles + deps on an unknown req (+ tickets satisfying an unknown req when
``tickets.json`` is given); ``warnings`` = reqs no ticket satisfies; ``topo_order`` = a
valid dependency order (empty when a cycle blocks it). Exit 0 if ``ok``, 1 if errors.
"""

from __future__ import annotations

import json
import sys
from graphlib import CycleError, TopologicalSorter


def analyze(reqs: dict, tickets: dict | None = None) -> dict:
    """Validate the requirement DAG; surface cycles, dangling deps, orphans.

    Returns {ok, errors, warnings, topo_order, stats}. ``tickets`` (a map with a
    ``satisfies_reqs`` list per ticket) enables orphan detection.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for rid, r in reqs.items():
        for d in r.get("depends_on", []):
            if d not in reqs:
                errors.append(f"{rid} depends on unknown {d}")

    topo: list[str] = []
    try:
        ts = TopologicalSorter({rid: set(r.get("depends_on", [])) for rid, r in reqs.items()})
        topo = list(ts.static_order())
    except CycleError as exc:
        errors.append(f"cycle detected: {exc.args[1]}")

    if tickets is not None:
        satisfied: set[str] = set()
        for t in tickets.values():
            satisfied.update(t.get("satisfies_reqs", []))
        for rid in reqs:
            if rid not in satisfied:
                warnings.append(f"{rid} not satisfied by any ticket")
        for tid, t in tickets.items():
            for rid in t.get("satisfies_reqs", []):
                if rid not in reqs:
                    errors.append(f"ticket {tid} satisfies unknown req {rid}")

    has_dependents = {d for r in reqs.values() for d in r.get("depends_on", [])}
    roots = [rid for rid, r in reqs.items() if not r.get("depends_on")]
    leaves = [rid for rid in reqs if rid not in has_dependents]

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "topo_order": topo,
        "stats": {"n_reqs": len(reqs), "n_roots": len(roots), "n_leaves": len(leaves)},
    }


def main(argv: list[str]) -> int:
    usage = "Usage: requirements_analyze.py <reqs.json> [tickets.json]  (or pipe reqs on stdin)"
    tickets = None
    if len(argv) == 1 and not sys.stdin.isatty():
        text = sys.stdin.read()
        if not text.strip():
            print(usage, file=sys.stderr)
            return 2
        reqs = json.loads(text)
    elif len(argv) == 2:
        reqs = json.loads(open(argv[1], encoding="utf-8").read())
    elif len(argv) == 3:
        reqs = json.loads(open(argv[1], encoding="utf-8").read())
        tickets = json.loads(open(argv[2], encoding="utf-8").read())
    else:
        print(usage, file=sys.stderr)
        return 2
    result = analyze(reqs, tickets)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
