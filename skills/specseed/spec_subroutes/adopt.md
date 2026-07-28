# adopt

## Short description

Existing code, no spec. Recover the spec **from the codebase** into
`<specseed_dir>/spec/`, then map what already exists (and the gaps) into the work
breakdown as remote posts. One-time onboarding; later changes route to adapt /
plan-next-sprint / tweak.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/spec-change-protocol.md` | the spec spine: outputs, gate, async clarification, tracking contract |
| `references/remote-posts.md` | the post/label model |
| `references/reply-protocol-spec.md` | clarification-round format |
| `references/chat-mode.md` | when run in chat (no runtime) |
| `references/work-breakdown.md` | break the forward gaps into epics/tickets/issues; risk pass; critical path; sprints |
| `references/component-questions.md` | which concerns to probe per recovered component (cold-start fallback for a thin repo) |
| `templates/spec_doc_templates/` | the vision/SRS/SAD/SDD/ADR doc formats |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|
| `requirements_generate_json.py` | recovered SRS tables → `reqs.json` |
| `requirements_analyze.py` | validate `reqs.json` (cycles / orphans) |
| `critical_path.py` | critical path over the forward (gap) ticket delta |
| `sprint_pack.py` | pack the first sprint over the remaining work |
| `dependencies_validate.py` | validate `plan.json.creates` before emitting |

## Fires when

`spec-change:adopt` on a request post AND source code is present but
`<specseed_dir>/spec/` has no content.

## Hard invariants

1. **Never edit code.** Recon is read-only. You reconcile the *spec* to match the
   code, never the reverse.
2. **Never edit the user's existing docs in place.** Existing `spec/`, `docs/`, RFCs
   are reference; you translate their content INTO `<specseed_dir>/spec/`, you do not
   move or rewrite the originals. (Propagate-back is opt-in and one-time, below.)
3. Code is ground truth. Where code and a doc/request disagree, code wins for
   current state; surface material conflicts (below).

## 1. Recon (read-only)

Build the picture from disk, no writes:

- **Stack/build:** dependency manifests, lockfiles, CI, Dockerfiles, Makefiles ->
  languages, frameworks, test/build commands.
- **Structure:** top-level tree, entry points, test layout.
- **Components:** group the tree into candidate components (services, packages,
  front/back split). This is the component split, inferred from disk.
- **Existing docs:** README, `docs/`, ADRs, RFCs, design notes, CHANGELOG.
- **Agent rules:** `AGENTS.md`, root + nested `CLAUDE.md`, `.cursorrules`,
  `.github/copilot-instructions.md`, `CONTRIBUTING.md`.

Size-gate: small repo, read broadly; large repo, infer components from structure
first, then read one component at a time. Do not load the whole tree.

If recon is thin (tiny/opaque repo), fall back to treating the request post as a
brief and ask the cold-start questions (what is this, who uses it, hard constraints).

## 2. Existing-doc inventory -> import decision

- **Specs/docs exist** -> **import** (default): translate their content into
  `<specseed_dir>/spec/` (vision <- README/overview; SRS <- feature lists/RFCs; SAD <-
  architecture docs; SDD <- design docs; ADRs <- existing decision records).
  Reconcile against code in step 5. Originals untouched.
- **No specs, large project** -> ask (async) for any specs / design context before
  inferring cold; reverse-engineering a big system from code alone is expensive.
- **No specs, small project** -> infer from code; ask only to fill material gaps.

## 3. Agent-rules reconciliation

If recon found agent-rules files:
1. Extract the project's conventions (build/test commands, branching, review rules,
   style, how agents are told to behave).
2. **Import them** into the spec/runtime: build/test/branching conventions feed the
   target's operating policy; durable repo-specific instructions are preserved.
3. **State the diffs from the specseed way** so nothing surprises the human: the
   settled-doc soft-freeze, the issue-claim workflow, the per-tier status model, the
   approval gate. Where their rules conflict with the specseed runtime contract,
   surface it (async comment) and let them choose. Record reconciliations in `plan.json`.

If no agent-rules found, skip; the runtime supplies defaults.

## 4. Produce the spec (staged)

Read live `spec/` (usually empty here) for context; write every doc into the staging
tree `<specseed_dir>/storage/spec-change/<id>/spec/<same relative path>`
(`spec_change_spec_dir(id)`), never to live `spec/`. The runtime promotes the staged
docs into live `spec/` on approval. Same artifacts and order as `adapt.md` cold start,
sourced from code + imported docs:

- `vision.md` from README / the request post / inferred purpose.
- `*-srs.md` reqs reverse-engineered from actual behavior (what the code does becomes
  the requirement). **Keep built vs gap distinguishable** (below). Mark anything
  uncertain for async confirmation.
- `sad.md` / `*-sdd.md` describing the architecture and implementation **as built**
  (real components, interfaces, schemas, lib versions read from manifests).
- `adr.csv` for decisions evident in the code (chosen datastore, retry strategy) with
  short justifications. Log notable code-vs-doc reconciliations here too.
- `reqs.json` consistent with the SRS tables (via `scripts/requirements_generate_json.py`).

**Built vs gap reqs.** Recovered reqs describe current behavior (built). Reqs stated
in imported docs or the request post but **not implemented** are **gap reqs** — the
real forward work. Keep them separable (group them or mark gaps) — step 6 needs to
know which reqs already shipped and which become tickets.

**Humanizer pass on prose you DRAFT** (recovered vision/SAD/SDD). Prose **imported**
verbatim from the user's docs is already human-written: reformat into structure, leave
its voice alone. Don't humanize what a human already wrote.

## 5. Reconcile conflicts (code wins, with judgement)

Code is ground truth for current state. When an imported doc disagrees with the code:
- **Auto-reconcile to the code** for the bulk (the doc is stale; the spec describes
  what runs). List these reconciliations in `plan.json`; log notable ones to `adr.csv`.
- **Ask (async) only the material ones**: security posture, data handling, a behavior
  that looks like a bug vs a feature, ambiguous intent. Don't ask on obvious staleness.
- Doc-stated intent the code never implemented -> that's a **gap req** (step 4), not a
  current-state req.

## 6. Plan the work breakdown (remote posts)

Most of the system is already built. Do **NOT** fabricate done-tickets/issues for
shipped code by default — done work blocks nothing and would pollute the dependency
DAG.

### Built work -> a Phase 0 epic, NO per-ticket done posts (default)

Create one epic post titled **`EPIC-0000 Phase 0 — Already built`** at
`:status:done`, listing the shipped capabilities as **plain ticket titles in its
body** (each marked `✓ shipped`), with no child ticket/issue posts. Built reqs live in
the SRS; their done-ness lives in this epic's body. They do not enter the ticket DAG.
ROADMAP (runtime-rendered) will show this epic as a done outcome without per-ticket
folders.

### Optional: done-tickets for verification coverage (opt-in, per area)

If the human wants automated `req -> ticket -> test` traceability over the existing
code, offer to generate real done-tickets for chosen areas: `ticket:status:done`,
`satisfies_reqs` set, test paths linked to the existing tests. PM-tier only — no issue
decomposition (nobody claims done work). Off by default; ask which areas, if any.

### Forward gaps -> full breakdown

The gap reqs are the real work. Break them down per `work-breakdown.md`: tickets AND
**issues at `:status:todo`** in `plan.json.creates` (plan-first — nothing is created
until the plan is approved; the request is the gate). Forward
tickets' `satisfies_reqs` reference gap reqs; their `depends_on` DAG covers only
forward work. Run the risk-detection & gating pass (adopt repos often touch real infra
— expect gates). Compute the critical path + first sprint over the *remaining* work.
Write SCHEDULE (you write it); ROADMAP and CURRENT SPRINT are runtime-rendered.

### No forward gaps

If recon + imports find no forward gaps, say so plainly (async comment): the recovered
spec maps what exists, but there is no claimable work yet. Skip forward breakdown,
sprint planning, and the risk pass (unless the human opts into verification
done-tickets). The runner idles until work is added (via `inject`, `adapt`, or
`plan-next-sprint`). Do not invent work.

## 7. Propagate-back (opt-in, one-time)

If you imported + reconciled docs from the user's `spec/`/`docs/`, offer ONCE (async
comment, default **no**): mirror the recovered/changed docs back into their original
locations this one time? Default leaves the originals frozen; `<specseed_dir>/spec/`
is the source of truth from now on. Never set up ongoing sync.

## Finish

**Before stopping, validate the dependency graph:** run `dependencies_validate.py` and
clear every error + warning per **Issue dependencies** in `work-breakdown.md`.

Per the protocol: stage the spec, write `plan.json` (with `plan_summary` + `apr`), then stop. The runtime gates it. Write a `plan_summary` summarizing the
breakdown (overall + every epic/ticket/issue title with a one-line blurb, plus the risk
picture); the runtime posts it + the `APR-NNNN` request and parks the request
`spec-change:status:awaiting_approval`. Nothing is created or promoted until a human
approves; on approval the runtime promotes the staged spec into live `spec/` and runs
`plan.json`, which creates the posts. Already-built `:status:done` posts (mapped from
existing code) carry no new work — a run that only maps existing code and stages docs
with nothing to create still gates as a proposal (the staged spec is enough; the approval
is the settle). Use async clarification for any material behavior you could not determine
from the code.
