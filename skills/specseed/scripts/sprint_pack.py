"""sprint_pack.py - cohesion-aware sprint packing of the ticket delta (stdlib only).

A skill helper. Proposes sprints (time-boxed batches of tickets) from a ticket map,
respecting ``references/work-breakdown.md``:

- **hard:** a ticket is placed only after every ticket it depends on is in the same or
  an earlier sprint (no backward sprint dependency, by construction); fill toward the
  budget (default ~168h).
- **soft:** pull critical-path tickets early, then keep same-epic tickets together
  (cohesion), then order by priority.

"Computation proposes, human refines" - this is the proposal; the route does one
bounded refinement pass.

Input shapes match ``critical_path.load_tickets``. Each node may carry ``depends_on``,
``effort``, ``epic``, ``priority`` (high/medium/low), and ``on_critical_path``.

CLI: ``python3 sprint_pack.py <file> [budget]`` (or pipe JSON on stdin). Prints
``{"sprints": [[id, ...], ...]}``.
"""

from __future__ import annotations

import json
import sys

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def load_tickets(obj) -> dict:
    """Normalize various shapes into ``{id: node}`` (see critical_path.load_tickets)."""
    if isinstance(obj, dict) and "tickets" in obj:
        obj = obj["tickets"]
    if isinstance(obj, dict) and "creates" in obj:
        nodes = [c for c in obj["creates"] if c.get("tier") == "ticket"]
        return {n["id"]: n for n in nodes if "id" in n}
    if isinstance(obj, list):
        return {n["id"]: n for n in obj if "id" in n}
    if isinstance(obj, dict):
        return obj
    raise ValueError("unrecognized tickets input")


def _prio(node: dict) -> int:
    return _PRIORITY_RANK.get(str(node.get("priority", "medium")).lower(), 1)


def pack(tickets: dict, budget: float = 168.0) -> dict:
    """Pack ``tickets`` into sprints. Returns ``{"sprints": [[id, ...], ...]}``."""
    deps = {tid: set(t.get("depends_on", [])) & set(tickets) for tid, t in tickets.items()}
    effort = {tid: float(t.get("effort", 0) or 0) for tid, t in tickets.items()}
    placed: set[str] = set()
    sprints: list[list[str]] = []

    while len(placed) < len(tickets):
        sprint: list[str] = []
        sprint_effort = 0.0
        sprint_epic = None
        while True:
            ready = [t for t in tickets if t not in placed and deps[t] <= placed]
            if not ready:
                break
            ready.sort(key=lambda t: (
                not bool(tickets[t].get("on_critical_path")),
                sprint_epic is not None and tickets[t].get("epic") != sprint_epic,
                _prio(tickets[t]),
                str(t),
            ))
            pick = None
            for t in ready:
                if not sprint or sprint_effort + effort[t] <= budget:
                    pick = t
                    break
            if pick is None:  # none of the ready tickets fit; close this sprint
                break
            sprint.append(pick)
            placed.add(pick)
            sprint_effort += effort[pick]
            if sprint_epic is None:
                sprint_epic = tickets[pick].get("epic")
        if not sprint:  # unplaced remain but nothing is ready -> unsatisfiable deps
            break
        sprints.append(sprint)

    return {"sprints": sprints}


def main(argv: list[str]) -> int:
    budget = 168.0
    path = None
    args = argv[1:]
    if args and not args[0].replace(".", "", 1).isdigit():
        path = args.pop(0)
    if args:
        budget = float(args[0])
    if path is not None and path != "-":
        raw = open(path, encoding="utf-8").read()
    elif path == "-" or not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        print("Usage: sprint_pack.py <file> [budget]  (or pipe JSON on stdin)", file=sys.stderr)
        return 2
    tickets = load_tickets(json.loads(raw))
    print(json.dumps(pack(tickets, budget), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
