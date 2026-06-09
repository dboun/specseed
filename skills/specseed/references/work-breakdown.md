# Work breakdown

How a spec becomes epics -> tickets -> issues. In this build the breakdown lives as
**remote posts** (`references/remote-posts.md`), planned into `plan.json` and applied
by `apply.py`. There is no local folder tree. Deterministic structure (reqs from SRS,
dependency cycles, critical path, sprint packing) is computed by the **skill scripts**
under `skills/specseed/scripts/` (run them; do not hand-compute what a script owns).

## Three tiers

| Tier | Nature | Holds |
|------|--------|-------|
| epic | PM, non-technical | an outcome; groups tickets |
| ticket | PM, user-visible value | story, description, product acceptance criteria, `satisfies_reqs`, the critical-path `depends_on` DAG |
| issue | technical, claimable | technical acceptance criteria, artifacts, plan notes. INVEST applies HERE |

**Every issue belongs to a ticket; every ticket belongs to an epic.** A planned
breakdown is a full tree, never loose posts: link each issue to its ticket
(`Ticket: #NN`) and each ticket to its epic (`Epic: #NN`). Even a tiny single-ticket
project gets one grouping epic (it is one post, and the roadmap renders from epics).
The lone exception is a standalone issue filed directly (a one-off bug/chore via
`inject`/`tweak` with no ticket) - that may be parentless. No separate "story" tier (a
user story is a `## Story` section in a ticket body). Relationships are body links, not
labels (`remote-posts.md`).

## IDs (human-facing, in the body/title)

Posts have a numeric provider id (`#NN`); on top of that each carries a stable
human id so dashboards and links read well:
- **Epic:** `EPIC-NNNN`. **Ticket:** `PROJ-NNNN` (project-wide). **Issue:** typed —
  `FEAT-NNNN`, `BUG-NNNN`, `CHORE-NNNN`, `SPIKE-NNNN`, `QA-NNNN`.

## Type + difficulty (labels)

Each issue carries a **`type:<kind>`** label (`feature` / `bug` / `chore` / `spike` /
`qa`) and an optional **`difficulty:<level>`** label (`easy` / `hard`). Tickets may
carry a `type:` label too (`feature` / `bug` / `chore` / `spike`), never `qa`. These
are real labels in the tracker vocabulary (`supported_values.py`), so the runtime can
filter on them. Use the body templates in `templates/entity_templates/` for the
matching body shape.

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

1. **Semantic:** you can name 2 to 6 technical acceptance criteria with confidence.
   `<2` -> too small, merge. `>6` -> too big, split.
2. **Mechanical:** the work fits one focused agent session. Too big -> split even if
   the semantic check passed.

**Vertical slice:** cut each issue so it produces an observable behavior change in one
run/request. If you can say in one sentence what looks different after it ships, it is
a valid slice. Slices may skip layers; the rule is observability.

**Integration issue:** insert one before any point where 2+ parallel branches converge
(its dependencies span different branches), especially across 2+ components.

## Formation

1. From the spec, name epics + ticket titles.
2. Flesh each ticket: story, description, product acceptance criteria; set
   `satisfies_reqs` (1–8); set ticket dependencies (the critical-path DAG).
3. Decompose each ticket into issues (vertical slices, INVEST, sizing).
4. Set issue artifacts (code paths, tests, migrations) and plan notes; set the
   `type:` label and the `difficulty:` label.
5. Insert integration issues at merge points.
6. Body-link both directions: every ticket carries `Epic: #NN`, every issue `Ticket: #NN`
   (`remote-posts.md` for the exact tokens - the runtime builds the tree only from these,
   so an unlinked post orphans). Declare EVERY issue->issue dependency per **Issue
   dependencies** (below): a tests/consumer issue that needs another issue's code must
   carry `Depends on:` — the runtime enforces only what you write. Then run
   `scripts/dependencies_validate.py <plan.json>` and clear it (it checks BOTH the parent
   tree and the dependency DAG: an orphaned issue/ticket or a dangling parent is an error).
7. **Run the risk-detection & gating pass** (below) before sprint planning.
8. Label every post: tier + status. Epics/tickets AND **issues at `:status:todo`**
   in `plan.json.creates` — but nothing is created until the plan is approved
   (plan-first; the spec-change request is the gate, see `spec-change-protocol.md`).
   On approval the issues are created already-claimable.

