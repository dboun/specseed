"""dependencies_validate.py - validate the work-breakdown links in plan.json (stdlib only).

A skill helper, sibling to ``requirements_analyze.py`` but one tier down: it checks the
body links the breakdown writes into the work items in ``plan.json.creates``, BEFORE
``apply.py`` creates anything. The runtime (``executing/dispatch.py`` for the dependency
gate, ``entities/entity_base`` for the tree) only acts on a link it can PARSE from the
post body, so a typo'd, cyclic, or simply-missing link silently lets a dependent run too
early OR drops a post into the orphan bucket. This catches both mechanically instead of
trusting the agent to have gotten it right. Two link kinds are checked:
  * ``Depends on:`` - the issue->issue dependency DAG (the gate);
  * parent links (``Ticket:``/``Epic:``/``Parent:``) - the epic->ticket->issue tree.

Deps live in body text, not a structured field (see ``references/spec-change-protocol.md``).
A create references another create that has no provider id yet by title placeholder
``#{id:<exact title>}``; ``apply.py`` substitutes the real id at create time. A dep on an
already-existing post uses a literal ``#NN`` / ``#FEAT-001``.

Errors (exit 1, must fix):
  * unlabeled - a create whose ``labels`` carry no tier (``epic``/``ticket``/``issue``)
    or no status (``<tier>:status:<status>``, e.g. ``issue:status:todo``) label. ``apply.py``
    builds each post from ``labels`` ALONE (the ``tier`` field is advisory, never written to
    the tracker), so without them the runtime can't resolve the post's tier/status:
    ``decide_intent`` returns NONE and every event on it (assignment, comments, labels) is a
    silent no-op - the post is created but never worked, with no error to show for it;
  * dangling - a ``#{id:Title}`` dep whose Title matches no created item (apply.py can't
    substitute it, so the link evaporates and the gate never holds);
  * malformed - a ``Depends on:`` line with a bare ``{id:..}`` (no ``#``) or no ref at all
    (the body parser needs the ``#``; bare refs are dropped);
  * cycle - the created issues' intra-plan deps form a loop (nothing could ever start);
  * orphan - a decomposed issue with no ticket link, or a ticket with no epic link (the
    post lands loose, unreachable from the roadmap); a parent ``#{id:Title}`` that
    matches no created item; or a parent link pointing at the wrong tier. A lone issue
    in a plan that creates no tickets (a standalone bug/chore) is exempt.

Warnings (exit 0, but surfaced for the route to resolve): an issue that LOOKS like it
consumes a sibling - a ``type:qa`` issue, or one whose title/body reads as tests - yet
declares no dep at all. This is the exact hole that lets a "tests for X" issue run before
X is on primary. The route confirms the dep or justifies its absence.

Input is a ``plan.json`` with a ``creates`` list of work items, each
``{"tier": "epic"|"ticket"|"issue", "title": str, "body": str, ...}`` (the links are
parsed out of ``body``). CLI: ``python3 dependencies_validate.py <plan.json>`` or pipe
the plan on stdin. Prints a result JSON:

    {"ok": bool, "errors": [str], "warnings": [str], "topo_order": [title, ...],
     "stats": {"n_created": int, "n_issues": int, "n_dep_edges": int}}

Exit 0 if no errors, 1 if errors. ``ok`` requires an empty ``errors``; ``warnings`` are
surfaced for the route to resolve (confirm the dep or justify its absence), not fatal.
"""

from __future__ import annotations

import json
import re
import sys
from graphlib import CycleError, TopologicalSorter

_WORK_TIERS = ("epic", "ticket", "issue")

