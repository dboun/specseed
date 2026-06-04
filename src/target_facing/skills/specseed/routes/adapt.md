# adapt

A spec already exists. The request wants a non-trivial change: add/extend/revise
requirements or design, deprecate or retire a feature. Patch `.specseed/spec/`
and reconcile the affected work posts.

Read `references/spec-change-protocol.md`, `references/remote-posts.md`, and
`references/work-breakdown.md` first.

## Fires when

`spec-change:adapt` on a request post AND `.specseed/spec/` has content. (Tiny
single edits are `tweak`; appending the next un-specced slice is
`plan-next-sprint`.)

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
