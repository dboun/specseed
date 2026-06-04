# adapt

The request wants to create or change the spec. If no spec exists yet, this route
creates the first spec from the request. If a spec already exists, it applies a
non-trivial change: add/extend/revise requirements or design, deprecate or retire
a feature. Patch `<specseed_dir>/spec/` and reconcile the affected work posts.

Read `references/spec-change-protocol.md`, `references/remote-posts.md`, and
`references/work-breakdown.md` first.

## Fires when

`spec-change:adapt` on a request post. It is valid whether `<specseed_dir>/spec/`
is empty or already populated. Tiny single edits are `tweak`; appending the next
un-specced slice is `plan-next-sprint`.

## Cold start (no spec yet)

When `<specseed_dir>/spec/` is empty, treat the request post as the first project
brief: what it is, who uses it, hard constraints, and scope. If the brief is too
thin to spec a coherent v1, use async clarification rather than inventing a
product.

Produce the first spec in this order, scaled to the brief:

1. `vision.md` - problem, users, scope IN / OUT, success signals. Prose;
   humanize.
2. Component split - decide functional components from the brief. One SRS+SDD
   per component, or a single pair for a small project.
3. `*-srs.md` (or `srs.md`) - requirement tables. IDs `SRS-<COMP>-NNN`.
   Columns stay machine-formatted. Optional `cross-cutting-srs.md` only if
   security/observability/i18n/a11y materially matter (`SRS-CC-NNN`).
4. `sad.md` - architecture: components, interfaces, data flow. Prose sections
   humanized; keep it real, not aspirational.
5. `*-sdd.md` (or `sdd.md`) - the how, per component in scope.
6. `adr.csv` - columns `Decision,Justification`. One row per real decision made.
7. `reqs.json` - the machine projection of the SRS rows (ids, priority,
   component, text, depends_on). Keep it consistent with the SRS tables.
8. `deployment.md` - only if operations/deployment is clearly in scope.

Plan the first work breakdown as remote posts:

- Epics group tickets by outcome. Tickets satisfy requirements and carry
  product-level acceptance criteria. Issues are technical, claimable units with
  technical acceptance criteria.
- Use the templates in `templates/entity_templates/` for post bodies. Express
  relationships as body links (`remote-posts.md`), `satisfies_reqs` as body
  text.
- Label each post with its tier + `:status:todo`.
- Compute critical path at the ticket tier and a rough first sprint; record it
  for the dashboards.

Write all of this into `plan.json` as `creates` plus dashboard edits if enabled.
Then finish through the normal protocol.

## 1. Assess + localize

Read the current spec (`vision.md`, `sad.md`, all `*-srs.md`, `*-sdd.md`,
`adr.csv`, `reqs.json`) and the current work posts from the local tracker. From
the request post, identify the trigger and map it to an impact set:

- Which spec files change? Which req ids are added / changed / deprecated?
- Which existing work posts need new / revised / deprecated?
- Record the impact map in `plan.json` so the delta is inspectable.

## 2. Patch the spec in place

- **New reqs:** next id per component; add rows to the SRS table.
- **Deprecate, do not delete:** append `[DEPRECATED <ISO date>: reason]` to the
  requirement text (preserves the id and traceability). Optionally collect under
  a `## Deprecated` section.
- **Semantic change** -> new id + deprecate old. **Phrasing only** -> edit in
  place, same id.
- **SAD/SDD:** update the affected sections; log structural SAD changes to
  `adr.csv`.
- **Reopen a settled v1 doc:** allowed here (adapt is the only route that may);
  log a row in `adr.csv` noting what reopened and why.
- Regenerate `reqs.json` from the updated SRS tables.
- Humanize any prose you touched; keep machine rows machine-formatted.

## 3. Reconcile the work posts (plan.json)

- New scope -> new tickets/issues (`creates`, tier + `:status:todo`, body links,
  `satisfies_reqs`). Recompute ticket-tier critical path; assign to a sprint.
- Obsolete work -> swap its status label to `:status:deprecated` (was real) or
  `:status:wont_do` (cancelled before built). Do not delete shipped history.
- Revised acceptance criteria -> `edit_entry` the post body, or a `comment`
  noting the revision and its cause.
- A `done` post whose underlying req changed -> swap to `:status:blocked` and
  comment for triage.
- Refresh ROADMAP / TIMELINE / Current sprint bodies (`edit_entry`) if dashboards
  are enabled.

## 4. ADR

Append one `Decision,Justification` row per real decision made. Append-only;
never rewrite existing rows.

## Retirement (whole-feature removal)

When the request retires an entire feature, not one req:

- Mark every related req `[RETIRED <ISO date>: reason]`; remove the feature's SAD
  /SDD sections; append an ADR retirement row.
- Swap all related tickets/issues (including `done` ones) to `:status:deprecated`;
  close or delete their posts (close on GitHub).
- Update `vision.md` scope only if the feature was named there or the retirement
  is a strategic pivot.

## Finish

Per the protocol: `plan.json` -> `apply.py` -> move the request post toward
`done` -> `enqueue_spec_change_run(...)` -> stop. If the change would invalidate
in-progress work, comment a warning (async) rather than silently breaking it.