Ticket/issue/epic prose gets the humanizer pass (neutral, concrete, no em dashes).
Labels and req ids are machine text, exempt.

## Scaffolding (the first issue) — suggest, don't force

A greenfield project with no agreed layout will have each parallel issue invent its
own (one issue makes `pkg/`, another `src/pkg/`) and never commit cleanly. Prevent it
by laying the foundation ONCE, first.

**Emit a scaffold issue** (`type:chore`, `difficulty:easy`, first in the first sprint)
when the project is greenfield AND the structure is non-trivial (a real package tree,
a build/test toolchain, multiple components). It builds the canonical layout from
`sad.md`'s `## Project layout`, the build/test config + manifest, and the
language-appropriate `.gitignore` (e.g. the standard Python ignore) — nothing more. It
produces a building, test-runnable skeleton.

**Wire it as the foundation dependency:** every other first-wave issue (and the first
issue of each component) gets `depends_on: #<scaffold>` in its body. The runtime
dependency gate then holds those issues until the scaffold issue is `done`, so they
start against a layout that already exists rather than racing to create one. (This is
exactly what the issue-level `depends_on` gate is for; see the runtime's dispatch
gate.)

**Skip it** when there is nothing to scaffold: a tiny/single-file project, or an
existing repo whose layout is already set (adopt route). Then fold the minimal setup
(a couple of files, the `.gitignore`) into the first feature issue instead — do not
manufacture a ceremonial scaffold issue. Naming is a soft convention (a clear title,
optional `-scaffold` suffix), not enforced.

## Issue dependencies (declare them — the runtime only enforces what you write)

Every issue branch is cut fresh from primary, so an issue can only see code that has
already MERGED. The runtime dependency gate holds an issue until each `Depends on:` it
declares is `done` (== on primary). But the gate enforces **only the links you write into
the body** — it cannot infer intent. An undeclared dependency is not a soft dependency; it
is a race, and the dependent will start too early against code that is not there yet.

**The rule — mandatory, not a judgment call:** if an issue needs another issue's code,
interface, or output to do its job, it MUST carry `Depends on: #<that issue>`. This is
NOT about ordering by number or by ticket; it is logical need. The cases that bite most:

- **tests depend on the thing they test.** A "write tests for X" issue depends on the "X"
  issue. (The classic miss: tests run, X is not merged, the tests have nothing to import.)
- **a consumer depends on its producer.** Wiring/CLI/integration that imports a module
  depends on the issue that writes that module; an issue parsing a format depends on the
  one that defines it.
- **scaffold foundation** (above) and **QA-last** (below) are just named instances of this
  same rule.

Cross-ticket need is the same rule, but you usually express it one tier up: if work in
ticket B needs ticket A's code, give **ticket** B `Depends on: #A` (a ticket reaches
`done` only when all its issues merge, so this holds every B issue until all of A lands).
Drop to an issue->issue cross-ticket dep only when one specific issue in B needs one
specific issue in A and waiting for all of A would stall B needlessly.

Write deps in the body as `Depends on: #{id:<exact title>}` for an item this plan creates
(the `#` is mandatory; `apply.py` substitutes the real id), or `Depends on: #NN` for an
already-existing post. See `references/remote-posts.md`.

**Validate before you emit.** Run `scripts/dependencies_validate.py <plan.json>`. It
fails (exit 1) on a dangling ref, a malformed dep line, a **cycle** among the created
issues, or a broken parent link (a decomposed issue with no ticket, a ticket with no
epic, a parent `#{id:...}` that resolves to nothing or to the wrong tier); resolve a
cycle the same way as a requirement cycle (split / extract interface / reorder, below).
It also WARNS when a tests/QA-shaped issue declares no dep at all — for
each warning, either add the missing dep or satisfy yourself the issue truly stands alone
(e.g. it exercises code already on primary). Re-run until errors are clear and every
warning is accounted for.

## Critical path (ticket tier, project-level)

Compute it over **all** tickets, not per sprint: the dependency DAG crosses sprint
boundaries, so a per-sprint view hides the real bottleneck. Run
`scripts/critical_path.py` over the ticket delta in `plan.json` (each ticket carries
`depends_on` + summed-issue effort); it returns the longest dependency chain by summed
effort, which sets the minimum duration. Schedule its tickets first. "Important" is not
the same as "on the critical path". A very long CP usually means tickets are too narrow
or deps are artificial: rebalance. Record the CP in `plan.json` and mark `★` on the
critical tickets in the SCHEDULE dashboard body.

