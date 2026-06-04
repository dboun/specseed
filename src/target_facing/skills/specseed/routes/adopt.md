# adopt

Existing code, no spec. Recover the spec **from the codebase** into
`<specseed_dir>/spec/`, then map what already exists (and the gaps) into the work
breakdown as remote posts.

Read `references/spec-change-protocol.md`, `references/remote-posts.md`, and
`references/work-breakdown.md` first.

## Fires when

`spec-change:adopt` on a request post AND source code is present but
`<specseed_dir>/spec/` has no content.

## Hard invariants

1. **Never edit code.** Recon is read-only. You reconcile the *spec* to match the
   code, never the reverse.
2. **Never edit the user's existing docs in place.** Existing `spec/`, `docs/`,
   RFCs are reference; you translate their content INTO `<specseed_dir>/spec/`, you do
   not move or rewrite the originals.
3. Code is ground truth. Where code and the request post disagree, code wins;
   note the discrepancy in a comment (async) if it is material.

## Recon (read-only)

Build the picture from disk, no writes:

- **Stack/build:** dependency manifests, lockfiles, CI, Dockerfiles, Makefiles
  -> languages, frameworks, test/build commands.
- **Structure:** top-level tree, entry points, test layout.
- **Components:** group the tree into candidate components (services, packages,
  front/back split). This is the component split, inferred from disk.
- **Existing docs + agent rules:** README, `docs/`, ADRs, `AGENTS.md`,
  `CLAUDE.md`. Import their content into the spec; leave the files alone.

Size-gate: small repo, read broadly; large repo, infer components from structure
first, then read one component at a time. Do not load the whole tree.

## Produce the spec (`<specseed_dir>/spec/`)

Same artifacts and order as `adapt.md` cold start, but sourced from code +
imported docs instead of a brief:

- `vision.md` from README / the request post / inferred purpose.
- `*-srs.md` reqs reverse-engineered from actual behavior (what the code does
  becomes the requirement). Mark anything uncertain for async confirmation.
- `sad.md` / `*-sdd.md` describing the architecture as built.
- `adr.csv` for decisions evident in the code (a chosen datastore, a retry
  strategy) with short justifications.
- `reqs.json` consistent with the SRS tables.

## Plan the work breakdown (remote posts)

- **Already-built work** -> tickets/issues created at `:status:done` (they
  shipped; record them so the roadmap reflects reality).
- **Gaps / remaining work** (from the request post, TODOs, obvious holes) ->
  tickets at `:status:todo`, **issues at `:status:awaiting_approval`** (claimable,
  so gated per the protocol's approval gate).
- Epics group both. Critical path + first sprint over the *remaining* work.
- Dashboards (ROADMAP/TIMELINE/Current sprint) reflect current state, if enabled.

All into `plan.json` (`creates`, dashboard `edits`).

## Finish

Per the protocol: `plan.json` -> `apply.py` -> `enqueue_spec_change_run(...)` ->
stop. If you created any remaining-work issue, post one `APR-NNNN` request comment
and park the request `spec-change:status:awaiting_approval` (the approval gate),
not `done`. (Already-built `:status:done` issues need no approval.) Use async
clarification for any material behavior you could not determine from the code.
