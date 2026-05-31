# Bootstrap mode

Greenfield. No prior spec. Produce full `.specseed/spec/` tree (or chat artifacts in progressive delivery).

Load `references/question-protocol.md` before any user-facing round.

## Flow overview

1. Context pre-stage (structured, with thin-input fallback)
2. Vision draft
3. Component-split decision (incl. optional cross-cutting virtual component)
4. Per-component questioning rounds → per-component SRS drafts
5. SAD draft
6. Iterate vision + srs + sad (≤2 loops)
7. Settle SRS (propose + confirm)
8. ADRs + SDD (parallel, after settle)
9. `reqs.json` generation + cycle resolution (run `requirements_generate_json.py`, then `requirements_analyze.py`; resolve cycles if any)
10. Ticket formation + validation + critical path (use `ticket-formation.md`; run `tickets_validate.py` + `tickets_analyze.py`)
11. Write main-repo entry files (merge protocol if any already exist): `README.md` + `CLAUDE.md` (from `references/CLAUDE_template.md`, with `CONTRIBUTING` content folded into its `## Project conventions` section) + `AGENTS.md` + per-component `CLAUDE.md` (if N>1). No separate `CONTRIBUTING.md`
12. Optional artifacts (`milestones.md`, `deployment.md`) if triggered
13. Session end (clean `memory.md`)

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

Write summary → `memory.md` under `## Context`.

---

## Stage 2: Vision draft

Write `.specseed/spec/vision.md`. **No IDs.** Sections:
- Why (the problem and why now)
- Stakeholders (who cares, in what role)
- Scope (in / out)
- Success criteria (observable, not just aspirational)

Short. Caveman spirit, lean clarity. Vision stays brief and general — do NOT enumerate every feature; that's SRS's job. Scope section lists themes/areas, not individual reqs.

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

Write split → `memory.md` under `## Components`.

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

Note in `memory.md`: cross-cutting component yes/no.

---

## Stage 4: Per-component questioning rounds → SRS drafts

For each component (or single "core" if N=1), including the cross-cutting component if added:

### 4a. Questioning

Invoke `references/component-questions.md`. Themes announced before round 1 per component.

After each component's questioning done:
- Write per-component summary → `memory.md`
- **Compression checkpoint:** propose "consider compress now?" to user. If yes, note state in `memory.md`; after compression resume, reread `SKILL.md` then `memory.md`

Components processed sequentially. Do not interleave.

**Operations theme flag:** if a component's questioning round includes the Operations theme (theme 6 in `component-questions.md`) AND the user's answer indicates ops concerns matter, mark in `memory.md` to add `.specseed/spec/deployment.md` at stage 12.

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

Group reqs by feature/area inside the SRS file (with `##` subheadings). ID is the unit of verification, not the unit of work — tickets are the work units, formed later.

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

**Chat mode:** deliver `sdd.md` (or per-component versions) as artifact.

---

## Stage 9: reqs.json generation + cycle resolution

### Generate

Run `.specseed/scripts/requirements_generate_json.py` to extract from SRS table rows.

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
**Chat mode:** skill reminds user with command: `python .specseed/scripts/requirements_generate_json.py` and delivers the script if user doesn't have it yet.

### Analyze + resolve

Then run `.specseed/scripts/requirements_analyze.py` (user-provided) — surfaces cycles, orphans, dangling refs.

#### Cycle resolution moves

When `requirements_analyze.py` reports a cycle, present 3 standard moves via `question-protocol.md`:

1. **Split node** (most common) — req A had two concerns, one needs B and the other doesn't. Split A into A1 + A2
2. **Extract interface I** — both A and B depend on a new req I; remove direct A↔B link
3. **Reorder** — direction was wrong; flip dependency

**Never silently "fix" by deletion.** User picks the move per cycle. Re-run `requirements_analyze.py` after.

---

## Stage 10: Ticket formation + validation + critical path

Use `references/ticket-formation.md`.

### Form

