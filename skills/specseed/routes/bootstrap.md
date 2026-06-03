# Bootstrap mode

Greenfield. No prior spec. Produce full `.specseed/spec/` tree (or chat artifacts in progressive delivery).

**Collision guard (precondition — check before writing ANY spec file).** Bootstrap assumes greenfield. If `.specseed/spec/` already exists with content (`vision.md`, any `*-srs.md`, `sad.md`, `sdd.md`, …), **STOP** — this is not greenfield. The session-start reconnaissance (`SKILL.md`) should have routed to adapt/plan-next/tweak; if you're here anyway, surface it: "Found an existing spec at `.specseed/spec/` — bootstrap would overwrite `vision.md`/`srs.md`/`sdd.md`. Use adapt (change it), plan-next (extend it), or confirm explicit start-over." Only proceed to overwrite on an **explicit** user "start over / throw it away". Never silently clobber a settled doc. Per-file belt-and-suspenders: stages 2 / 4 / 8 must not overwrite an existing `vision.md` / `*-srs.md` / `sdd.md` without that explicit OK.

Load `references/question-protocol.md` before any user-facing round.

**Depth dial.** Bootstrap is not one-size. After vision + component-split (where real signal exists) it picks a **depth tier** — `lite` / `standard` / `incremental` — auto-suggested, user overrides (Stage 3.5). The tier never drops artifacts or scripts; it right-sizes **how much the user must answer and review at once**. specseed sells *better*, not faster — `lite` is not corner-cut, it's matched to a small project's real information content; `incremental` keeps full depth but only for the first increment, deferring the rest to `plan-next` mode so the user never specs 3 hours upfront. Machinery (assemble/validate/CP/sprints) is identical across tiers — only interaction load + breakdown horizon change.

## Flow overview

1. Context pre-stage (structured, with thin-input fallback)
2. Vision draft
3. Component-split decision (incl. optional cross-cutting virtual component)
3.5. **Depth selection** — auto-suggest `lite`/`standard`/`incremental`, user overrides
4. Per-component questioning rounds → per-component SRS drafts
5. SAD draft
6. Iterate vision + srs + sad (≤2 loops)
7. Settle SRS (propose + confirm)
8. ADRs + SDD (parallel, after settle)
9. `reqs.json` generation + cycle resolution (run `requirements_generate_json.py`, then `requirements_analyze.py`; resolve cycles if any)
10. **ROADMAP draft + discussion gate** — phases → epics → ticket TITLES, discussed with user BEFORE detailed formation (use `work-breakdown.md`). Whole roadmap in ALL tiers — it's the cheap map.
11. Work breakdown: flesh tickets + form issues + assemble + validate + critical path + **sprint planning** (use `work-breakdown.md`; run the assemble scripts, `tickets_validate.py` + `issues_validate.py` + `tickets_analyze.py`, then `sprint_plan.py` → write sprints → `sprints_validate.py` + `timeline_render.py`). **`incremental`: detail first increment only.**
12. Write main-repo entry files (merge protocol if any already exist): `README.md` + `CLAUDE.md` (from `templates/CLAUDE_template.md`, with `CONTRIBUTING` content folded into its `## Project conventions` section) + `AGENTS.md` + per-component `CLAUDE.md` (if N>1). Ask whether to add `.specseed/`, `CLAUDE.md`, `AGENTS.md`, and selected per-component `CLAUDE.md` paths to `.gitignore` at this same step. Recommend **no** because ignored artifacts transfer poorly. No separate `CONTRIBUTING.md`. **`incremental`: write `CLAUDE.md`+`AGENTS.md` EARLY (after stage 8) so the runtime contract is locked before breakdown.**
13. Optional artifacts (`deployment.md`) if triggered
14. Session end (clean `session_state.md`; `incremental` hands off to `plan-next`)

**Per-tier deltas at a glance** (stages not listed run identically):

