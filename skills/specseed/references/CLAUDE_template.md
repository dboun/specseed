# Agent runtime entry: CLAUDE.md template

This file is **the content the specseed skill writes to the user's repo root as `CLAUDE.md`**. It tells the implementation agent (Claude Code, Codex, or any other) how to pick up and execute the next ticket.

The skill writes this template by default, with two customizations: (1) the bash one-liners in the "Claim the ticket" section if the user has a different command preference; (2) the **`## Project conventions`** section, which the skill fills with the project's folder structure, branching, versioning, release process, and release gates (this is the former `CONTRIBUTING.md` content, now folded in here — the skill no longer writes a separate `CONTRIBUTING.md`).

When writing to the user's repo, write the content below (everything between the `---BEGIN TEMPLATE---` and `---END TEMPLATE---` markers) as the file `CLAUDE.md` at the repo root.

**If a `CLAUDE.md` already exists at the target path**, do NOT overwrite blindly — follow the merge protocol in `SKILL.md` ("Main-repo files & merge protocol"): default to replacing with this template, but offer to append any project-specific notes worth keeping from the existing file.

---BEGIN TEMPLATE---

# CLAUDE.md

Agent entry point. If no other instructions given, your default task is to **handle the next ticket in the build order**.

## Initial reads

Always read first:
1. `.specseed/spec/vision.md` — why this project exists
2. The `## Project conventions` section below — folder structure, branching, versioning, release, conventions

## Pick next ticket

```bash
python .specseed/scripts/tickets_analyze.py .specseed/spec/tickets.json | jq -r '.next_todo'
```

This returns the ID of the next ticket whose status is `todo` (or `blocked`) AND whose `depends_on` are all `done`/`deprecated`. Output is `null` if no work remains — in that case stop and tell the user.

Then load just that ticket's info:

```bash
python .specseed/scripts/ticket_info.py <ticket_id>
```

Returns the ticket entry from `tickets.json` PLUS the full req entries it satisfies (joined from `reqs.json`). Use this — don't load all of `tickets.json` or `reqs.json` into context.

## Claim the ticket

Use `claim_ticket.py` for atomic claim — it handles concurrent agents safely via `fcntl.flock` and auto-recovers stale claims:

```bash
python .specseed/scripts/claim_ticket.py <ticket_id>
# Optional flags:
#   --agent <name>          # override agent identifier (default: env CLAUDE_AGENT_ID or hostname-pid)
#   --stale-hours <N>       # take over claims older than N hours (default: 3)
#   --lock-timeout <secs>   # max time to wait for the lock (default: 10)
```

Output is JSON to stdout:
```json
{"claimed": true, "ticket_id": "FEAT-0001", "previous_claim": null}
```

or on stale-takeover:
```json
{"claimed": true, "ticket_id": "FEAT-0001", "previous_claim": {"by": "agent-X", "at": "2025-01-01T..."}}
```

or on conflict:
```json
{"claimed": false, "ticket_id": "FEAT-0001", "reason": "already claimed by agent-Y at 2025-...", "stale": false}
```

If `claimed: false` and not stale → pick a different available ticket; don't fight over locks.

If you can't acquire the file lock within the timeout (script exits non-zero with a lock-timeout error), another claim/edit operation is in flight. Wait a few seconds and retry; if it persists, tell the user — something's stuck.

**Why this script and not raw `jq`?** Two agents reading and writing `tickets.json` in parallel can both think they claimed the same ticket. The script does a single locked read-verify-write so only one wins. The lock auto-releases on process exit, so a killed/stuck agent doesn't block forever; stale claims are detected via `claimed_at` age.

## Load only relevant context

- Read ONLY the sections of `.specseed/spec/<component>-srs.md` (or `.specseed/spec/srs.md`) corresponding to the req IDs in `satisfies_reqs`. Don't ingest the whole SRS
- Read the relevant section(s) of `.specseed/spec/<component>-sdd.md` (or `.specseed/spec/sdd.md`)
- Skim `.specseed/spec/sad.md` only if you need cross-component context
- If a per-component `CLAUDE.md` exists in your ticket's component dir (e.g. `api/CLAUDE.md`), read it for component-specific commands and gotchas

