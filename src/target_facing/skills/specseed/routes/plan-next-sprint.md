# plan-next-sprint

Extend the spec **forward** into the next un-specced slice and break it down as
the next sprint. Append-only: it never reopens settled design. If the slice would
force a change to something already settled, that is `adapt`, not this route.

Read `references/spec-change-protocol.md`, `references/remote-posts.md`, and
`references/work-breakdown.md` first.

## Fires when

`spec-change:plan-next-sprint` on a request post AND a spec already exists with a
roadmap whose tail has ticket titles not yet broken into posts. If everything is
already specced and broken down, create a draft adapt post as described below
and stop.

If the request post is asking for help choosing the next step, this route does
not edit the spec. Treat "help", "what next", "next step", or similar wording in
the title/body/comments as a planning-help request.

## 1. Reconstruct state (local read)

- Read `vision.md`, `sad.md`, the `*-srs.md` set, and the ROADMAP dashboard body.
- From the local tracker, see which roadmap titles already have posts (built /
  in flight) versus the un-foldered tail (the pending slice).
- Identify the **next slice**: the next coherent group of un-specced titles. The
  request post may name or re-scope it ("do the API before the worker").

## Planning help / no next slice

When the request asks for help, or when there is no un-specced roadmap tail left:

- Do not change spec files.
- Use the current spec, ROADMAP, Current sprint, and work posts to infer a short,
  grounded next-focus list. Prefer 2-5 concrete focus areas. If the work really
  looks complete, say that it looks done unless the user wants to adapt the spec.
- In `plan.json`, create one remote post labeled `draft`,
  `spec-change:adapt`, and `spec-change:status:open`.
- The draft post body must briefly explain the suggested focus areas, tell the
  user to edit it into the change they want, and remind them to remove the
  `draft` label when done.
- Move the `plan-next-sprint` request itself toward `done`.
- Write `apply.py`, enqueue, and stop.

## 2. Extend the spec (append only)

- **SRS:** append new req rows for the slice (new component -> new `*-srs.md`).
  New ids continue the per-component numbering. Do not edit existing rows.
- **SDD/SAD:** write/extend the SDD for the slice; deepen only the SAD skeleton
  blocks this slice touches. Leave settled blocks alone.
- Regenerate `reqs.json` over the now-larger SRS set.
- `adr.csv`: append rows for new decisions only.

If extending the slice reveals that a settled decision is wrong, **stop and route
to adapt** for that change (note it in `plan.json`), then resume.

## 3. Break down the slice (plan.json)

- Create tickets/issues for the slice's roadmap titles (`creates`, tier +
  `:status:todo`, body links, `satisfies_reqs` -> the reqs just added).
- Recompute ticket-tier critical path across **all** tickets (prior + new); each
  slice sharpens it.
- Pack the new tickets into the next sprint. Refresh ROADMAP / TIMELINE /
  Current sprint dashboard bodies (`edit_entry`) if dashboards are enabled.

## Finish

Per the protocol: `plan.json` -> `apply.py` -> move the request post toward
`done` -> `enqueue_spec_change_run(...)` -> stop.

## Boundary

| Situation | Route |
|-----------|-------|
| Spec the next un-specced roadmap slice | **plan-next-sprint** |
| Change a settled req/design | **adapt** |
| Slice reveals a settled decision is wrong | stop, **adapt** that, then resume |
| One tiny edit | **tweak** |