| Stage | `lite` | `standard` | `incremental` |
|-------|--------|------------|---------------|
| 4 questioning+SRS | N=1, 1 round, short SRS | all components, full | deep only on first-increment scope; others = SRS placeholder, deferred to `plan-next` |
| 5 SAD | short, whole | full, whole | **skeleton whole** + deep only where increment 1 touches |
| 6 iterate | ≤1 loop | ≤2 loops | ≤1 loop |
| 7 settle | whole SRS | whole SRS | settle first-increment reqs only |
| 8 ADR+SDD | short | full | SDD for first-increment scope only |
| 12 entry files | end | end | **early (post-stage-8)** + README at end |
| 11 breakdown | all tickets/issues, 1 sprint by default | all, one or more sprints as needed | **first increment only → 1 sprint**; rest stays roadmap titles |
| 14 end | normal | normal | handoff: `/specseed plan-next` for next slice |

---

## Stage 1: Context pre-stage

Themes announced upfront (prune to relevant):
1. Problem space & users
2. Existing artifacts / constraints (tech, regulatory, team, timeline)
3. Success criteria sketch

Default: **1 round × 4 Qs**. Extend to 2 rounds only if ranking gate keeps ≥3 high-impact Qs.

**Thin-input fallback:** if user gave no/very-sparse initial context, skip structured Qs for this stage. Ask 1–3 freeform open prompts:
- "What are you building, briefly?"
- "Who uses it?"
- "Any hard constraints I should know — tech stack, deadline, regulation?"

Once enough base in hand, resume structured Qs (if gaps remain).

Write summary → `session_state.md` under `## Context`.

---

## Stage 2: Vision draft

Write `.specseed/spec/vision.md`. **No IDs.** Sections:
- Why (the problem and why now)
- Stakeholders (who cares, in what role)
- Scope (in / out)
- Success criteria (observable, not just aspirational)

Short. Caveman spirit, lean clarity. Vision stays brief and general; do NOT enumerate every feature (that's SRS's job). Scope section lists themes/areas, not individual reqs.

**Humanizer pass before showing** (Step 0): vision is prose a human reads first, so scrub the AI tells (no "marks a pivotal moment", no rule-of-three, no em dashes). Keep it neutral — no injected voice.

Show user. At most one refinement pass.

**Chat mode:** deliver `vision.md` as artifact now.

---

## Stage 3: Component-split decision

### 3a. Functional components

Single Q via `question-protocol.md`:

> "1 component or N? If N, name them + one-line role each."

Suggestion based on context (obvious frontend/backend split, single-binary tool, etc). Confidences on N options.

**Split threshold heuristic** (use to shape your Suggestion):
- **N=1** → single SRS/SDD. Default for tools, scripts, small services
- **N=2–4** → split if total expected reqs >40 OR substantial inter-component contracts; else single doc with `## Component: X` subsections
- **N≥5** → always split per-component

For borderline N=2–4 cases, ask the user — don't auto-decide.

Write split → `session_state.md` under `## Components`.

### 3b. Cross-cutting component (conditional)

Review context for signals that cross-cutting concerns materially matter:
- Security (authn/authz beyond a single component; data sensitivity classification)
- Observability (logs/metrics/tracing as a project-wide concern, not per-component)
- Internationalization / accessibility
- Compliance (GDPR / HIPAA / SOC2 etc) cutting across components

**If signals present:** propose a virtual `cross-cutting` component:

> "Context suggests cross-cutting concerns matter (security + observability). Add a virtual `cross-cutting` component? Gets its own `.specseed/spec/cross-cutting-srs.md` with `SRS-CC-NNN` IDs, treated like any other component by analyzers and tickets."
> - **A)** Yes, add it
> - **B)** No, fold into per-component SRSs
>
> Confidence: A 70% / B 30%
> Suggestion: **A**. Reuses all existing machinery; keeps cross-cutting reqs from getting under-specified per-component.

**If no signals:** skip this sub-stage silently. Don't ask just to ask.

Note in `session_state.md`: cross-cutting component yes/no.

---

## Stage 3.5: Depth selection

Now (vision + split known) there's real signal. Pick a **depth tier**. Auto-suggest from signal, user overrides. The tier shapes how the rest of bootstrap runs (see the deltas table above).

