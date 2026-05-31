# Tweak mode

Tiny single-doc edits. Single req add, priority change, typo fix, status flip. Anything that doesn't warrant the full adapt flow.

**Default flow is minimal: locate → diff → confirm → apply.** No question rounds. No `memory.md`. May auto-escalate to adapt mode if triggers fire (see end of file).

## Flow

### 1. Locate target

Read the user's request, identify the doc(s) to touch — typically one, but the SRS + matching-ticket combo also counts as tweak-grade (see escalation rules below). If ambiguous which file, ask once.

Common tweak patterns:
- "Add this req to SRS" → which `<component>-srs.md` (or `srs.md`)
- "Add this req to SRS and a ticket for it" → SRS file + `tickets.json` (still tweak — see escalation note)
- "Change priority of SRS-API-007 to should" → find file containing that ID
- "Mark FEAT-0042 as blocked, reason: waiting on FEAT-0040" → `tickets.json`
- "Fix typo in vision.md" → that file

### 2. Read just the relevant section

Don't load the whole file's worth of context if you don't need to. For SRS table edits, just the table + the row being touched. For tickets.json, just the affected ticket entry.

### 3. Show diff

Present a tight diff to the user. Format:

```
File: .specseed/spec/api-srs.md

- | SRS-API-007 | System shall log all auth events | functional | must | — |
+ | SRS-API-007 | System shall log all auth events | functional | should | — |
```

Or for JSON:
```
File: .specseed/spec/tickets.json
Ticket: FEAT-0042

  "status": "todo"  →  "status": "blocked"
+ "notes": "waiting on FEAT-0040"
```

For the SRS+ticket combo, show both diffs in the same review block before confirming.

### 4. Confirm

Wait for user OK. If user wants adjustments, iterate the diff. Don't apply without explicit OK (`OK`, `apply`, `yes`, or equivalent).

### 5. Apply

Write the change. **Repo mode:** edit the file in place. **Chat mode:** deliver the changed file (and only that file) as an artifact.

### 6. Trigger downstream scripts

After apply:
- If a SRS file changed → reqs.json must be regenerated → run (or remind user to run) `python .specseed/scripts/requirements_generate_json.py`, then `python .specseed/scripts/requirements_analyze.py`
- If `tickets.json` changed → run (or remind) `python .specseed/scripts/tickets_validate.py`, then `python .specseed/scripts/tickets_analyze.py`
- If both SRS and tickets.json changed (SRS+ticket combo) → run all four in order: generate_json → requirements_analyze → tickets_validate → tickets_analyze
- If vision/sad/adr changed → no script triggers, just the edit

**Repo mode:** agent runs scripts directly and reports output.
**Chat mode:** print copy-paste-ready commands for the user to run locally.

### 7. Escalation check

Run through escalation triggers (next section). If any fire, escalate to adapt mode immediately — announce to user, then load `references/adapt.md`. Otherwise: done.

---

## Auto-escalation triggers

Tweak mode auto-escalates to adapt mode when ANY of these fire:

1. **Settled-doc reopen.** The edit touches a file with `settled: true` frontmatter. Require explicit user OK to reopen + log to `adr.csv`. Then escalate
2. **Analyzer reports new issue.** Post-apply, `requirements_analyze.py` reports new cycle / orphan / dangling-ref, OR `tickets_validate.py` reports schema violation, OR `tickets_analyze.py` reports critical-path shift. Escalate
3. **Cross-doc fanout beyond the SRS+ticket carve-out.** Tweak handles ONE doc, OR the specific combo "SRS req add/change + one matching ticket add/change". Anything else (SRS + SAD, SRS + SDD, SAD + tickets, multiple SRS files, etc.) → escalate
4. **User signals deeper concern.** User free-texts a follow-up question, concern, or "wait, what about…" beyond a simple "apply / iterate diff / done". Escalate

### The SRS+ticket carve-out

The most common natural tweak is "add this req AND a ticket for it". Stays tweak-grade because:
- Both edits are mechanical given the user's spec
- No structural decision required
- Downstream script cascade handles fallout

What stays tweak: exactly ONE req added/changed in SRS, exactly ONE ticket added/changed referencing it. More than one of either side → escalate. Reqs touching multiple components (multiple SRS files) → escalate.

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
