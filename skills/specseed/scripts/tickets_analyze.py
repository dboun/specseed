"""Critical path + topo order + next-todo selector from ticket DAG."""
import json
import sys
from graphlib import TopologicalSorter, CycleError


def analyze(tickets: dict) -> dict:
    """Compute critical path + topo order + next todo from ticket DAG.

    Input schema (only the fields used; tickets may carry more):
        {
            "<ticket_id>": {
                "effort_hours": <number>,                # required
                "depends_on": ["<ticket_id>", ...],      # optional, default []
                "status": "todo|in_progress|blocked|done|deprecated"  # optional, default "todo"
            },
            ...
        }

    Returns:
        {
            "critical_path": ["<ticket_id>", ...],     # longest chain by effort
            "critical_path_effort_hours": <number>,    # sum along that chain
            "build_order": ["<ticket_id>", ...],       # one valid topo order
            "next_todo": "<ticket_id>" | null          # first not-yet-completed,
                                                       # not-in-progress ticket in
                                                       # build_order whose deps are
                                                       # all done
        }

    Raises:
        ValueError on unknown dep reference, missing effort_hours.
        graphlib.CycleError on cyclic deps.
    """
    # Empty input guard
    if not tickets:
        return {
            "critical_path": [],
            "critical_path_effort_hours": 0,
            "build_order": [],
            "next_todo": None,
        }

    # Validate refs and required fields
    for tid, t in tickets.items():
        if "effort_hours" not in t:
            raise ValueError(f"{tid} missing required field 'effort_hours'")
        for d in t.get("depends_on", []):
            if d not in tickets:
                raise ValueError(f"{tid} depends on unknown {d}")

    # Topo sort (raises CycleError if cyclic)
    ts = TopologicalSorter({tid: set(t.get("depends_on", [])) for tid, t in tickets.items()})
    order = list(ts.static_order())

    # Longest path ending at each node (by effort)
    # finish[n] = max over deps of finish[dep], + effort[n]
    finish = {}
    pred = {}
    for n in order:  # topo order: deps processed first
        deps = tickets[n].get("depends_on", [])
        if not deps:
            finish[n] = tickets[n]["effort_hours"]
            pred[n] = None
        else:
            best = max(deps, key=lambda d: finish[d])
            finish[n] = finish[best] + tickets[n]["effort_hours"]
            pred[n] = best

    # Sink with max finish = end of critical path
    end = max(finish, key=finish.get)
    total = finish[end]
    path = []
    cur = end
    while cur is not None:
        path.append(cur)
        cur = pred[cur]
    path.reverse()

    # Next todo: walk build_order, pick first ticket where:
    #   - status is "todo" or "blocked" (i.e. not done/deprecated/in_progress)
    #   - all depends_on are in {done, deprecated}
    done_set = {
        tid for tid in tickets
        if tickets[tid].get("status", "todo") in ("done", "deprecated")
    }
    next_todo = None
    for tid in order:
        status = tickets[tid].get("status", "todo")
        if status in ("done", "deprecated", "in_progress"):
            continue
        deps = tickets[tid].get("depends_on", [])
        if all(d in done_set for d in deps):
            next_todo = tid
            break

    return {
        "critical_path": path,
        "critical_path_effort_hours": total,
        "build_order": order,
        "next_todo": next_todo,
    }


if __name__ == "__main__":
    # Usage: `python3 tickets_analyze.py tickets.json` OR pipe JSON to stdin.
    if len(sys.argv) == 1 and not sys.stdin.isatty():
        data = json.load(sys.stdin)
    elif len(sys.argv) == 2:
        data = json.load(open(sys.argv[1]))
    else:
        print("Usage: python3 tickets_analyze.py <tickets.json>  (or pipe JSON via stdin)",
              file=sys.stderr)
        sys.exit(2)

    try:
        print(json.dumps(analyze(data), indent=2))
    except CycleError as e:
        print(json.dumps({"error": "cycle detected", "details": str(e)}), file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(json.dumps({"error": "validation", "details": str(e)}), file=sys.stderr)
        sys.exit(1)