**Signal → suggestion heuristic:**
- **`lite`** — N=1, no cross-cutting, bounded scope (vision Scope lists few areas, no "scale/compliance/multi-team" themes surfaced). Small tool/script/single service. *Same docs, fewer questions, one sprint by default.*
- **`standard`** — N=2–4, contained scope, user willing to plan it all now. Today's full bootstrap. *Plan everything at full depth.*
- **`incremental`** — N≥3, OR large/multi-phase scope, OR user signals a long/evolving project ("platform", "v1 then…", "big"), OR speccing it all would clearly cost the user hours. *Spec the shared contract whole-but-lean, then deep-dive only the first increment; defer the rest to `plan-next`.*

Ask via `question-protocol.md` single-Q format:

> **Depth for this build?**
>
> (Doesn't change *what* gets produced — same docs, same machinery. Changes how much you answer/review at once.)
> - **A)** `lite` — small project, plan it all now, light questioning
> - **B)** `standard` — plan everything now at full depth
> - **C)** `incremental` — spec + break down the first increment now; `/specseed plan-next` for later slices (best for big/long projects)
>
> Confidence: A NN% / B NN% / C NN%
> Suggestion: **<letter>**. <one-line rationale from signal>

Write chosen tier → `session_state.md` under `## Depth`. From here, every stage reads this tier (see deltas table). If `incremental`, also note in `session_state.md`: **which components/areas are in the first increment** (the deep-question scope) — ask the user "what's the first thing to build?" if not obvious from context (e.g. "data schema + ingest first, UI later").

---

## Stage 4: Per-component questioning rounds → SRS drafts

For each component (or single "core" if N=1), including the cross-cutting component if added:

### 4a. Questioning

Invoke `references/component-questions.md`. Themes announced before round 1 per component.

After each component's questioning done:
- Write per-component summary → `session_state.md`
- **Compression checkpoint:** propose "consider compress now?" to user. If yes, note state in `session_state.md`; after compression resume, reread `SKILL.md` then `session_state.md`

Components processed sequentially. Do not interleave.

**Tier scope (from stage 3.5):**
- `lite` — N=1; 1 round, 2 themes, short SRS.
- `incremental` — deep-question + draft full SRS ONLY for components in the first increment (per `session_state.md` `## Depth` scope). For out-of-scope components: write a 1-line SRS file (`# <Component> SRS` + `> Deferred — broken down via /specseed plan-next when this slice is planned.`) so the file exists and SAD/roadmap can reference it, but DON'T question the user about it now. A component partially in scope: question only the in-scope behavior, leave the rest as a deferred note in its SRS.
- `standard` — all components, full depth (≤3 rounds).

**Operations theme flag:** if a component's questioning round includes the Operations theme (theme 6 in `component-questions.md`) AND the user's answer indicates ops concerns matter, mark in `session_state.md` to add `.specseed/spec/deployment.md` at stage 12.

### 4b. SRS draft for that component

Per component (or single `.specseed/spec/srs.md` if N=1), draft the SRS immediately after that component's questioning is done.

**ID format:** `SRS-<COMP>-<NNN>` where `<COMP>` is short code (e.g. `API`, `UI`, `CORE`, `WORKER`, `CC` for cross-cutting). Numbering starts at 001 per component.

**Table format** (one row per req, machine-parseable by `requirements_generate_json.py`):

| ID | Requirement | Type | Priority | Depends on |
|----|-------------|------|----------|------------|
| SRS-API-001 | System shall accept POST /signup with email + password | functional | must | — |
| SRS-API-002 | System shall reject signups with duplicate emails | functional | must | SRS-API-001 |

- **Type:** `functional` / `non_functional` / `constraint`
- **Priority:** MoSCoW — `must` / `should` / `could` / `wont`
- **Depends on:** comma-separated req IDs, or `—`

**No `Verified by` column.** Verification traceability is derived on demand by `verification_map.py` from `tickets.json` (`satisfies_reqs` × `artifacts.tests`). Avoids drift between SRS and tickets.

Reqs must be: atomic (one testable claim), independent in phrasing ("system shall X", not "after Y, system shall…"), verifiable (clear pass/fail), traceable.