# A "Depends on:" segment, up to the line end or an HTML-comment close. Mirrors the
# runtime body parser (entities/entity_base.py) so we validate exactly what it will read.
_DEPENDS_SEG_RE = re.compile(r"depends\s+on\s*:?\s*(.+)", re.IGNORECASE)
# A title placeholder for a create with no id yet: #{id:<exact title>}.
_PLACEHOLDER_RE = re.compile(r"#\{id:([^}]+)\}")
# A bare placeholder MISSING the leading # - invalid, the gate won't enforce it.
_BARE_PLACEHOLDER_RE = re.compile(r"(?<!#)\{id:([^}]+)\}")
# A literal ref to an already-existing post: #NN or #FEAT-001.
_LITERAL_ID_RE = re.compile(r"#\s*([0-9]+|[A-Za-z]+-[0-9]+)")
# An issue that reads as tests - so its missing dep is suspicious.
_TESTS_RE = re.compile(r"\btest(s|ing|ed)?\b", re.IGNORECASE)
# Parent link in a body. Mirrors the runtime parser (entities/entity_base._PARENT_RE):
# an issue links its ticket, a ticket its epic, via `Ticket:`/`Epic:`/`Parent:`. In a
# plan the target may not have an id yet, so the ref is a `#{id:Title}` placeholder
# (apply.py substitutes it) OR a literal `#NN` to an already-existing post.
_PARENT_PLACEHOLDER_RE = re.compile(r"\b(?:Ticket|Epic|Parent)\s*:\s*#\{id:([^}]+)\}", re.IGNORECASE)
_PARENT_LITERAL_RE = re.compile(r"\b(?:Ticket|Epic|Parent)\s*:\s*#\s*([0-9]+|[A-Za-z]+-[0-9]+)", re.IGNORECASE)


def _tier_of(item: dict) -> str | None:
    """The work tier of a create entry, from its explicit ``tier`` or its labels."""
    tier = item.get("tier")
    if tier in _WORK_TIERS:
        return tier
    for label in item.get("labels") or []:
        name = str(label)
        bare = name.split(":", 1)[0] if name.startswith("tier:") else name
        if bare in _WORK_TIERS:
            return bare
    return None


def _tier_from_labels(labels: list) -> str | None:
    """The work tier resolvable from a create's LABELS alone (what ``apply.py`` writes).

    Mirrors ``entities/entity_base.Entity.tier_from_labels``: a ``tier:<t>`` label, a bare
    ``epic``/``ticket``/``issue`` label, or the tier embedded in the canonical
    ``<tier>:status:<status>`` label. The ``tier`` *field* is deliberately NOT consulted -
    apply.py creates the post from ``labels`` only, so only a label makes it workable. Every
    form is restricted to a real work tier, so a stray ``tier:garbage`` doesn't pass the gate.
    """
    for label in labels:
        name = str(label)
        if name.startswith("tier:") and name[len("tier:"):] in _WORK_TIERS:
            return name[len("tier:"):]
    for label in labels:
        if str(label) in _WORK_TIERS:
            return str(label)
    for label in labels:
        name = str(label)
        idx = name.find(":status:")
        if idx != -1 and name[:idx] in _WORK_TIERS:
            return name[:idx]
    return None


def _status_from_labels(labels: list) -> str | None:
    """The status resolvable from a create's LABELS - a bare ``status:<s>`` or the canonical
    ``<tier>:status:<status>`` form. Mirrors ``entities/entity_base.Entity.status_from_labels``."""
    for label in labels:
        name = str(label)
        if name.startswith("status:"):
            return name[len("status:"):] or None
        idx = name.find(":status:")
        if idx != -1:
            return name[idx + len(":status:"):] or None
    return None


def _depends_segments(body: str) -> list[str]:
    """Every ``Depends on:`` segment in a body, each trimmed at newline / comment close."""
    out: list[str] = []
    for m in _DEPENDS_SEG_RE.finditer(body or ""):
        seg = m.group(1)
        for stop in ("-->", "\n"):
            idx = seg.find(stop)
            if idx != -1:
                seg = seg[:idx]
        out.append(seg)
    return out