## Plan

Create `.specseed/ticket_tracking/<ticket_id>/plan.md` before writing any code. Format:

```markdown
# Plan: <ticket_id> — <title>

## Steps
- [ ] 1. <step description>
- [ ] 2. <step description>
- [ ] 3. <step description>
...

## Notes / decisions as I work
- ...
```

Keep this file updated as you work — mark `[x]` when steps complete, add sub-steps if a step splits, note decisions inline.

## Execute

- Tests live with the code under test (per the `## Project conventions` section below, typically `tests/` mirroring `src/`)
- Use subagents for parallelizable sub-steps if your harness supports them. **Only the main agent writes step reports** (subagent outputs are aggregated by the main agent)

## Step reports

After each top-level step completes, write a report to:
```
.specseed/ticket_tracking/<ticket_id>/step_reports/<X>_<step_or_substep>_<desc>.md
```
- `<X>` = monotonic counter starting at 1
- `<step_or_substep>` = identifier from the plan (e.g. `2` or `2-1`)
- `<desc>` = a few words describing what was done, hyphen-separated

Example: `.specseed/ticket_tracking/FEAT-0001/step_reports/1_1_setup-signup-route.md`

Report should cover: what changed, files touched, tests added, any deviations from plan + why.

## DO NOT EDIT settled docs

**This is a contract honored by you, not enforced by the filesystem.** Nothing technically stops you from editing `.specseed/spec/vision.md`, `.specseed/spec/sad.md`, `*srs.md`, `*sdd.md`, or `.specseed/spec/adr.csv`. The rule exists because the user's adapt workflow assumes settled docs are stable. Breaking the contract silently desyncs traceability and breaks the spec_concern handoff below.

If ticket execution reveals a settled doc looks wrong (requirement is incorrect, design assumption fails, new ADR is needed), you have ONE option:

### Spec concern handoff

