# Adopt mode

Existing code, **no `.specseed/`**. Reverse-bootstrap: recover the spec FROM the codebase (+ any docs already there), land it in `.specseed/`. One-time onboarding. Once `.specseed/` exists, later sessions route to adapt / plan-next / tweak as normal.

Same output tree as bootstrap. Same machinery, same depth dial. The difference is the **source**: code + imported docs, not a blank-slate Q&A.

Load `references/question-protocol.md`, `references/component-questions.md`, `references/work-breakdown.md` as the stages below call them. Reuses bootstrap stages by reference — don't duplicate their detail, follow the pointers.

## When fires

Session-start reconnaissance (`SKILL.md`) case 3: **source files present, no `.specseed/`**. Or resume of an interrupted adopt (case 1: `session_state.md` exists, mode `adopt`).

## Hard invariants (non-negotiable)

1. **specseed NEVER edits code.** Recon is read-only. Reconciliation edits the **spec** to match the code — never the code to match a spec.
2. **Plug-and-play. No external skill dependency.** Do the recon yourself (read files, parse manifests). Do NOT rely on `learn-codebase` or anything in `~/.claude/skills` being present.
3. **Never edit the user's existing docs in place.** `spec/`, `docs/`, RFCs, design notes stay untouched. Importing = translating their content INTO `.specseed/`. The original is left as-is. Propagate-back is opt-in and one-time (stage 9).
4. `.specseed/` becomes the **source of truth** after adopt. Originals are frozen reference.

## Flow overview

1. Recon (read-only) — stack, structure, components, existing docs, agent-rules
2. Existing-doc inventory → import / ask-for-more decision
3. Agent-rules reconciliation (confirm conventions; state diffs from specseed way)
4. Depth selection (reuse bootstrap stage 3.5)
5. Draft spec from code + imports, per component (reuse bootstrap 4b/5/8) — reduced questioning, code is ground truth
6. Reconcile conflicts (code wins; surface; ask only material ones)
7. reqs.json (reuse bootstrap stage 9), then settle (auto, post-review)
8. Current-state ROADMAP + forward-gap breakdown (reuse work-breakdown + bootstrap 10/11), per depth tier
9. Entry files (reuse bootstrap stage 12 + merge protocol) + propagate-back offer
10. Session end

Memory cadence + compression hooks: same as bootstrap (`SKILL.md` memory protocol). Write `session_state.md` with `mode: adopt` + current stage so an interrupted adopt resumes.

---

## Stage 1: Recon (read-only)

Build a picture of what exists. **No file writes.** Order:

1. **Stack + build:** dependency manifests (`package.json`, `pyproject.toml`/`requirements.txt`, `go.mod`, `Cargo.toml`, `pom.xml`/`build.gradle`, `Gemfile`, …), lockfiles, CI configs, Dockerfiles, Makefiles. → languages, frameworks, build/test commands.
2. **Structure:** top-level + one-or-two-deep dir tree. Entry points (`main`, `cmd/`, `src/`, server bootstrap). Test layout.
3. **Component inference:** group the tree into candidate components (services, packages, top-level modules, frontend/backend split). This is the analog of bootstrap's component-split — but INFERRED from disk, then confirmed by the user (stage 5a).
4. **Existing docs:** README, `docs/`, `spec/`, ADR files/dirs, RFCs, design notes, CHANGELOG.
5. **Agent-rules:** `AGENTS.md`, root + nested `CLAUDE.md`, `.cursorrules`/`.cursor/rules`, `.github/copilot-instructions.md`, `CONTRIBUTING.md`.

**Size-gate the depth (plug-and-play, never read every file on a big repo):**
- **Small repo** → read broadly; cheap.
- **Large repo** → infer components from structure + manifests FIRST, present the component map, then deep-read **one component at a time** (sequential, like bootstrap stage 4 — but reading, not asking). Don't load the whole tree into context.

Output: a tight **recon summary** to the user (caveman): stack, inferred components (one-line role each), existing docs found (paths), agent-rules found (paths). This replaces bootstrap's context+vision pre-stage — most of the "what" is already on disk. Confirm the picture before drafting.

If recon is thin (tiny/opaque repo) → fall back to bootstrap stage 1 freeform prompts ("what is this, who uses it, hard constraints?").

---

## Stage 2: Existing-doc inventory → import decision

Decide per the project's doc situation:

