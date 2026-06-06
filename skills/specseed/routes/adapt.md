# adapt

The request wants to create or change the spec. If no spec exists yet, this route
creates the first spec from the request (cold start). If a spec exists, it applies a
non-trivial change: add/extend/revise requirements or design, deprecate or retire a
feature. Patch `<specseed_dir>/spec/` and reconcile the affected work posts. adapt is
the **only route that may reopen a settled doc**.

Read `references/spec-change-protocol.md`, `references/remote-posts.md`,
`references/work-breakdown.md`, `references/component-questions.md`, and
`references/question-protocol.md` first.

## Fires when

`spec-change:adapt` on a request post. Valid whether `<specseed_dir>/spec/` is empty
or populated. Tiny single edits are `tweak`; appending the next un-specced slice is
`plan-next-sprint`. A **draft `spec-change:adapt` post** opened by the runtime (a
review that exhausted its attempts parks the issue `blocked` and opens such a post —
the remote-post analog of an impl agent flagging a wrong settled doc) is also an adapt
trigger: read the named blocked issue, treat the review summary as the concern, and on
finish unblock that issue (swap it back to `:status:todo`).

## Cold start (no spec yet)

When `<specseed_dir>/spec/` is empty, treat the request post as the first project
brief: what it is, who uses it, hard constraints, scope. If the brief is too thin to
spec a coherent v1, raise a clarification round (`question-protocol.md`) rather than
inventing a product.

### Depth dial (how much to bite off up front)

Before drafting, pick a depth tier. It never drops artifacts; it right-sizes how much
the human answers/reviews now and how far forward you break work down. Auto-suggest
from signal (component count, project size, scope words), let the human override async:

- **`lite`** (small/clear project): same docs, fewer question rounds (cap 1/stage),
  one lightweight first sprint.
- **`standard`** (default): spec the whole brief now, break down all of it, one or
  more sprints.
- **`incremental`** (big project): spec the **shared contract whole but lean**, then
  deep-spec + break down **only the first increment** -> 1 sprint; the rest of the
  roadmap stays as titles for `plan-next-sprint`. This is the one that stops a big
  project getting specced for hours before any code ships.

Record the tier + rationale in `plan.json`.

### Produce the first spec (scaled to the brief + tier)

1. `vision.md` — problem, users, scope IN / OUT, success signals. Prose; humanize.
2. Component split — functional components from the brief (see
   `component-questions.md`). One SRS+SDD per component, or a single pair for a small
   project. Propose a virtual `cross-cutting` component only if security/observability/
   i18n/a11y materially cut across components (`SRS-CC-NNN`).
