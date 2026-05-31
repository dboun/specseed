# Tweak mode

Tiny single-doc edits. Single req add, priority change, typo fix, status flip. Anything that doesn't warrant the full adapt flow.

**Default flow is minimal: locate → diff → confirm → apply.** No question rounds. No `memory.md`. May auto-escalate to adapt mode if triggers fire (see end of file).

## Flow

### 1. Locate target

Read the user's request, identify the doc(s) to touch — typically one, but the SRS + matching-ticket combo also counts as tweak-grade (see escalation rules below). If ambiguous which file, ask once.

Common tweak patterns:
- "Add this req to SRS" → which `<component>-srs.md` (or `srs.md`)
- "Add this req to SRS and a ticket for it" → SRS file + a new ticket folder under `project_management/tickets/` (still tweak — see escalation note)
- "Change priority of SRS-API-007 to should" → find file containing that ID
- "Mark FEAT-0042 as blocked, reason: waiting on FEAT-0041" → that issue's folder frontmatter `project_management/issues/FEAT-0042/FEAT-0042.md` (FEAT-* is an issue)
- "Change priority of PROJ-0042 to medium" → that ticket's folder `project_management/tickets/PROJ-0042/PROJ-0042.md`
- "Fix typo in vision.md" → that file

Note: ticket/issue edits change the FOLDER (source of truth), then re-assemble (step 6). Don't hand-edit `tickets.json`/`issues.json` — they're generated.

### 2. Read just the relevant section

Don't load the whole file's worth of context if you don't need to. For SRS table edits, just the table + the row being touched. For a ticket/issue edit, just that entity's folder `<id>.md`.

### 3. Show diff

Present a tight diff to the user. Format:

```
File: .specseed/spec/api-srs.md

- | SRS-API-007 | System shall log all auth events | functional | must | — |
+ | SRS-API-007 | System shall log all auth events | functional | should | — |
```

Or for a ticket/issue folder:
```
File: .specseed/project_management/issues/FEAT-0042/FEAT-0042.md  (frontmatter)
Issue: FEAT-0042

  status: todo  →  status: blocked
+ notes: "waiting on FEAT-0041"
```

For the SRS+ticket combo, show both diffs in the same review block before confirming.

### 4. Confirm

Wait for user OK. If user wants adjustments, iterate the diff. Don't apply without explicit OK (`OK`, `apply`, `yes`, or equivalent).

### 5. Apply

Write the change. **Repo mode:** edit the file in place. **Chat mode:** deliver the changed file (and only that file) as an artifact.

### 6. Trigger downstream scripts

After apply:
- If a SRS file changed → reqs.json must be regenerated → run (or remind user to run) `python .specseed/scripts/requirements_generate_json.py`, then `python .specseed/scripts/requirements_analyze.py`
- If a ticket/issue folder changed → re-assemble + validate (issues first): `issues_assemble.py` → `tickets_assemble.py` → `issues_validate.py` → `tickets_validate.py`, then `tickets_analyze.py .specseed/project_management/tickets.json`. If a ticket's issue set changed, run `roadmap_render.py` to refresh ROADMAP `(X/Y)` counts.
- If both SRS and a ticket/issue changed (SRS+ticket combo) → run the req scripts then the assemble/validate cascade
- If vision/sad/adr changed → no script triggers, just the edit

**Repo mode:** agent runs scripts directly and reports output.
**Chat mode:** print copy-paste-ready commands for the user to run locally.

### 7. Escalation check

Run through escalation triggers (next section). If any fire, escalate to adapt mode immediately — announce to user, then load `references/adapt.md`. Otherwise: done.

---

## Auto-escalation triggers

Tweak mode auto-escalates to adapt mode when ANY of these fire:

1. **Settled-doc reopen.** The edit touches a file with `settled: true` frontmatter. Require explicit user OK to reopen + log to `adr.csv`. Then escalate
2. **Analyzer reports a problem.** Post-apply, `requirements_analyze.py` reports new cycle / orphan / dangling-ref, OR `tickets_validate.py` / `issues_validate.py` reports a schema/ref violation, OR `tickets_analyze.py` reports a critical-path shift. Escalate
3. **Cross-doc fanout beyond the SRS+ticket carve-out.** Tweak handles ONE doc, OR the specific combo "SRS req add/change + one matching ticket add/change (+ its issues)". Anything else (SRS + SAD, SRS + SDD, SAD + tickets, multiple SRS files, multiple tickets, epic restructuring, etc.) → escalate
4. **User signals deeper concern.** User free-texts a follow-up question, concern, or "wait, what about…" beyond a simple "apply / iterate diff / done". Escalate

### The SRS+ticket carve-out

The most common natural tweak is "add this req AND a ticket for it". Stays tweak-grade because:
- Both edits are mechanical given the user's spec
- No structural decision required
- Downstream script cascade handles fallout

What stays tweak: exactly ONE req added/changed in SRS, exactly ONE ticket added/changed referencing it (plus that ticket's issues). More than one ticket, or reqs touching multiple components (multiple SRS files), or any epic-level restructuring → escalate.

When escalating:
1. Announce: `This is bigger than a tweak. Switching to adapt mode.`
2. Carry over context already gathered (target doc(s), trigger description)
3. Load `references/adapt.md` and pick up at its stage 3 (localize impact) — stages 1 and 2 are already covered by what tweak gathered

---

## What NOT to do in tweak mode

- Don't run multi-round questioning — that's adapt's job
- Don't load `memory.md` or write to it — tweak is stateless
- Don't deliver unchanged files in chat mode
- Don't silently rewrite a settled doc — escalate first
- Don't skip the diff step even if the edit feels obvious — review is the safety rail