def _parse_deps(body: str) -> dict:
    """Pull dep refs out of a body. Returns placeholders (titles), literals (existing
    ids), bare (malformed, no #), and empty (a Depends-on line carrying no ref)."""
    placeholders: list[str] = []
    literals: list[str] = []
    bare: list[str] = []
    empty = False
    for seg in _depends_segments(body):
        seg_ph = [t.strip() for t in _PLACEHOLDER_RE.findall(seg)]
        seg_bare = [t.strip() for t in _BARE_PLACEHOLDER_RE.findall(seg)]
        # A placeholder is `#{id:..}`; strip them before hunting literal #NN so the
        # leading # of a placeholder is never miscounted as a literal id.
        seg_no_ph = _PLACEHOLDER_RE.sub("", seg)
        seg_lit = [t.strip() for t in _LITERAL_ID_RE.findall(seg_no_ph)]
        placeholders.extend(seg_ph)
        literals.extend(seg_lit)
        bare.extend(seg_bare)
        if not seg_ph and not seg_lit and not seg_bare:
            empty = True
    return {"placeholders": placeholders, "literals": literals, "bare": bare, "empty": empty}


def _parse_parent(body: str) -> dict:
    """The parent link in a body, if any. Placeholder (a created title) takes precedence
    over a literal id (an existing post). Returns {placeholder, literal} (each or None)."""
    ph = _PARENT_PLACEHOLDER_RE.search(body or "")
    if ph:
        return {"placeholder": ph.group(1).strip(), "literal": None}
    lit = _PARENT_LITERAL_RE.search(body or "")
    if lit:
        return {"placeholder": None, "literal": lit.group(1).strip()}
    return {"placeholder": None, "literal": None}