Skill writes `.specseed/spec/tickets.json` directly (NOT generated from markdown — JSON is source of truth here, see SKILL.md output hierarchy notes).

### Validate

After writing, run `.specseed/scripts/tickets_validate.py` — schema + ID uniqueness + dangling-ref checks. Fix any reported issues before proceeding.

### Critical path

Run `.specseed/scripts/tickets_analyze.py` (user-provided) — returns critical path + build order. Show critical path to user.

User may rebalance ticket grouping if critical path is unreasonably long (often a sign of overly narrow tickets or artificial dependencies).

**Chat mode:** deliver `tickets.json` as artifact + remind user to run both scripts locally if they have them.

---

## Stage 11: Main-repo entry files

These are the ONLY files written outside `.specseed/`. Before writing any of them, **for each that already exists on disk, follow the merge protocol** in `SKILL.md` ("Main-repo files & merge protocol"): read the existing file, default to replacing with the skill's version, but scan for project-specific additions worth keeping and offer to append them; never destroy user content without explicit OK. **Tell the user** which of these will be placed in the main repo and that everything else stays under `.specseed/`.

### README.md

User-facing (not agent-facing). **Normal English** (not caveman) — newcomers need plain language for install/quickstart. **Brief and anti-fluff** — no marketing voice, no "in today's fast-paced world", no over-explanation. Target ~30–50 lines unless the project genuinely needs more.

Sections:
- What this project is (1 paragraph, plain language)
- Quick start / install / first run
- Where things live (note: spec/design artifacts live under `.specseed/`; link source dirs)
- How to contribute (point to the `## Project conventions` section of `CLAUDE.md`)
- License / contact

(Merge protocol applies if `README.md` already exists.)

### CLAUDE.md (root)

Write the contents of `references/CLAUDE_template.md` to the repo root as `CLAUDE.md`. Customize:
- the bash one-liners if user has a different command preference;
- the `## Project conventions` section — fill it with the project's folder structure, branching, versioning, release process, artifact storage, and release gates (this is the former `CONTRIBUTING.md` content, now folded in — **no separate `CONTRIBUTING.md` is written**). If the user has nothing specific on release gates, leave that line as a placeholder for them to fill later.

(Merge protocol applies if `CLAUDE.md` already exists.)

### Per-component CLAUDE.md (multi-component projects only)

If N>1 from stage 3, **propose** per-component `CLAUDE.md` files to the user (don't auto-write). Each per-component file lives in that component's source directory (e.g. `api/CLAUDE.md`, `worker/CLAUDE.md`) and holds component-specific guidance: build/test commands, file layout conventions, common gotchas, library version pins relevant only to that component.

Propose with a one-liner per component summarizing what each would contain, based on `memory.md` per-component summaries. User picks: write all, write some, write none. (Merge protocol applies to any that already exist.)

Root `AGENTS.md` already directs agents to read these when they exist — no additional wiring needed.

### AGENTS.md

Literally one line:
```
Read ./CLAUDE.md. In dirs you work on, read corresponding CLAUDE.md files in there too.
```

(Merge protocol applies if `AGENTS.md` already exists — though a one-liner rarely has additions worth keeping.)

---

## Stage 12: Optional artifacts

- **`.specseed/spec/milestones.md`** — only if user asked for milestones during context pre-stage or later. Lists named milestones (M1, M2, ...) with target dates if any + list of ticket IDs in each. Tickets may carry a `milestone` field referencing these
- **`.specseed/spec/deployment.md`** — only if Operations theme was flagged during a component questioning round (see stage 4 note). Covers operational procedures, runbooks, deployment commands. Distinct from SAD's Deployment topology section (that's *where things run*; this is *how to run them*)

---

## Stage 13: Session end

- Deliver manifest (chat mode): single artifact listing every file with its canonical path
- Delete `.specseed/memory.md` (or move salient bits to a changelog file if user wants — confirm before)
- Tell user what was created + any open TODOs
- Note: future spec changes → re-invoke skill in adapt or tweak mode
