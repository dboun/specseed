# Adapt mode

Existing `.specseed/spec/` tree present. User wants to update, extend, or revise it non-trivially. Not a full re-spec.

Load `references/question-protocol.md` before any user-facing round. Most adapt sessions are SHORTER than bootstrap — apply anti-max-bias harder.

## Flow overview

1. Assess existing artifacts + drift → 1-paragraph state-of-spec summary to user
2. Identify trigger ("what's changing? why now? what must not break?" — OR pick up an open `spec_concern.md` — OR retirement)
3. Localize impact → delta-map shown to user
4. Reduced question rounds (only on changed areas)
5. Patch docs in-place (settled-doc reopen requires logging)
6. Re-run analyzers; resolve any new issues
7. Update tickets accordingly
8. Append `adr.csv` if a decision was made
9. Deliver changed files (chat) or commit-ready set (repo)
10. Session end

---

## Stage 1: Assess existing artifacts + drift

Read in this order:
1. `.specseed/spec/vision.md`
2. `.specseed/spec/sad.md`
3. All `*srs.md` (note which have `settled: true` frontmatter)
4. All `*sdd.md`
5. `.specseed/spec/adr.csv`
6. `.specseed/spec/reqs.json`
7. `.specseed/spec/tickets.json`
8. `.specseed/spec/milestones.md` and `.specseed/spec/deployment.md` if present

Scan for open `spec_concern.md` files under `.specseed/ticket_tracking/*/`. These were written by implementation agents that hit a settled doc they thought was wrong mid-ticket. Note their existence; they're candidate triggers for this session (see stage 2).