## Risk-detection & gating pass

Runs **once after issues are formed, before sprint planning**. In this build the
**APR approval gate already forces a human to approve every new issue batch** before
any work starts (`spec-change-protocol.md`), so this pass is early-warning + routing,
not a separate enforcement. It does three things, recorded together in `plan.json`
under `risk` and summarized in the APR request comment so the human approves with the
risk picture in hand:

1. **Scan** each issue's scope (artifacts touched, technical acceptance criteria,
   description) for the eight action-gate categories: `container`, `heavy_compute`,
   `network`, `deps`, `data_destructive`, `external_publish`, `outside_repo`,
   `secrets` (authoritative list: `specseed_runtime/executing/permissions.py`). Also
   flag anything user-facing or irreversible the categories miss. Note the detected
   categories in the issue body so the impl agent is not surprised when it parks for an
   action gate mid-work.
2. **Assign `difficulty`** per issue (drives the code-review gate, below). Default from
   scope and let the human correct: bias `hard` for foundational, safety/security/data,
   broad/semantic refactor, multi-phase/externally-gated, or low-confidence/ambiguous
   work; bias `easy` for localized, reversible, well-understood, narrow-blast-radius
   work with obvious validation. Mixed signals -> `hard` or split. Record a one-word
   rationale per issue (`foundation`, `safety/data`, `broad refactor`, `localized`).
3. **Decide per-ticket QA** (below) and propose any isolate-gated-execution splits.

Surface the count of gated + `hard` issues in the APR summary — it tells the human how
often the runner will park during execution.

### Isolate gated execution (the split heuristic)

When an issue mixes **pure-code authoring** (scripts, wiring, mocked tests — agent
territory, no gate) with a **gated resource-consuming execution** (real training, a
docker build, a deploy, a paid-API run, a destructive migration against real data —
human/gated territory), split it so each issue finishes in a clean, gate-free state:

- **prep** issue — the code. Finishes clean.
- **run** issue — the gated execution. The impl agent writes self-run instructions and
  **parks** on the action gate; the human runs it; results are transcribed back. **No
  source edits during a run issue.** When the human action is fat (multi-step setup,
  "download this model", provision creds), the instructions go in a handoff pointer in
  the issue body, not crammed into the gate. This is the one issue *designed* to park.
- **consume** issue (optional) — non-trivial analysis of the run's outputs. Agent
  territory again; unit-testable with mocks.

Rewire deps: dependents needing **code/interface** point at *prep*; dependents needing
**real outputs** (built images, trained weights, measured numbers) point at *run*.
Naming is a **soft convention** (clear titles, optional `-prep`/`-run` suffix), not
enforced. **Suggest, don't force** — skip the split when the gated action is incidental
or inseparable (a measurement-only spike, a deploy with no separable code).

## Code review + difficulty

Optional automated code review sits between an issue finishing and `done`, run by the
runtime (`executing/advance.py` + the `review` config), not by this skill. The skill's
only input is the per-issue **`difficulty:` label**:
- **Confidence is the primary gate; difficulty is the modifier.** `hard` issues are
  excluded from auto-approve by default, so they always land in `awaiting_approval` for
  a human even at high review confidence. `easy` issues may auto-close above the
  confidence bar. The implementing agent never reviews its own work.

## QA issues

For a larger or riskier ticket, append a **terminal `type:qa` issue** that depends on
ALL the ticket's other issues (so it runs last). It is NOT a separate ticket — a QA
ticket would need its own `satisfies_reqs` and pollute the roadmap. It reuses all issue
machinery (label gating, the approval gate).

- **When:** decided in the risk pass. Default *suggest*: propose QA for tickets that
  clear the bar (meaningful effort, breadth across 2+ components, or a cluster of
  `hard` issues). The human accepts per ticket. (No QA on small, single-component
  tickets.)
- **Bounded scope** (QA complements dev, must not double it): the QA issue's technical
  acceptance criteria ARE the checklist — smoke + regression over the sibling issues'
  touched paths + the obvious integration paths. Cap effort at roughly ≤25% of the
  ticket's summed issue effort.