1. **Stop work on the current ticket.** Mark its status `blocked` in `tickets.json` (keep your `claimed_at`/`claimed_by` so the user knows where it is — `claim_ticket.py` does NOT need to be re-run; you're not releasing the claim, just changing status)
2. **Write `.specseed/ticket_tracking/<ticket_id>/spec_concern.md`** with this structure:
   ```markdown
   # Spec concern: <ticket_id>

   ## What's wrong
   <plain description of the issue>

   ## Why I think so
   <reasoning, code snippets, examples that surfaced the problem>

   ## Proposed change
   <what you'd change in the settled doc, including which doc(s)>

   ## Impact if not addressed
   <what happens if we proceed without changing the spec>
   ```
3. **Tell the user, verbatim:**
   > "Use `/specseed adapt` to address spec concern: .specseed/ticket_tracking/<id>/spec_concern.md"
4. **DO NOT** edit the settled doc yourself. Adapt mode is the only path through. The user will invoke it, address the concern, unblock your ticket, and you'll pick it up again on the next cycle

This is the ONLY procedure. There is no emergency override, no exception for "small" changes to settled docs, no "I'll just fix it and log it after". The contract is hard — and again, it's a contract you keep, not one the filesystem enforces.

## Spike post-completion

If your ticket's `type` is `spike`, before marking it `done` you MUST capture findings using the spike report template (short version below). Otherwise the learning vanishes.

Write into the ticket's `notes` field (plain prose, 3–10 lines):

```
Spike report:
- Question: <what was being investigated>
- Investigation: <what was tried — sources, prototypes, benchmarks>
- Findings: <what was learned>
- Decision: <chosen path, or "no decision yet — see Followups">
- Followups: <ADR row? new req? SDD update? — tell user via /specseed>
```

Then:
1. **If a decision was made** → tell the user to use `/specseed` to add an ADR row capturing the decision + justification
2. **If new or changed requirements emerged** → tell the user to use `/specseed adapt` to draft them properly
3. **If a design pattern surfaced that belongs in the SDD** → tell the user to use `/specseed adapt` to add the relevant SDD section
4. Only AFTER you've surfaced the findings and the user has invoked the skill (or explicitly said "no spec changes needed") do you mark the spike `done`

Do NOT silently mark a spike done with findings only in `notes`. The notes are the minimum capture; ADR/SRS/SDD updates are where future agents will look.

## Finish

When all plan steps `[x]` and tests pass:
1. (Spike only) complete the spike post-completion steps above first
2. Mark ticket `status: "done"` in `tickets.json`. Also clear `claimed_at` and `claimed_by` to `null` (the ticket is no longer claimed; it's finished). Use a brief atomic edit — `claim_ticket.py` is only for taking a claim, not for releasing it; just edit the JSON with a small `jq` block:
   ```bash
   jq --arg id "<ticket_id>" \
      '.[$id].status = "done" | .[$id].claimed_at = null | .[$id].claimed_by = null' \
      .specseed/spec/tickets.json > /tmp/t.json && mv /tmp/t.json .specseed/spec/tickets.json
   ```
3. Run `python .specseed/scripts/tickets_validate.py` to sanity-check `tickets.json`
4. Commit per the `## Project conventions` section below

## What you CAN edit

- `tickets.json` — your own ticket's status (per the lifecycle: `todo` → `in_progress` → `done`/`blocked`), `claimed_at`/`claimed_by` (set by `claim_ticket.py` on claim; cleared by you on done), `notes` field, `artifacts.tests` and `artifacts.migrations` fields as work progresses
- `.specseed/ticket_tracking/<ticket_id>/plan.md` — your plan file
- `.specseed/ticket_tracking/<ticket_id>/spec_concern.md` — when needed (see "Spec concern handoff" above)
- `.specseed/ticket_tracking/<ticket_id>/step_reports/*` — your step reports
- All source code, test files, build configs (per the ticket's scope)

You may NOT edit other tickets in `tickets.json`, even if you think their status is wrong. Surface to user instead.

## Optional: drift check before claim

For long-running projects where it's been weeks since the last spec edit, before claiming a ticket consider running:

```bash
python .specseed/scripts/drift_check.py
```

If it surfaces mechanical drift in areas your ticket touches (missing test files, stale settled docs vs recent commits in your component), surface to user before proceeding:

> "drift_check flagged X concerns related to my ticket. Address via /specseed adapt first?"

This is optional, not mandatory. Skip if drift_check.py isn't present or the project is small/recent.

## Project conventions

> The skill fills this section with the project's actual conventions. This is the former `CONTRIBUTING.md`, folded in. Keep it short and concrete.

- **Folder structure:** where source, tests, and configs live (high-level). Spec artifacts live under `.specseed/` (never edit those except as allowed above; only `/specseed` does).
- **Branching:** branch naming + how work maps to branches/PRs.
- **Versioning:** scheme (semver, calver, none) and where the version is set.
- **Release:** how a release is cut, where artifacts are stored.
- **Release gates:** test-coverage thresholds, env smoke checks, manual sign-off, etc — define here if relevant to this project; otherwise leave as a placeholder to fill later.
- **Build/test commands:** the canonical commands for this project (or per-component, if multi-component — those may also live in each component's `CLAUDE.md`).

---END TEMPLATE---

## Skill-side notes (not written to user's CLAUDE.md)

The above template is INTENTIONALLY medium-length. It includes:
- Ticket pickup flow (one-liner)
- Atomic claim via `claim_ticket.py` (replaces raw `jq` claim — handles concurrency + stale recovery)
- Plan + step-report conventions
- Don't-edit-settled-docs safety rule, explicitly framed as a contract (not enforcement) with structured `spec_concern.md` handoff
- Spike post-completion checklist with inline spike report template
- Optional drift-check hook
- Concurrency guidance
- `## Project conventions` section (folder structure, branching, versioning, release, release gates, build/test commands) — this is the former `CONTRIBUTING.md`, now folded in; the skill fills it from what it gathered

It does NOT include:
- Detailed multi-agent orchestration rules (rare in practice; add via adapt mode if needed)
- Implementation patterns (those go in code or per-component `CLAUDE.md` files)
- Deep per-component build/test detail when multi-component — that can live in each component's `CLAUDE.md`; the root `## Project conventions` stays high-level

For multi-component repos, the skill at bootstrap stage 11 proposes creating a `CLAUDE.md` per component subdirectory with component-specific notes — root `AGENTS.md` already directs agents to read those.
