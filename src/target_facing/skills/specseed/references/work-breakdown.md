# Work breakdown

How a spec becomes epics -> tickets -> issues. In this build the breakdown lives
as **remote posts** (`references/remote-posts.md`), planned into `plan.json` and
applied by `apply.py`. There is no local folder tree and no assemble/validate
script; you compute the structure and the critical path yourself.

## Three tiers

| Tier | Nature | Holds |
|------|--------|-------|
| epic | PM, non-technical | an outcome; groups tickets |
| ticket | PM, user-visible value | story, description, product acceptance criteria, `satisfies_reqs` |
| issue | technical, claimable | technical acceptance criteria, artifacts, plan notes |

An issue may belong to a ticket; a ticket may belong to an epic. No separate
"story" tier (a user story is a section in a ticket body). Relationships are body
links, not labels.

## INVEST (issues)

Independent, Negotiable, Valuable, Estimable, Small (one focused agent session),
Testable (clear pass/fail technical acceptance criteria).

## Acceptance criteria != requirements

- Requirements (`SRS-...`): what the system does. Persistent.
- Ticket acceptance criteria: product-level, proves the ticket's value shipped.
- Issue acceptance criteria: technical, proves the issue is done.

A ticket may satisfy 1 to 8 reqs. More than 8 -> split the ticket. Do not force a
criterion-to-req 1:1.

## Sizing an issue

1. **Semantic:** you can name 2 to 6 technical acceptance criteria with
   confidence. `<2` -> too small, merge. `>6` -> too big, split.
2. **Mechanical:** the work fits one focused agent session. Too big -> split even
   if the semantic check passed.

**Vertical slice:** cut each issue so it produces an observable behavior change
in one run/request. If you can say in one sentence what looks different after it
ships, it is a valid slice. Slices may skip layers; the rule is observability.

**Integration issue:** insert one before any point where 2+ parallel branches
converge (its dependencies span different branches), especially across 2+
components.

## Formation

1. From the spec, name epics + ticket titles.
2. Flesh each ticket: story, description, product acceptance criteria; set
   `satisfies_reqs`; set ticket dependencies (the critical-path DAG).
3. Decompose each ticket into issues (vertical slices, INVEST, sizing).
4. Set issue artifacts (code paths, tests) and plan notes.
5. Insert integration issues at merge points.
6. Body-link both directions (epic <-> tickets, ticket <-> issues, depends-on).
7. Label every post: tier + status. Epics/tickets at `:status:todo`; **issues at
   `:status:awaiting_approval`** (the approval gate, see
   `spec-change-protocol.md`). New issues never start `todo`.

Ticket/issue/epic prose gets the humanizer pass (neutral, concrete, no em
dashes). Labels and req ids are machine text, exempt.

## Critical path (ticket tier, project-level)

Compute it over **all** tickets, not per sprint: the dependency DAG crosses
sprint boundaries, so a per-sprint view hides the real bottleneck. The critical
path is the longest dependency chain by summed effort; it sets the minimum
duration. Schedule its tickets first. "Important" is not the same as "on the
critical path". A very long CP usually means tickets are too narrow or deps are
artificial: rebalance. Record the CP in `plan.json` and reflect it in the
ROADMAP/TIMELINE dashboard bodies.

## Sprints

A sprint is a time-boxed batch of tickets (~one week, ~168h soft budget). It is
**orthogonal to epics**: an epic groups by outcome, a sprint groups by time. A
ticket has one epic and one sprint.

In this build a sprint is expressed as a `sprint:<id>` label on each member
ticket post plus the `Current sprint` dashboard body; `TIMELINE` lists sprints in
execution order. ROADMAP stays strategic and never lists sprints.

Packing rule: place a ticket only after all the tickets it depends on are in the
same or an earlier sprint (**no backward sprint dependency**). Within that
constraint, pull critical-path tickets early, then keep same-epic tickets
together (cohesion), then by priority, filling toward the budget. One sprint is
`in_progress` at a time (the claim target for impl agents).

## Risk + gating (light)

Flag issues whose work is risky or irreversible (data migration, destructive
ops, external side effects) so an impl agent treats them carefully; note the flag
in the issue body. This skill does not run the approval engine itself (the
executor does); it only marks risk and births issues `awaiting_approval` so a
human approves before any work starts (see `spec-change-protocol.md`).