def validate(plan: dict) -> dict:
    """Validate the issue dependency graph in a ``plan.json``.

    Returns {ok, errors, warnings, topo_order, stats}. ``ok`` is False (and exit 1) on any
    dangling / malformed / cyclic dep; warnings (likely-missing dep) never fail the run.
    """
    errors: list[str] = []
    warnings: list[str] = []

    creates = plan.get("creates") or []
    # Title -> tier for resolving placeholders. Titles are the substitution key apply.py
    # uses, so a dep placeholder must match a created title EXACTLY.
    by_title: dict[str, str] = {}
    items: list[dict] = []
    for entry in creates:
        if not isinstance(entry, dict):
            warnings.append("skipped a non-object entry in creates")
            continue
        title = str(entry.get("title") or "").strip()
        label_list = [str(x) for x in entry.get("labels") or []]
        who = repr(title) if title else "a create with no title"
        # apply.py creates each post from `labels` ALONE - the `tier` field never reaches the
        # tracker. A post lacking a tier or status label is unworkable: the runtime can't
        # resolve its tier/status, so decide_intent returns NONE and every event on it
        # (assignment, comments, labels) is a silent no-op. Catch it here, before apply.py runs.
        if _tier_from_labels(label_list) is None:
            errors.append(
                "{0} has no tier label in `labels` (one of `epic`/`ticket`/`issue`, or the "
                "tier in a `<tier>:status:<status>` label); apply.py builds the post from "
                "`labels`, so without one the post is unworkable".format(who))
        if _status_from_labels(label_list) is None:
            errors.append(
                "{0} has no status label in `labels` (e.g. `issue:status:todo`); without one "
                "the runtime can't act on the post".format(who))
        tier = _tier_of(entry)
        if tier is None:
            continue
        item = {
            "title": title,
            "tier": tier,
            "labels": label_list,
            "body": str(entry.get("body") or ""),
            "deps": _parse_deps(str(entry.get("body") or "")),
        }
        items.append(item)
        if title:
            by_title[title] = tier

    # Edges among created items, keyed by title - the only deps that can cycle WITHIN the
    # plan. A literal #NN points at an already-existing post, so it can't close a loop here.
    graph: dict[str, set] = {}
    for item in items:
        title = item["title"]
        if not title:
            errors.append("a created {0} has no title (can't be referenced or linked)".format(item["tier"]))
            continue
        graph.setdefault(title, set())
        deps = item["deps"]
        for dep_title in deps["placeholders"]:
            if dep_title in by_title:
                graph[title].add(dep_title)
            else:
                errors.append(
                    "{0!r} depends on #{{id:{1}}} but no created item has that title "
                    "(apply.py can't resolve it; the dep would vanish)".format(title, dep_title)
                )
        for bad in deps["bare"]:
            errors.append(
                "{0!r} has a malformed dep `{{id:{1}}}` - the leading # is mandatory "
                "(`#{{id:{1}}}`), else the gate ignores it".format(title, bad)
            )
        if deps["empty"]:
            errors.append("{0!r} has a `Depends on:` line with no #ref".format(title))

    topo: list[str] = []
    try:
        topo = list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        errors.append("dependency cycle among created issues: {0}".format(exc.args[1]))

    # Hierarchy links: an issue belongs to a ticket, a ticket to an epic. The runtime
    # places posts in the tree ONLY from a parseable parent link (entities/entity_base.
    # parse_parent), so a missing or dangling one drops the post into the orphan bucket
    # ("Issues without a ticket"). Same failure mode as a dropped dep: catch it here.
    # Standalone issues (a lone bug/chore filed with no ticket in the plan) are exempt -
    # the rule is "if you DECOMPOSED it, wire it", so an issue must link a ticket only
    # when the plan also creates tickets; a ticket must always link an epic.
    n_tickets_created = sum(1 for i in items if i["tier"] == "ticket")
    for item in items:
        title = item["title"]
        if not title:
            continue  # already errored above
        tier = item["tier"]
        if tier == "epic":
            continue  # epics have no parent
        parent = _parse_parent(item["body"])
        kind = "ticket" if tier == "issue" else "epic"
        link = "Ticket: #NN" if tier == "issue" else "Epic: #NN"
        if parent["placeholder"]:
            ptier = by_title.get(parent["placeholder"])
            if ptier is None:
                errors.append(
                    "{0!r} links parent #{{id:{1}}} but no created item has that title "
                    "(apply.py can't resolve it; the post would orphan)".format(title, parent["placeholder"])
                )
            elif ptier != kind:
                errors.append(
                    "{0!r} ({1}) links a parent that is a {2}, not a {3}".format(title, tier, ptier, kind)
                )
        elif not parent["literal"]:
            # No parent link at all. A ticket always needs an epic; an issue needs a
            # ticket only when this plan is a decomposition (it creates tickets).
            if tier == "ticket" or n_tickets_created:
                errors.append(
                    "{0!r} ({1}) has no parent {2} link (`{3}`); the runtime can't place "
                    "it, so it lands as a loose post. Decomposed work must link its parent.".format(
                        title, tier, kind, link)
                )

    # Likely-missing dep: a consumer-shaped issue with no declared dep at all. A warning,
    # not an error - a tests issue MAY legitimately cover code already on primary.
    for item in items:
        if item["tier"] != "issue":
            continue
        deps = item["deps"]
        if deps["placeholders"] or deps["literals"]:
            continue
        is_qa = any(lbl == "type:qa" or lbl.endswith(":qa") for lbl in item["labels"])
        looks_tests = bool(_TESTS_RE.search(item["title"]))
        if is_qa or looks_tests:
            kind = "QA" if is_qa else "tests"
            warnings.append(
                "{0!r} reads as a {1} issue but declares no `Depends on:` - confirm it "
                "doesn't need the issue it exercises, or add the dep".format(item["title"], kind)
            )

    n_issues = sum(1 for i in items if i["tier"] == "issue")
    n_edges = sum(len(v) for v in graph.values())
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "topo_order": topo,
        "stats": {"n_created": len(items), "n_issues": n_issues, "n_dep_edges": n_edges},
    }


def main(argv: list[str]) -> int:
    usage = "Usage: dependencies_validate.py <plan.json>  (or pipe plan on stdin)"
    if len(argv) == 1 and not sys.stdin.isatty():
        text = sys.stdin.read()
        if not text.strip():
            print(usage, file=sys.stderr)
            return 2
        plan = json.loads(text)
    elif len(argv) == 2:
        plan = json.loads(open(argv[1], encoding="utf-8").read())
    else:
        print(usage, file=sys.stderr)
        return 2
    result = validate(plan)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