- **Specs/docs exist** → **import them** (default). Translate their content into `.specseed/` structure (vision ← README/overview/design intent; SRS ← feature lists/requirements/RFCs; SAD ← architecture docs/diagrams; SDD ← design docs; ADRs ← existing decision records). Reconcile against code in stage 6. **Originals untouched.** Where an imported doc has a gap the code doesn't fill (or vice-versa) → note it, ask in stage 5/6.
- **No specs, big project** → ASK the user for any specs / design context / tribal knowledge before inferring. A large unbuilt-from-scratch picture is expensive to reverse-engineer cold — get what they have first.
- **No specs, small project** → infer from code; ask only to fill gaps.

Record the import plan → `session_state.md` under `## Import`.

---

## Stage 3: Agent-rules reconciliation

If stage 1 found `AGENTS.md` / `CLAUDE.md` / `.cursorrules` / `CONTRIBUTING.md` etc:

1. Read them. Extract the project's conventions (build/test commands, branching, review rules, style, how agents are told to behave).
2. **Confirm with the user** — "found these rules: <summary>. Keep them?"
3. **State the diffs from the specseed way** so nothing surprises them: settled-doc soft-freeze contract, the issue-claim workflow, the per-tier status model, `spec_concern.md` escalation path (see `references/CLAUDE_template.md`). Where their rules conflict with specseed's runtime contract, surface it and let them choose.

These conventions feed the `## Project conventions` section of the `CLAUDE.md` specseed writes at stage 9 (merge protocol — never silently clobber their existing one).

If no agent-rules found → skip; specseed's `CLAUDE_template.md` supplies defaults.

---

## Stage 4: Depth selection

Reuse **bootstrap stage 3.5** verbatim (`lite` / `standard` / `incremental`). Auto-suggest from the recon: component count + project size + how much forward work remains. Same meaning — the tier shapes how much the user answers/reviews and how far forward we break work down, NOT what artifacts get produced.

Mapping to adopt's forward work (stage 8):
- `lite` / `standard` — spec the whole codebase, break down ALL remaining forward gaps now (~1 sprint for `lite`).
- `incremental` — spec the shared contract whole-but-lean, deep-spec the first forward slice, break it into 1 sprint; defer the rest of the forward roadmap to `plan-next`.

Write tier → `session_state.md` under `## Depth`.

---

## Stage 5: Draft spec from code + imports (per component)

Reuse bootstrap **4b (SRS), 5 (SAD), 8 (SDD)** for the artifact shapes. The source is code + imported docs; **questioning is REDUCED** — recon already answered most "what." Per component (sequential, large-repo one-at-a-time per stage 1):

### 5a. Confirm component split
Present the inferred components from stage 1. User confirms / renames / merges / splits. (This is bootstrap stage 3 done from evidence instead of a cold question.) Cross-cutting virtual component: propose only if recon shows real cross-cutting concerns (security/observability/i18n) — same rule as bootstrap 3b.

### 5b. Reduced questioning (gap-fill only)
Run `references/component-questions.md` but ONLY for what the code can't tell you: intent behind a design, non-obvious constraints, doc-vs-code conflicts, whether inferred behavior is actually a requirement vs an accident. Apply the auto-skip rule HARD — don't ask what the code already answers. Often 0–1 rounds per component.

