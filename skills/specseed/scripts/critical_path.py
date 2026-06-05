"""critical_path.py - longest dependency chain over the ticket delta (stdlib only).

A skill helper. The critical path is the longest chain of dependent tickets by summed
effort; it sets the minimum project duration. Computed at the **ticket tier,
project-level** (over ALL tickets, never per sprint) per ``references/work-breakdown.md``.

Input is a ticket map ``{id: {"depends_on": [...], "effort": <hours>}}``. It also
accepts a ``plan.json`` carrying a ``tickets`` key (map or list of nodes), or a list of
nodes each with an ``id`` (see ``load_tickets``).

CLI: ``python3 critical_path.py <file>`` (or pipe JSON on stdin). Prints
``{"critical_path": [...], "total_effort": <hours>}``. Exit 1 on a dependency cycle.
"""

from __future__ import annotations

import json
import sys
from graphlib import CycleError, TopologicalSorter


def load_tickets(obj) -> dict:
    """Normalize various shapes into ``{id: {depends_on, effort, ...}}``."""
    if isinstance(obj, dict) and "tickets" in obj:
        obj = obj["tickets"]
    if isinstance(obj, dict) and "creates" in obj:  # a raw plan.json
        nodes = [c for c in obj["creates"] if c.get("tier") == "ticket"]
        return {n["id"]: n for n in nodes if "id" in n}
    if isinstance(obj, list):
        return {n["id"]: n for n in obj if "id" in n}
    if isinstance(obj, dict):
        return obj
    raise ValueError("unrecognized tickets input")


def critical_path(tickets: dict) -> dict:
    """Longest-by-effort dependency chain. Raises ValueError on a cycle."""
    deps = {tid: set(t.get("depends_on", [])) & set(tickets) for tid, t in tickets.items()}
    try:
        order = list(TopologicalSorter(deps).static_order())
    except CycleError as exc:
        raise ValueError(f"cycle among tickets: {exc.args[1]}") from exc

    effort = {tid: float(t.get("effort", 0) or 0) for tid, t in tickets.items()}
    best = {tid: 0.0 for tid in tickets}   # best chain effort ENDING at tid
    prev: dict[str, str | None] = {tid: None for tid in tickets}
    for tid in order:  # deps come before dependents
        base = effort[tid]
        chosen, chosen_cost = None, -1.0
        for d in deps[tid]:
            if best[d] > chosen_cost:
                chosen, chosen_cost = d, best[d]
        best[tid] = base + (chosen_cost if chosen is not None else 0.0)
        prev[tid] = chosen

    if not tickets:
        return {"critical_path": [], "total_effort": 0.0}
    end = max(best, key=lambda t: best[t])
    chain = []
    node: str | None = end
    while node is not None:
        chain.append(node)
        node = prev[node]
    chain.reverse()
    return {"critical_path": chain, "total_effort": best[end]}


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] != "-":
        raw = open(argv[1], encoding="utf-8").read()
    elif (len(argv) >= 2 and argv[1] == "-") or not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        print("Usage: critical_path.py <file>  (or pipe JSON on stdin)", file=sys.stderr)
        return 2
    tickets = load_tickets(json.loads(raw))
    try:
        result = critical_path(tickets)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
