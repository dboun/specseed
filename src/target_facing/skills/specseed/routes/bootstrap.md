# bootstrap

Greenfield. No spec yet. The spec-change post describes a new project; produce
the first spec under `.specseed/spec/` and the first work breakdown as remote
posts.

Read `references/spec-change-protocol.md`, `references/remote-posts.md`, and
`references/work-breakdown.md` first. `references/component-questions.md` is the
drafting checklist (used to make sure you covered the right dimensions, not for
live Q&A).

## Fires when

`spec-change:bootstrap` on a request post AND no `.specseed/spec/` content yet.
If spec content already exists, this is the wrong route (that is `adapt` /
`plan-next-sprint`); comment that and stop.

## Inputs

The request post (title + body + comments) is the project brief: what it is, who
uses it, hard constraints, scope. The local tracker is otherwise near-empty
(only the seeded dashboards). If the brief is too thin to spec a coherent vision,
use **async clarification** rather than inventing a product.

## Produce the spec (`.specseed/spec/`)

In order, scaled to the brief (a small tool gets a lighter set):

1. `vision.md` — problem, users, scope IN / OUT, success signals. Prose; humanize.
2. Component split — decide functional components from the brief. One SRS+SDD
   per component, or a single pair for a small project.
3. `*-srs.md` (or `srs.md`) — requirement tables. IDs `SRS-<COMP>-NNN`. Columns
   stay machine-formatted (no humanizing the rows). Optional
   `cross-cutting-srs.md` only if security/observability/i18n/a11y materially
   matter (`SRS-CC-NNN`).
4. `sad.md` — architecture: components, interfaces, data flow. Prose sections
   humanized; keep it real, not aspirational.
5. `*-sdd.md` (or `sdd.md`) — the how, per component in scope.
6. `adr.csv` — columns `Decision,Justification`. One row per real decision made.
7. `reqs.json` — the machine projection of the SRS rows (ids, priority,
   component, text, depends_on). You write it directly; keep it consistent with
   the SRS tables.
8. `deployment.md` — only if operations/deployment is clearly in scope.

Spec prose follows the doc-style rules in `SKILL.md` (caveman density +
humanizer + em-dash ban). Settle is implicit here: what you write is the v1.

## Plan the work breakdown (remote posts)

Per `work-breakdown.md`, turn the spec into epics -> tickets -> issues:

- Epics group tickets by outcome. Tickets satisfy requirements and carry
  product-level acceptance criteria. Issues are the technical, claimable units
  with technical acceptance criteria.
- Use the templates in `templates/entity_templates/` for post bodies. Express
  relationships as body links (`remote-posts.md`), `satisfies_reqs` as body
  text.
- Label each post with its tier + `:status:todo`.
- Compute critical path at the ticket tier and a rough first sprint (the
  `Current sprint` board); record it for the dashboards.

Write all of this into `plan.json` as `creates` (+ dashboard `edits` for ROADMAP
/ TIMELINE / Current sprint bodies if dashboards are enabled). Do not mutate the
remote here; that is `apply.py`.

## Finish

1. Write `plan.json` then `apply.py` into the request dir (protocol).
2. Move the request post toward `done` (label swap in the plan).
3. `enqueue_spec_change_run(...)`. Stop. The executor (TODO) runs `apply.py`
   later; the next poll syncs the new posts back into the local cache.

If the brief forced an unresolved product decision, prefer async clarification
over guessing: spec what is certain, ask one focused question, enqueue, stop.