- **Findings → `type:bug` issues on the SAME ticket**, `high` priority if they block
  the ticket's value. This holds the ticket open (it can't roll up to `done` until the
  QA issue and any spawned bugs resolve) — the intended "reopen with feedback"
  behavior. QA never silently fixes and never expands scope.

## Spike issues

Spike issues (`type:spike`) are time-boxed research. The spike issue body must state
that, **before the spike can be marked done, the impl agent captures a spike report**
(else the learning vanishes):

```
Spike report:
- Question: <what was investigated>
- Investigation: <what was tried — sources, prototypes, benchmarks>
- Findings: <what was learned>
- Decision: <chosen path, or "no decision yet — see Followups">
- Followups: <ADR row? new req? SDD update? -> file a spec-change>
```

A decision or new req surfaced by a spike is a follow-up **spec-change** (adapt/tweak),
filed by the human, not an auto-edit from spike completion.

## Data-shape migrations

An issue that changes **data shape** (DB schema, persisted file format, on-disk state,
config schema) needs migration handling, stated in the issue body:
1. **Migration artifact:** list the forward-migration script path under the issue's
   artifacts.
2. **Acceptance criterion:** include one phrased "migration script committed at
   `<path>` and runs cleanly against the current schema".

For retired features (see `routes/adapt.md`), cleanup migrations get their own
`type:chore` issues with the same treatment.

## Requirement cycles

After regenerating `reqs.json`, run `scripts/requirements_analyze.py`. If it reports a
dependency **cycle** among reqs, never silently break it by deletion. Resolve with one
of three standard moves, picked per cycle (ask the human async if the choice is
material):
- **split node** — the req does two things; split it so the dependency only touches one
  half.
- **extract interface** — pull the shared contract into its own req both sides depend
  on.
- **reorder** — the dependency direction is wrong; flip it.
Re-run the analyzer until clean before computing the critical path.

## Sprints

A sprint is a time-boxed batch of tickets (~one week, ~168h soft budget). It is
**orthogonal to epics**: an epic groups by outcome, a sprint groups by time. A ticket
has one epic and one sprint.

A sprint is expressed as a `sprint:<id>` label on each member ticket post (the runtime
renders the `CURRENT SPRINT` board from those labels — you do not write that body). You
write `SCHEDULE`, which lists every sprint in execution order with its tickets (format
in `remote-posts.md`). ROADMAP stays strategic, never lists sprints, and is also
runtime-rendered.

**ID format:** `SPRINT_<YYYY>_W<WW>_<X>` — ISO-ish year + week + a letter
disambiguator. One sprint is `ongoing` at a time (the claim target for impl agents).

### Packing — computation proposes, human refines

1. **Propose.** Run `scripts/sprint_pack.py` over the ticket delta: it places a ticket
   only after every ticket it depends on is in the same or an earlier sprint (**no
   backward sprint dependency, by construction**), fills toward the budget, and within
   that pulls critical-path tickets early, then keeps same-epic tickets together
   (cohesion), then orders by priority.
2. **Refine (the art).** One bounded pass — don't re-solve. Read the proposal, the
   critical path, and any durable sprint preferences (below); adjust a few placements
   for business dates, coherence, deliberate splits, slack. If a change is material,
   confirm via an async question.
3. **Write.** Assign `sprint:<id>` labels to the member tickets and refresh the
   SCHEDULE body.
4. **Capture durable prefs.** If the human states a *generalizable* sprint preference
   during refinement ("always leave ~15% slack", "keep auth + session together"),
   record it as a note comment on the CONTROL post so future planning honors it (the
   remote-post analog of the old `sprint_planning.md`). Not one-off placements.

### Carry-over

At a sprint boundary, incomplete tickets roll into the next sprint: move their
`sprint:<id>` label to the next sprint and note the carry-over in the new sprint's
SCHEDULE section.

## The art part (judgment calls)

- **Where to cut a vertical slice spanning 2 components** — pick the slice with the
  smaller cross-component contract; tie -> the component better understood.
- **When to merge tightly-coupled issues** — identical scope + one depends only on the
  other -> consider merging.
- **When to defer a spike** — off the critical path -> defer; on it -> schedule now.

When unsure, raise one async question with a 1-line trade-off; don't ask open-ended.