**Run `.specseed/scripts/drift_check.py`** if present. It surfaces mechanical drift:
- Test files referenced in tickets `artifacts.tests` that don't exist on disk
- Settled docs whose `settled_at` predates significant recent commits to related source modules
- (Other heuristic checks per the script's docstring)

Surface a **1-paragraph state-of-spec summary** to user: components, settle status per file, ticket count + completion status, any obvious gaps, count + paths of any open `spec_concern.md`, brief drift warnings if any. Keep it tight — caveman.

**Repo mode:** run scripts directly.
**Chat mode:** if user has scripts wired, ask them to paste output; otherwise skill notes what would be checked and proceeds without it.

If any user-provided script (`requirements_analyze.py`, `tickets_analyze.py`) is missing, note it now and ask user.

---

## Stage 2: Identify trigger

The trigger is one of:

**Free-form trigger.** Single user-facing freeform Q:

> "What's changing? Why now? What must NOT break?"

Free-form answer expected — not a structured round. Capture in `memory.md` under `## Adapt trigger`.

**Spec-concern trigger.** If stage 1 found one or more open `spec_concern.md` files, offer them as triggers first:

> "Implementation agent flagged a concern at `.specseed/ticket_tracking/<id>/spec_concern.md`. Address it now, or set your own trigger?"

If user picks the concern, read the concern file, summarize it back to the user in one paragraph, confirm understanding, then proceed. The concern file itself becomes the trigger.

**Drift-driven trigger.** If stage 1's `drift_check` surfaced something the user wants to address, that can be the trigger too. Capture which drift items are in scope.

**Retirement trigger.** If user says "retire feature X" or equivalent (remove an entire feature, not just deprecate one req) → jump to the Retirement sub-flow at the end of this file.

Multiple triggers in one session OK if related; keep them tracked.

---

## Stage 3: Localize impact

Map the trigger to specific docs + IDs:
- Which file(s) touched?
- Which req IDs affected (added / changed / deprecated)?
- Which tickets need new / revision / deprecation?
- Does this touch any `settled: true` doc?

**Show user a delta-map** before any drafting. Format:

```
Impact map:
- vision.md: NO CHANGE
- .specseed/spec/sad.md: NO CHANGE
- .specseed/spec/api-srs.md: 2 new reqs (SRS-API-042, SRS-API-043), 1 deprecated (SRS-API-017)  [SETTLED — reopen]
- .specseed/spec/api-sdd.md: new "Webhook retry" section
- adr.csv: +1 row (retry strategy)
- reqs.json: regenerate
- tickets.json: 2 new tickets (FEAT-0023, FEAT-0024); FEAT-0011 acceptance criteria revised
```

User confirms before drafting begins.

---

## Stage 4: Reduced question rounds

Only on changed areas. Default: **1 round × 3–4 Qs.** Anti-max-bias applies harder than bootstrap. Apply the auto-skip rule from `question-protocol.md` aggressively here — adapt-mode Qs are often the obvious ones.

Pick themes from `references/component-questions.md`'s palette but only for impacted components/concerns.

Skip rounds entirely if delta-map is unambiguous and user's trigger already covered everything (e.g. "add deprecated marker to SRS-API-017" — no questions needed, go straight to stage 5).

---

## Stage 5: Patch docs in-place

### Adding new reqs
- Next available ID per component (e.g. if last `SRS-API-NNN` was 041, new is 042)
- Insert row in appropriate section of SRS markdown

### Deprecating reqs
- DO NOT DELETE — preserves traceability and existing test references
- Mark deprecated by appending `[DEPRECATED <ISO date>: reason]` to the Requirement column text
- Optionally move row to a `## Deprecated` section at end of the SRS file

### Renaming vs new ID
- Substantive change in *what the req means* → new ID, deprecate old
- Pure phrasing/clarity change with same semantics → in-place edit, no new ID

### Reopening a settled doc
- Require explicit user OK before editing any file with `settled: true`
- Log to `adr.csv`: `"Reopened <doc>: <reqs touched>","<reason>"`
- Keep `settled: true` frontmatter — settle status preserves after edit (don't toggle false then back)

### SAD changes
- Component added/removed → update SAD components section, interfaces section, data flow
- Always log a row in `adr.csv` for structural SAD changes

### Resolving a spec_concern.md
- If the trigger was a `spec_concern.md`, after patching the relevant docs append a resolution note at the bottom of the concern file:
  ```
  ## Resolved <ISO date>
  - Docs patched: <list>
  - Adapt session reason: <one line>
  ```
- Do NOT delete the concern file — keep for traceability
- Unblock the originally-blocked ticket: set its `status` back to `todo` (or `in_progress` if user wants it picked up immediately)

---

## Stage 6: Re-run analyzers

After patches:
1. Re-run `.specseed/scripts/requirements_generate_json.py` (regenerates `reqs.json` from SRS)
2. Re-run `.specseed/scripts/requirements_analyze.py` — any new cycles/orphans/dangling refs? Apply cycle resolution moves from bootstrap stage 9
3. Run `.specseed/scripts/tickets_validate.py` after any ticket edit — fix schema issues
4. Re-run `.specseed/scripts/tickets_analyze.py` — critical path shifted? Surface to user if so
5. (Optional) re-run `.specseed/scripts/drift_check.py` to confirm the trigger drift item(s) are no longer flagged

**Repo mode:** agent runs all five directly.
**Chat mode:** skill reminds user with copy-paste-ready commands after delivering changed files.

---

## Stage 7: Update tickets

- Mark obsolete tickets `status: "deprecated"` in `tickets.json` (do not delete)
- Add new tickets if scope expanded (use `references/ticket-formation.md`)
- Revise acceptance criteria on existing tickets if reqs they satisfy changed — in-place with a note in `notes` field: `"Acceptance criteria revised <date>: <reason>"`
- If a ticket was `done` but its underlying req changed, mark `status: "blocked"` with note for user to triage
- If resolving a `spec_concern.md`, unblock the originating ticket per stage 5

---

## Stage 8: ADR append

If architecture or significant design decision was made during adapt:
```
"<Decision>","<Justification>"
```

Append-only — never edit existing rows.

---

## Stage 9: Delivery

**Repo mode:** files already written; print a tight summary of changes (file paths + 1-line per change).

**Chat mode:** deliver ONLY changed files as artifacts + a diff summary in chat. Do NOT re-deliver unchanged docs (the user already has them). Use full canonical paths as artifact identifiers.

---

## Stage 10: Session end

- Update `memory.md` with one-line "adapt session completed <date>: <summary>" then delete (or move to changelog if user asks)
- Note any open TODOs (e.g. tickets needing triage from stage 7)
- Note any open `spec_concern.md` files NOT addressed this session — they remain for future adapt sessions

---

## Conflict handling

If adapt would invalidate a currently `in_progress` or `done` ticket:
- Surface to user as a warning before proceeding
- Options: pause the in-progress ticket; mark done ticket as blocked-pending-rework; defer adapt until ticket finishes

If adapt session itself grows large enough that ≥40% of spec docs need rework, recommend the user end this session and start a fresh bootstrap (with the old `.specseed/spec/` as reference). Don't try to push through.

---

## Retirement sub-flow

Triggered when user says "retire feature X" or equivalent (whole-feature removal, not single-req deprecation).

### R1. Identify scope

Locate all reqs belonging to the feature: by SRS section heading, by ID range, or by `depends_on` clusters. Show user a candidate list:

```
Retirement candidates for feature "webhook retry":
- SRS-API-031 through SRS-API-038 (8 reqs, 6 must / 2 should)
- Section "## Webhook retry" in .specseed/spec/api-srs.md
- Tickets: FEAT-0022 (done), FEAT-0023 (done), FEAT-0024 (todo)
- Tests under tests/api/test_webhook_retry.py
- SDD section "Webhook retry strategy" in .specseed/spec/api-sdd.md
- ADR row: "Picked exponential backoff for webhook retries"
```

User confirms scope before any edits. If scope wrong, refine and re-show.

### R2. Apply retirement

In a single pass:

- **SRS reqs:** mark each `[RETIRED <ISO date>: reason]` (same mechanism as deprecation; do not delete — preserves ID space and traceability). Move to a `## Retired` section if the SRS now has 3+ retired reqs.
- **SAD:** remove the feature's component/interface/data-flow blocks. Log structural change to ADR.
- **SDD:** remove the feature's section(s) entirely. SDD is the *how*; retired features have no how.
- **Tickets:** mark all related tickets `status: "deprecated"` in `tickets.json` (including `done` ones — they shipped, but the feature is being retired). Do NOT delete.
- **Tests:** delete them. Feature gone → tests are dead weight. Git history preserves if needed. Update `artifacts.tests` lists on deprecated tickets.
- **ADR:** append a retirement row: `"Retired <feature>","<reason>"`. Also append a row noting any prior ADR rows about this feature are now historical context.

### R3. Vision update (conditional)

Default: vision stays as-is. Vision is intentionally brief and general — it doesn't enumerate every feature, so it doesn't need to enumerate every retirement.

**Propose a vision update ONLY if:**
- Retirement removes ≥25% of must-priority reqs in a top-level scope area, OR
- Retirement represents a strategic pivot (user-described, not heuristic), OR
- The retired feature was named explicitly in `vision.md` Scope IN

If so, propose moving the relevant scope item from IN to OUT (or removing it entirely) and confirm with user before editing.

### R4. Migration / cleanup tickets

If the retirement leaves behind data (orphan DB tables, persisted state, config flags), add a `CHORE-NNNN` cleanup ticket. Use `artifacts.migrations` field for any data migrations needed (see `ticket-formation.md`).

### R5. Re-run analyzers + finish

Run stage-6 analyzer cascade. Orphan/dangling warnings are expected for retired reqs — verify they only flag the retired set, not unrelated drift.

Finish per stage 9 + 10.

---

## Open TODOs

- [ ] Exact diff-presentation format (Markdown table vs prose vs side-by-side) — currently per-skill judgment
- [ ] Bulk-edit batching: if user has 10+ small changes, should those batch into one adapt session or split? Currently single session OK; flag if it blows past ~30 minutes of conversation