### 5c. Draft SRS / SAD / SDD describing REALITY
- **SRS** — reqs describe what the system *does today* (shipped behavior), phrased as normal `SRS-<COMP>-NNN` rows. Plus any **gap reqs** (planned/intended-but-not-built, from imported docs or user) — these are the forward work. Keep built vs gap distinguishable (group them, or note gaps — you'll need to know which get tickets at stage 8).
- **SAD** — the architecture *as built* (real components, real interfaces, real data flow).
- **SDD** — the implementation *as built* (real APIs, schemas, libs, versions read from manifests).
- `incremental`: deep-spec only the first forward slice's scope; skeleton/placeholder the rest (bootstrap tier-scope rules apply).

---

## Stage 6: Reconcile conflicts

Code = ground truth for current state. When an imported doc disagrees with the code:
- **Auto-reconcile to the code** for the bulk (the doc is stale; the spec describes what's actually running).
- **Surface a conflict list** to the user (tight).
- **Use judgement on what to ask:** material / risky / ambiguous-intent conflicts (security posture, data handling, a behavior that looks like a bug vs a feature) → ask. The rest → reconcile silently and list them.
- Doc-stated intent the code never implemented → that's a **gap req** (stage 5c), not a current-state req.
- Log notable reconciliations to `adr.csv` (`"Recovered spec: <doc> disagreed with code on X — took code","reverse-engineered <date>"`).

specseed never changes the code to resolve a conflict.

---

## Stage 7: reqs.json + settle

1. Run `requirements_generate_json.py` then `requirements_analyze.py` (bootstrap stage 9). Resolve cycles with the same moves.
2. **Settle (auto, post-review).** The code is the proof — recovered SRS describes shipped reality, nothing to drift. After ONE user review pass, settle (add `settled: true` + `settled_at`). Still one explicit confirm; just don't drag it through bootstrap's iterate loop. `incremental`: settle only the first-slice scope; gap/deferred reqs stay unsettled for `plan-next`.

---

## Stage 8: Current-state ROADMAP + forward-gap breakdown

Use `references/work-breakdown.md` + bootstrap **stages 10–11**, with the adopt twist: **most of the system is already built.**

### Built work → ROADMAP only, NO tickets (default)
Do **NOT** fabricate done-tickets/issues for already-shipped code. Instead, ROADMAP carries the built story:
- Add a `## Phase 0 — Already built` section listing shipped capabilities as ticket **titles**, each marked `✓ shipped` (plain titles, NO ticket folders, NO `(X/Y)` counts). `roadmap_render.py` tolerates folderless titles, so this needs no script change.
- Built reqs live in the SRS; their done-ness lives in this ROADMAP section. They don't enter the ticket DAG (done work blocks nothing).

### Optional: done-tickets for verification coverage (user opt-in)
If the user wants automated `req → ticket → test` traceability over the **existing** codebase, offer to generate real done-tickets for chosen areas: `status: done`, `satisfies_reqs` set, `artifacts.tests` linked to the existing test files. PM-tier only — skip issue-tier decomposition (nobody claims done work). Off by default; ask which areas, if any.

### Forward gaps → full breakdown (per depth tier)
The gap reqs (stage 5c) are the real work. Break them down exactly as bootstrap stage 11:
- `lite` / `standard` — all forward gaps → tickets + issues + ~1 sprint (or as many as needed). Run the full assemble → validate → `tickets_analyze` → `sprint_plan` → `sprints_validate` → `timeline_render` → `roadmap_render` chain.
- `incremental` — first forward slice only → 1 sprint; rest stays roadmap titles for `plan-next`.

Forward tickets' `satisfies_reqs` reference gap reqs; their `depends_on` DAG covers only forward work (built work is already done). If a forward ticket genuinely needs a built capability the user wanted in the graph, that's the opt-in done-ticket case above.

---

## Stage 9: Entry files + propagate-back

### Entry files
Reuse bootstrap **stage 12** + the merge protocol (`SKILL.md`). Write `README.md` (if absent/merge), root `CLAUDE.md` (from `CLAUDE_template.md`, `## Project conventions` filled from stage 3's recovered conventions), `AGENTS.md`, per-component `CLAUDE.md` if N>1. An existing `CLAUDE.md`/`AGENTS.md` from stage 1 → merge protocol, never silent clobber.

### Propagate-back (opt-in, one-time)
If the user had docs under `spec/`/`docs/` that we imported + reconciled, offer ONCE at the end:
> "`.specseed/` is now the source of truth. Mirror the recovered/changed docs back into your `spec/` too? (default: no — leave your originals frozen; future spec changes happen in `.specseed/`.)"

- Default **no** — `.specseed/` owns it, originals untouched.
- If **yes** — write the reconciled versions back into their original locations this once. Tell them: not continuous; future `/specseed adapt` can re-mirror on request, but `.specseed/` remains source of truth.
- Never set up automatic ongoing sync.

---

## Stage 9.5: Remote mirror init (only if configured)

Same as bootstrap **stage 13.5** — the mirror choice was made earlier in **configure mode** (first-run preamble / `/specseed configure`), so don't re-ask. Read `.specseed/memory/remote.json`: `enabled:false`/absent → skip; `enabled:true` and not `initialized` → run `remote_sync.py init` + `--write-shim` + the CLAUDE block, set `initialized:true`. Full model in `references/remote.md`.

---

## Stage 10: Session end

- Summary: spec recovered for <components>, <N> built capabilities mapped in ROADMAP Phase 0, <M> forward gaps broken into sprint(s).
- Tell user the runtime contract is live (`CLAUDE.md` written) → an impl agent can claim forward issues now.
- `incremental` handoff: `/specseed plan-next` for the next forward slice (same as bootstrap's incremental handoff).
- Delete `session_state.md` (keep `sprint_planning.md`). Future spec changes → adapt / tweak; future forward slices → plan-next.

---

## Open TODOs

- [ ] Heuristic threshold for "large repo" size-gate (file count / LOC) — currently judgement.
- [ ] How aggressively to infer gap-reqs from stale imported docs vs treating them as retired-before-built — currently per-judgement, ask when material.
