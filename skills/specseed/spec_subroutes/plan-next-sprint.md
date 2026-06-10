# plan-next-sprint

## Short description

Extend the spec **forward** into the next un-specced slice and break it down as
the next sprint. Append-only: it never reopens settled design. If the slice would
force a change to something already settled, that is `adapt`, not this route.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/spec-change-protocol.md` | the spec spine: outputs, gate, async clarification, tracking contract |
| `references/remote-posts.md` | the post/label model |
| `references/reply-protocol-spec.md` | clarification-round format |
| `references/chat-mode.md` | when run in chat (no runtime) |
| `references/work-breakdown.md` | break the slice into tickets/issues; risk pass; critical path across all tickets; sprint packing |
| `templates/spec_doc_templates/` | the SRS/SAD/SDD/ADR doc formats for the appended slice |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|
| `requirements_generate_json.py` | regenerate `reqs.json` after appending the slice's SRS rows |
| `requirements_analyze.py` | re-run after regen; resolve any cycles |
| `critical_path.py` | recompute the ticket-tier critical path across all tickets (prior + new) |
| `sprint_pack.py` | pack the new tickets into the next sprint |
| `dependencies_validate.py` | validate `plan.json.creates` before emitting |

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
- Use the current spec, ROADMAP, CURRENT SPRINT, and work posts to infer a short,
  grounded next-focus list. Prefer 2-5 concrete focus areas. If the work really
  looks complete, say that it looks done unless the user wants to adapt the spec.
- In `plan.json`, create one remote post labeled `draft`,
  `spec-change:adapt`, and `spec-change:status:open`.
- The draft post body must briefly explain the suggested focus areas, tell the
  user to edit it into the change they want, and remind them to remove the
  `draft` label when done.
- Move the `plan-next-sprint` request itself toward `done`.
- Write `plan.json` + `apply.py` and stop. (The draft post is a `creates`, so the
  runtime gates this as a proposal.)

## 2. Extend the spec (append only, staged)

Read live `spec/` for context; write every changed/new doc into the staging tree
`<specseed_dir>/storage/spec-change/<id>/spec/<same relative path>`
(`spec_change_spec_dir(id)`), never to live `spec/`. The runtime promotes the staged
docs on approval. Append-only still holds: stage a copy that only adds the slice's rows,
never rewriting settled content.

- **SRS:** append new req rows for the slice (new component -> new `*-srs.md`).
  New ids continue the per-component numbering. Do not edit existing rows.
- **SDD/SAD:** write/extend the SDD for the slice; deepen only the SAD skeleton
  blocks this slice touches. Leave settled blocks alone.
- Regenerate `reqs.json` via `scripts/requirements_generate_json.py`; re-run
  `scripts/requirements_analyze.py` and resolve any cycles (`work-breakdown.md`).
- `adr.csv`: append rows for new decisions only.
- Record the spec docs/sections this slice newly wrote in `plan.json.settle_docs`
  (the new SDD/SRS for the slice), so the runtime settles them on approval. You never
  reopen an already-settled block; you only settle the new slice's own docs.

If extending the slice reveals that a settled decision is wrong, **stop and route
to adapt** for that change (note it in `plan.json`), then resume.

## 3. Break down the slice (plan.json)

- Plan tickets/issues for the slice's roadmap titles (`creates`, body links,
  `satisfies_reqs` -> the reqs just added). Tickets AND **issues at `:status:todo`** in
  `plan.json.creates` — created only on approval (plan-first, per the protocol's
  approval gate). Label each issue with its `type:` (and `difficulty:`) and run the
  risk-detection & gating pass over the new issues (`work-breakdown.md`).
- Recompute ticket-tier critical path across **all** tickets (prior + new) with
  `scripts/critical_path.py`; each slice sharpens it. Pack with
  `scripts/sprint_pack.py`.
- Pack the new tickets into the next sprint. Refresh the SCHEDULE body
  (`edit_entry`). ROADMAP and CURRENT SPRINT re-render from the runtime — do not
  hand-edit them.

## Finish

**Before stopping, validate the dependency graph:** run `dependencies_validate.py` and
clear every error + warning per **Issue dependencies** in `work-breakdown.md`.

Per the protocol: stage the spec, write `plan.json` (with `plan_summary` + `apr`) +
`apply.py`, then stop. The runtime gates it as a proposal (staged spec + `creates`): it
posts the `plan_summary` + `APR-NNNN` and parks the request
`spec-change:status:awaiting_approval`; on approval it promotes the staged spec into live
`spec/` and runs `apply.py`, which creates the sprint's posts. Nothing is created or
promoted before then.

## Boundary

| Situation | Route |
|-----------|-------|
| Spec the next un-specced roadmap slice | **plan-next-sprint** |
| Change a settled req/design | **adapt** |
| Slice reveals a settled decision is wrong | stop, **adapt** that, then resume |
| One tiny edit | **tweak** |