Group reqs by feature/area inside the SRS file (with `##` subheadings). ID is the unit of verification, not the unit of work — the work units (tickets → issues) are formed later in the work-breakdown stage.

**Chat mode:** deliver each `<component>-srs.md` (or `srs.md`) as artifact as it completes.

Continue to next component (back to 4a) until all components done.

---

## Stage 5: SAD draft

`.specseed/spec/sad.md`. Sections:
- Components (one block each: responsibility, owns-data, doesn't-own). Include the cross-cutting component if present, noting it's virtual (no deployment unit; reqs realized across other components)
- Interfaces (between components + external)
- Data flow (sequence-level for key paths)
- Deployment topology (where things run — high-level; full deployment procedures, if any, go to optional `.specseed/spec/deployment.md`)
- Key decisions (high-level pointers; full decisions live in `adr.csv`)

If interface control (formal contracts between components or with external systems) matters, fold into Interfaces section rather than a separate doc.

**Tier scope:**
- `incremental` — SAD is **whole at skeleton depth**: name every component (incl. deferred ones), one-line responsibility + owns-data, and the interfaces between them. Go **deep** (data flow, detailed contracts, deployment topology) ONLY where the first increment touches — e.g. if increment 1 is the data schema, detail the storage component + its contracts; leave the frontend block a skeleton stub. Architecture is NOT deferred (sprint 1 builds on it) — only the *depth* on untouched areas is, picked up by `plan-next`. Mark deferred-deep blocks: `> Skeleton — deepened via /specseed plan-next.`
- `lite` — short SAD, whole.

**Chat mode:** deliver `sad.md` as artifact.

---

## Stage 6: Iterate vision + srs + sad

**Max 2 loops.** Each loop:
1. User reviews the three together
2. Flag inconsistencies (e.g. SRS depends on a SAD component that doesn't exist; vision excludes scope that SRS requires)
3. Patch all three in place

Stop early if user OKs. After 2 loops, stop regardless — further drift = scope creep, signal it to user.

---

## Stage 7: Settle SRS

Heuristic: SDD work (next stage) will start pressuring SRS; once SRS doesn't shift across a review pass, it's stable.

**Propose:** "Settle the SRS? Means no more drafting edits this session. Adapt mode required for later changes."

**On user confirm:** add to each SRS file's frontmatter:
```yaml
---
settled: true
settled_at: <ISO date>
---
```

This marker tells the implementation agent (later, via `CLAUDE.md`) not to edit. Adapt mode is the only path back through.

**Tier scope:** `incremental` — settle ONLY the first-increment SRS scope. SRS files that are deferred placeholders (stage 4) stay UNsettled (`plan-next` settles each slice's reqs when it details them). If a single SRS file mixes settled increment-1 reqs with deferred ones, settle the file but note in its frontmatter `settled_scope: <area>` and keep the deferred rows clearly marked — `plan-next` appends + settles the rest later without reopening what's settled (so it's plan-next, not adapt).

**Compression checkpoint** here too — settle is a clean state to compress.

---

## Stage 8: ADRs + SDD (parallel, post-settle)

### adr.csv

Columns: `Decision,Justification`. One row per decision. Append-only. Examples:
```
"Picked Postgres over Mongo","Need relational integrity for billing; team familiar"
"Picked FastAPI over Flask","Async webhook handlers needed"
```

Cheap, high-value. Add rows as decisions emerge during SDD work.

### SDD

`.specseed/spec/sdd.md` or `.specseed/spec/<component>-sdd.md` (per-component if multi-component). Sections:
- APIs (endpoints / function signatures / message schemas)
- Schemas (data models, DB tables)
- Libraries / frameworks (with versions if pinned)
- Algorithms (only where non-obvious)
- Cross-references to SRS IDs satisfied

This is the **how**. SRS is the **what**.

For the cross-cutting component (if present), SDD covers how cross-cutting reqs are realized: shared middleware, libraries, conventions, what each functional component must do to comply.

**Tier scope:** `incremental` — SDD covers ONLY the first-increment scope (the components/areas you settled in stage 7). Deferred components get no SDD now — `plan-next` writes it per slice. `lite` — short.

**Chat mode:** deliver `sdd.md` (or per-component versions) as artifact.

**`incremental` only — write entry files now (early stage 12).** Specs for increment 1 are settled, so the runtime contract can be locked before breakdown. Jump to **stage 12** and write `CLAUDE.md` + `AGENTS.md` (+ per-component `CLAUDE.md` if N>1) now — defer only `README.md` to the end. Ask the `.gitignore` question from stage 12 now too, since this is when the runtime files first land. This is the "how agents behave" the user wants front-loaded; it lets an impl agent start the moment sprint 1 has issues. Then return here for stage 9.

---

## Stage 9: reqs.json generation + cycle resolution

### Generate

Run `.specseed/scripts/core/requirements_generate_json.py` to extract from SRS table rows.

Output schema:
```json
{
  "SRS-API-001": {
    "text": "System shall accept POST /signup with email + password",
    "type": "functional",
    "priority": "must",
    "depends_on": []
  }
}
```

(No `verified_by` field — see stage 4b note.)

**Repo mode:** agent runs script directly.
**Chat mode:** skill reminds user with command: `python .specseed/scripts/core/requirements_generate_json.py` and delivers the script if user doesn't have it yet.

### Analyze + resolve

Then run `.specseed/scripts/core/requirements_analyze.py` (shipped; editable analysis seam) — surfaces cycles, orphans, dangling refs.

#### Cycle resolution moves

When `requirements_analyze.py` reports a cycle, present 3 standard moves via `question-protocol.md`:

1. **Split node** (most common) — req A had two concerns, one needs B and the other doesn't. Split A into A1 + A2
2. **Extract interface I** — both A and B depend on a new req I; remove direct A↔B link
3. **Reorder** — direction was wrong; flip dependency

**Never silently "fix" by deletion.** User picks the move per cycle. Re-run `requirements_analyze.py` after.

---

## Stage 10: ROADMAP draft + discussion gate

Use `references/work-breakdown.md` ("ROADMAP" section).

Now that reqs exist, draft the PM-level map BEFORE detailing any work:

Write `.specseed/project_management/ROADMAP.md` with:
- **Phases** (`## Phase 1 — <name>` — logical stages, not necessarily quarters)
- **Subsections** per phase (areas), at least one listing **epics** with sub-lists of **ticket TITLES**

At this stage tickets are TITLES + short comments only — no bodies, no issues yet.

**Discussion gate (required).** Present the draft roadmap — phases, epics, ticket titles, comments — and discuss with the user. Adjust phases / epic grouping / ticket split per feedback. Do NOT proceed to detailed formation until the user approves the roadmap shape.

Write the approved structure → `session_state.md` under `## Roadmap`.

**Chat mode:** deliver `ROADMAP.md` as artifact; gate on user approval before stage 11.

---

## Stage 11: Work breakdown — flesh tickets + form issues + validate + critical path

Use `references/work-breakdown.md`.

**Tier scope (breakdown horizon):**
- `standard` / `lite` — flesh ALL roadmap tickets → issues; create at least one sprint so claimable work has an `in_progress` target. `lite` defaults to a single sprint with a lightweight approval. `standard` may create multiple sprints as needed.
- `incremental` — detail ONLY the **first increment**: create ticket + issue folders for the roadmap titles in the first slice (the area you deep-specced), pack them into **one sprint** (`SPRINT_…_1`, mark `in_progress`). The rest of the roadmap stays TITLES in `ROADMAP.md` with no ticket folders yet — that's intentional, not incomplete. The chain (assemble/validate/`tickets_analyze`/`sprint_plan`/`sprints_validate`/`timeline_render`/`roadmap_render`) runs over whatever folders exist; CP is computed over the increment-1 tickets now and **recomputed across all tickets by `plan-next`** as later slices are added. No script changes — `roadmap_render.py` leaves un-foldered titles untouched, validators see a consistent (smaller) graph. `satisfies_reqs` on these tickets reference only settled increment-1 reqs, which exist.

### Form

Create the folder tree under `.specseed/project_management/`:
- `epics/<EPIC-NNNN>/<EPIC-NNNN>.md` — frontmatter + non-technical prose (goal/why/success)
- `tickets/<PROJ-NNNN>/<PROJ-NNNN>.md` — frontmatter (`satisfies_reqs`, `depends_on`=critical path, `issues`, `epic`) + prose (story, description, product acceptance criteria)
- `issues/<FEAT-NNNN>/<FEAT-NNNN>.md` — frontmatter (`component`, `effort_hours`, `artifacts`, claim fields, `ticket` parent) + prose (technical acceptance criteria, notes)

Folders are source of truth. Decompose each ticket into INVEST issues (vertical slices, sizing checks). Back-link tickets ⟷ issues.

### Assemble + validate

Run in this order (ticket effort + counts are summed from issues, so issues first):
```bash
python .specseed/scripts/core/issues_assemble.py
python .specseed/scripts/core/tickets_assemble.py
python .specseed/scripts/core/issues_validate.py
python .specseed/scripts/core/tickets_validate.py
```
Fix any reported errors before proceeding.

### Critical path

Run `.specseed/scripts/core/tickets_analyze.py .specseed/project_management/tickets.json` (shipped; editable analysis seam) — returns ticket critical path + build order. Show critical path to user. Rebalance ticket grouping if unreasonably long (often overly narrow tickets or artificial deps). Critical path is PROJECT-level, not per-sprint.

### Risk-detection & gating pass (HITL)

Run the **risk-detection & gating pass** from `work-breakdown.md` — scan the formed issues for gated actions (the 8 categories), present a consolidated coverage table, get the user's **explicit approval**, set per-issue `approval_required` where wanted, and propose isolate-gated-execution splits. Do this before sprint planning so gated issues are known when batching. (`incremental`: run it over the first-increment issues only.)

### Sprint planning

After the critical path is settled, batch tickets into sprints (see `work-breakdown.md` "Sprints"). This is mandatory for onboarding when claimable work exists: the first `in_progress` sprint is the runner's normal claim target. For tiny/lite flows, keep it nearly invisible — one sprint, simple "OK" approval unless the user wants dates or swaps.

1. Read `.specseed/memory/sprint_planning.md` for any durable prefs.
2. `python .specseed/scripts/core/sprint_plan.py` → advisory proposal (cohesion-aware, CP-first, ~168h budget).
3. One bounded refinement pass (business dates, coherence, slack); present to user; capture any durable prefs back to `sprint_planning.md`.
4. On approval: write `sprint:` into each ticket folder + create `sprints/<SPRINT_ID>/` folders with `tickets:` lists; mark the first sprint `in_progress` (the sprint claiming targets).
5. Assemble + validate + render:
   ```bash
   python .specseed/scripts/core/sprints_assemble.py
   python .specseed/scripts/core/sprints_validate.py
   python .specseed/scripts/core/timeline_render.py
   ```

### Refresh ROADMAP counts

```bash
python .specseed/scripts/core/roadmap_render.py
```
Bumps each ticket title's `(X/Y complete)` annotation in `ROADMAP.md` from `tickets.json` (`issues_done`/`issues_total`).

**Chat mode:** deliver the folder set + generated `tickets.json`/`issues.json` as artifacts; remind user to run the assemble/validate/analyze scripts locally.

---

## Stage 12: Main-repo entry files

**Tier note:** `incremental` already wrote `CLAUDE.md` + `AGENTS.md` (+ per-component) early, after stage 8 — at this point in the flow only `README.md` remains here. `lite` / `standard` write all of them now.

These are the ONLY standard files written outside `.specseed/`. The exception is optional host issue-template projection, controlled by configure mode (`.github/ISSUE_TEMPLATE/*.md` or `.gitlab/issue_templates/*.md`, only if mirror + user opt-in). Before writing any standard entry file, **for each that already exists on disk, follow the merge protocol** in `SKILL.md` ("Main-repo files & merge protocol"): read the existing file, default to replacing with the skill's version, but scan for project-specific additions worth keeping and offer to append them; never destroy user content without explicit OK. **Tell the user** which of these will be placed in the main repo and that everything else stays under `.specseed/`.

At this same step, ask whether to add specseed artifacts to `.gitignore`:
> "Add specseed artifacts to `.gitignore`? Default/recommended: **no**. Tracking `.specseed/`, `CLAUDE.md`, and `AGENTS.md` keeps the spec and agent contract portable. Ignore them only if this setup is private/local."

If user says yes, append missing ignore lines for `.specseed/`, root `CLAUDE.md`, root `AGENTS.md`, and any selected per-component `CLAUDE.md` paths. If `.gitignore` is absent, create it. Do not add those lines by default. Do not ignore `README.md` or provider issue-template projections unless user explicitly asks.

### README.md

User-facing (not agent-facing). **Normal English** (not caveman) — newcomers need plain language for install/quickstart. **Brief and anti-fluff** — no marketing voice, no "in today's fast-paced world", no over-explanation. Target ~30–50 lines unless the project genuinely needs more.

**Full humanizer pass** (Step 0) — README is the most-read prose and the most likely to look AI-generated. Scrub promo language, rule-of-three, generic upbeat conclusions, boldface/emoji tells, and all em/en dashes. A light natural voice is fine; manufactured enthusiasm is not.

Sections:
- What this project is (1 paragraph, plain language)
- Quick start / install / first run
- Where things live (note: spec/design artifacts live under `.specseed/`; link source dirs)
- How to contribute (point to the `## Project conventions` section of `CLAUDE.md`)
- License / contact

(Merge protocol applies if `README.md` already exists.)

### CLAUDE.md (root)

Write the contents of `templates/CLAUDE_template.md` to the repo root as `CLAUDE.md`. Customize:
- the bash one-liners if user has a different command preference;
- the `## Project conventions` section — fill it with the project's folder structure, branching, versioning, release process, artifact storage, and release gates (this is the former `CONTRIBUTING.md` content, now folded in — **no separate `CONTRIBUTING.md` is written**). If the user has nothing specific on release gates, leave that line as a placeholder for them to fill later.

For durable repo-specific custom instructions, use `.specseed/memory/repo/`, not `CLAUDE.md` bloat:
- Always-needed rules → `.specseed/memory/repo/index.md`.
- Scenario-specific details → sibling markdown files next to `index.md`, linked from the index with when-to-read notes.
- Keep `CLAUDE.md` pointing agents to repo memory via the template's Initial reads section.

(Merge protocol applies if `CLAUDE.md` already exists.)

### Repo memory

If the user gave durable custom instructions during bootstrap (for example "we also maintain a Software Verification and Validation Plan in `spec/...`; when X happens, do Y"), write them under `.specseed/memory/repo/`:
- `.specseed/memory/repo/index.md` — always-read rules, short and structured.
- `.specseed/memory/repo/<scenario>.md` — optional sidecar docs for conditional detail.

Index format:
```markdown
# Repo Memory

## Always read
- <standing rule>

## Scenario docs
- [Verification](verification.md) — read when planning or changing verification work.
```

If no durable custom instructions exist, skip the file. Do not invent placeholder memory.

### Per-component CLAUDE.md (multi-component projects only)

If N>1 from stage 3, **propose** per-component `CLAUDE.md` files to the user (don't auto-write). Each per-component file lives in that component's source directory (e.g. `api/CLAUDE.md`, `worker/CLAUDE.md`) and holds component-specific guidance: build/test commands, file layout conventions, common gotchas, library version pins relevant only to that component.

Propose with a one-liner per component summarizing what each would contain, based on `session_state.md` per-component summaries. User picks: write all, write some, write none. (Merge protocol applies to any that already exist.)

Root `AGENTS.md` already directs agents to read these when they exist — no additional wiring needed.

### AGENTS.md

Literally one line:
```
Read ./CLAUDE.md. In dirs you work on, read corresponding CLAUDE.md files in there too.
```

(Merge protocol applies if `AGENTS.md` already exists — though a one-liner rarely has additions worth keeping.)

---

## Stage 13: Optional artifacts

- **`.specseed/spec/deployment.md`** — only if Operations theme was flagged during a component questioning round (see stage 4 note). Covers operational procedures, runbooks, deployment commands. Distinct from SAD's Deployment topology section (that's *where things run*; this is *how to run them*)

(Strategic grouping lives in `ROADMAP.md` phases; tactical scheduling lives in sprints / `TIMELINE.md` — done in stage 11.)

---

## Stage 13.5: Remote mirror init (if configured)

**Always — point the user at the runner (both backends).** Now that the work layer
exists, tell the user how to start the loop. There is **no repo-root shim**; they run
the shipped runner directly:
- `python .specseed/scripts/agents_runner.py &` — start. The runner works **local-only**
  too (claims + runs the next ready issue in a loop); the mirror just adds reconcile +
  the CONTROL channel. File-based control: `echo pause|run|stop > .specseed/memory/runner.ctl`
  (or Ctrl-C). The full how-to is in `.specseed/README.md` (written at configure time).

**Then — mirror init only if configured.** The mirror choice was already made in
**configure mode** (the first-run preamble or `/specseed configure`) — do NOT re-ask
here. Read `config.backend.enabled` in `.specseed/memory/config.json` (the per-repo
`remote.json` carries `initialized`):

- **`backend.enabled: false`** (or no `remote.json`) → local-only. The runner above is enough; write nothing remote.
- **`backend.enabled: true` and `remote.json` not yet `initialized`** → create the mirror programmatically
  (prefer scripts — don't hand-create issues). This is a required step, not optional:
  1. `python .specseed/scripts/remote/remote_sync.py init` — 4 dashboards (pin ROADMAP/TIMELINE/CONTROL), seed labels, project host issue templates (if enabled), push current work. If your environment can't reach the remote, say so and hand the user this exact command to run; do not silently skip it.
  2. Add the optional **Remote mirror** block to `CLAUDE.md` (see `templates/CLAUDE_template.md`).
  3. Set `initialized: true` in `remote.json`; point the user at the CONTROL-issue verbs (also in `.specseed/README.md`).
- **`backend.enabled: true` and `remote.json` already `initialized`** (re-run) → just `remote_sync.py reconcile` to push the latest work.

If the user never configured but now wants the mirror → point them to `/specseed configure`. See `references/remote.md` for the full model.

**Entity templates.** Ensure `.specseed/entity_templates/` exists before session end:
```bash
python .specseed/scripts/core/entity_templates.py sync
```
This always writes/keeps canonical templates for agents. If the configured provider is
GitHub/GitLab and `backend.entity_templates.enabled:true`, it also writes only
`bug`, `feature`, and `change-request` to that provider's top-level issue-template
directory on the current branch. GitHub/GitLab may show those in the web UI only after
the files land on the repo's default branch.

---

## Stage 14: Session end

- Chat mode: deliver the final `specseed-bundle.zip` plus a manifest artifact listing every file with its canonical path. Per `SKILL.md` bundle protocol, the zip is the complete handoff; separate artifacts are only this round's changed/created files.
- Delete `.specseed/memory/session_state.md` (or move salient bits to a changelog file if user wants — confirm before). KEEP `.specseed/memory/sprint_planning.md` — it's durable cross-session memory.
- Tell user what was created + any open TODOs
- Note: future spec changes → re-invoke skill in adapt or tweak mode

**`incremental` handoff (instead of the line above).** The build is intentionally partial: increment 1 is fully specced + broken down (sprint 1 ready to claim); the rest of `ROADMAP.md` is titles. Tell the user:
- What's ready: settled specs for <scope>, sprint 1 issues claimable now, `CLAUDE.md` live → an impl agent can start.
- What's deferred: roadmap titles for later slices, not yet broken down.
- Next: **`/specseed plan-next`** when ready to spec + break down the next slice (creates sprint 2, extends SRS/SDD/SAD-depth for that scope — without reopening anything settled). Spec *changes* (not extensions) still go through adapt/tweak.
- Keep `session_state.md`? It's deleted at session end as usual; `plan-next` re-detects state from disk (settled docs + which roadmap titles have ticket folders). `sprint_planning.md` persists.