3. `*-srs.md` (or `srs.md`) — requirement tables. IDs `SRS-<COMP>-NNN`. Columns stay
   machine-formatted. Run `component-questions.md` per component (evidence-first;
   `incremental` deep-questions only the first increment's components).
4. `sad.md` — architecture: components, interfaces, data flow. Prose humanized; real,
   not aspirational. `incremental`: skeleton whole + deep only where increment 1 touches.
5. `*-sdd.md` (or `sdd.md`) — the how, per component in scope.
6. `adr.csv` — columns `Decision,Justification`. One row per real decision.
7. `reqs.json` — via `scripts/requirements_generate_json.py`, then
   `scripts/requirements_analyze.py`; resolve any cycles (`work-breakdown.md`).
8. `deployment.md` — only if operations/deployment is clearly in scope.

### Cross-doc consistency pass

Before breakdown, review vision + SRS + SAD **together** and flag cross-doc breaks
(the SRS depends on a SAD component that doesn't exist; vision excludes scope the SRS
requires). Patch all three; at most 2 loops. This is the main guard against the three
core docs drifting apart at birth.

Then plan the first work breakdown as remote posts per `work-breakdown.md` (epics +
tickets AND **issues at `:status:todo`** in `plan.json.creates` — created only on
approval, plan-first), run the risk
pass, compute the critical path + first sprint, write the SCHEDULE body. Record which
spec docs this run created in `plan.json.settle_docs` (see "Settling").

## 1. Assess + localize (existing spec)

Read the current spec (`vision.md`, `sad.md`, all `*-srs.md`, `*-sdd.md`, `adr.csv`,
`reqs.json` — note which docs are `settled: true`) and the current work posts from the
local tracker. **Light drift surface** (optional, no script needed): tests referenced
in issue bodies that are missing on disk; settled docs whose `settled_at` predates
recent commits to related modules. From the request post, identify the trigger and map
it to an impact set:

- Which spec files change? Which req ids are added / changed / deprecated? Which are
  `settled`?
- Which existing work posts need new / revised / deprecated?
- Record the impact map in `plan.json` so the delta is inspectable.

Most adapt requests are shorter than a cold start — apply anti-max-bias harder, and
the auto-skip rule aggressively (adapt questions are often the obvious ones). Skip
question rounds entirely when the impact map is unambiguous.

## 2. Patch the spec in place

- **New reqs:** next id per component; add rows to the SRS table.
- **Deprecate, do not delete:** append `[DEPRECATED <ISO date>: reason]` to the
  requirement text (preserves the id and traceability). Optionally collect under a
  `## Deprecated` section.
- **Semantic change** -> new id + deprecate old. **Phrasing only** -> edit in place,
  same id.
- **SAD/SDD:** update the affected sections; log structural SAD changes to `adr.csv`.
- **Reopen a settled doc:** allowed here (adapt is the only route that may). Log a row
  in `adr.csv` noting what reopened and why. Keep the doc in `settle_docs` so it
  re-settles on approval (don't toggle `settled` off and on).
- Regenerate `reqs.json`; re-run the analyzer; resolve cycles.
- Humanize any prose you touched; keep machine rows machine-formatted.

## 3. Reconcile the work posts (plan.json)

- New scope -> new tickets/issues (`creates`, body links, `satisfies_reqs`). Tickets
  AND **issues at `:status:todo`** (created only on approval, plan-first). Run the risk
  pass over new issues. Recompute ticket-tier critical path; assign to a sprint; refresh
  SCHEDULE.
- Obsolete work -> swap its status label to `:status:deprecated` (was real) or
  `:status:wont_do` (cancelled before built). Do not delete shipped history.
- Revised acceptance criteria -> `edit_entry` the post body, or a `comment` noting the
  revision and its cause.
- A `done` post whose underlying req changed -> swap to `:status:blocked` and comment
  for triage.
- ROADMAP and Current sprint re-render from the runtime — do not hand-edit them.

## 4. ADR

Append one `Decision,Justification` row per real decision. Append-only; never rewrite
existing rows.

## Settling (the producer for `settled`)

A spec doc becomes `settled: true` **when the human approves this change**, not the
moment you write it. So:
- Record every spec doc this run created or reopened in `plan.json.settle_docs` (a list
  of paths under `<specseed_dir>/spec/`).
- Write `apr` + `plan_summary` in `plan.json` and enqueue a **proposal**; the runtime
  posts the summary + `APR-NNNN` request and parks the request
  `spec-change:status:awaiting_approval` (any run that touches the spec needs sign-off,
  even a spec-only one). On approval the **runtime** stamps `settled: true` +
  `settled_at` on each `settle_docs` path and
  moves the request to `done`. You never write `settled` yourself.
This is why adapt is the sole reopen path: once settled, only an approved adapt run
re-opens and re-settles a doc. `plan-next-sprint` and `tweak` never touch settled docs.

## Retirement (whole-feature removal)

When the request retires an entire feature, not one req:

1. **Identify scope.** Locate all reqs of the feature — by SRS section, id range, or
   `depends_on` cluster. Confirm the candidate list (async) before editing: the reqs,
   the SAD/SDD sections, the tickets/issues (incl. `done` ones), the tests, the ADR rows.
2. **Apply.** Mark every related req `[RETIRED <ISO date>: reason]` (don't delete);
   remove the feature's SAD/SDD sections; append an ADR retirement row. Swap all
   related tickets/issues (including `done` ones) to `:status:deprecated`; close or
   delete their posts (close on GitHub).
3. **Delete dead tests.** The feature is gone -> its tests are dead weight (git history
   preserves them). Update the affected issues' artifact/test lists.
4. **Data cleanup.** If retirement orphans data (DB tables, persisted state, config
   flags), add a `type:chore` cleanup issue (under a cleanup ticket if one fits) with a
   migration artifact + acceptance criterion (`work-breakdown.md`).
5. **Vision update (conditional).** Default: leave `vision.md` alone (it doesn't
   enumerate every feature). Propose a vision scope change ONLY if the retirement drops
   ≥25% of must-priority reqs in a scope area, OR is a strategic pivot (human-stated),
   OR the feature was named in `vision.md` Scope IN. Confirm before editing.

## Conflict handling + large-session bail

- If the change would invalidate `in_progress` or `done` work, surface a warning
  (async comment) before proceeding — don't silently break it. Options: pause the
  in-progress issue, mark the done post `blocked`-pending-rework, or defer.
- If the change balloons so that a large fraction of the spec (~40%+ of docs) needs
  rework, don't push through: comment recommending the human re-scope this as a fresh
  cold-start adapt with the current spec as reference.

## Finish

Per the protocol: `plan.json` (with `plan_summary` + `apr`) -> `apply.py` ->
`enqueue_spec_change_propose(...)` -> stop. Any run that plans issues OR touches the
spec proposes: the runtime posts the summary + `APR-NNNN`, parks
`spec-change:status:awaiting_approval`, settles the docs and runs `apply.py` (which
creates the posts) on approval. Nothing is created before approval. If this adapt
resolved a draft-adapt concern post, unblock its originating issue (swap to
`:status:todo`) in the plan.
