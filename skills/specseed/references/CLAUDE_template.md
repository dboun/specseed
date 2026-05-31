# Agent runtime entry: CLAUDE.md template

This file is **the content the specseed skill writes to the user's repo root as `CLAUDE.md`**. It tells the implementation agent (Claude Code, Codex, or any other) how to pick up and execute the next **issue** (issues are the technical, claimable unit; epics + tickets are the PM layer above them).

The skill writes this template by default, with two customizations: (1) the bash one-liners in the "Claim" section if the user has a different command preference; (2) the **`## Project conventions`** section, which the skill fills with the project's folder structure, branching, versioning, release process, and release gates (the former `CONTRIBUTING.md` content, now folded in — the skill no longer writes a separate `CONTRIBUTING.md`).

When writing to the user's repo, write the content below (everything between the `---BEGIN TEMPLATE---` and `---END TEMPLATE---` markers) as the file `CLAUDE.md` at the repo root.

**If a `CLAUDE.md` already exists at the target path**, do NOT overwrite blindly — follow the merge protocol in `SKILL.md` ("Main-repo files & merge protocol").

---BEGIN TEMPLATE---

# CLAUDE.md

Agent entry point. If no other instructions given, your default task is to **handle the next issue in the build order**.

## Work model

Work is organized in three tiers under `.specseed/project_management/`:
- **epics** (`EPIC-NNNN`) and **tickets** (`PROJ-NNNN`) — the PM / non-technical layer (outcomes, user-visible value). Tickets carry the requirements (`satisfies_reqs`) and the critical-path `depends_on` DAG.
- **issues** (`FEAT/BUG/CHORE/SPIKE-NNNN`) — the technical layer. **This is what you claim and execute.** An issue belongs to a ticket (its `ticket` field).

Folders are the authored source of truth; `tickets.json` / `issues.json` are the generated indexes that carry **live runtime state** (status, claims) during execution.

## Initial reads

Always read first:
1. `.specseed/spec/vision.md` — why this project exists
2. The `## Project conventions` section below

## Pick + claim the next issue

One atomic call picks the next ready issue AND claims it (no pick/claim race):

```bash
python .specseed/scripts/claim_issue.py
# Optional:
#   <issue_id>            # claim a specific issue instead of auto-picking
#   --skip ID,ID          # exclude issues a parallel agent already took
#   --agent <name>        # override agent id (default: env CLAUDE_AGENT_ID or hostname-pid)
#   --stale-hours <N>     # take over claims older than N hours (default 3)
#   --sprint-scope X      # current | spill (default) | all  — see below
#   --lock-timeout <secs> # max wait for the file lock (default 10)
```

Output is JSON:
```json
{"claimed": true, "issue_id": "FEAT-0101", "claimed_at": "...", "claimed_by": "...", "previous_claim": null}
```
- `claimed: false, issue_id: null, reason: "no ready issue to claim"` → no work is available right now; stop and tell the user.
- `claimed: false` with a reason (already claimed, deps not done, parent ticket not reachable) → pick a different issue; don't fight over locks.

"Ready" means: status todo/blocked, unclaimed, its own `depends_on` issues done, and its parent ticket reachable (the ticket's `depends_on` tickets all complete). Ticket completion is derived live from issues — downstream tickets unblock automatically as their issues finish.

**Sprints (if `sprints.json` exists):** auto-pick is sprint-scoped — it prefers issues whose ticket is in the **active** sprint and only spills to the next planned sprint when none are ready (default `--sprint-scope spill`). So normally you just run `claim_issue.py` and get current-sprint work. Use `--sprint-scope current` to refuse spill (stop when the active sprint is drained), or `all` to ignore sprints. No `sprints.json` → unscoped.

To see the ticket-level critical path / build order (priority context):
```bash
python .specseed/scripts/tickets_analyze.py .specseed/project_management/tickets.json
```

## Load only relevant context

```bash
python .specseed/scripts/issue_info.py <issue_id>
```
Returns your issue, its **parent ticket**, and the reqs the parent ticket satisfies (joined from `reqs.json`) — requirements live on the ticket, not the issue. Use this instead of loading whole JSON files.

Then:
- Read your issue's prose body for **technical acceptance criteria** + notes: `.specseed/project_management/issues/<issue_id>/<issue_id>.md`
- Read the parent ticket's body for the **story / product acceptance criteria** if you need the user-facing intent: `.specseed/project_management/tickets/<ticket_id>/<ticket_id>.md`
- Read ONLY the SRS sections for the req IDs from `issue_info` (`.specseed/spec/<component>-srs.md` or `srs.md`), and the relevant `*-sdd.md` section. Skim `sad.md` only for cross-component context.
- If a per-component `CLAUDE.md` exists in your issue's component dir, read it.

## Plan

Create `.specseed/project_management/issues/<issue_id>/plan.md` before writing code:

```markdown
# Plan: <issue_id> — <title>

## Steps
- [ ] 1. <step>
- [ ] 2. <step>

## Notes / decisions as I work
- ...
```
Keep it updated as you work.

## Execute

- Tests live with the code under test (per `## Project conventions`).
- Use subagents for parallelizable sub-steps if your harness supports them. **Only the main agent writes step reports.**

## Step reports

After each top-level step, write:
```
.specseed/project_management/issues/<issue_id>/step_reports/<X>_<step_or_substep>_<desc>.md
```
`<X>` = monotonic counter from 1. Cover: what changed, files touched, tests added, deviations from plan + why.

## DO NOT EDIT settled docs

**A contract honored by you, not enforced by the filesystem.** Nothing stops you editing `.specseed/spec/vision.md`, `sad.md`, `*srs.md`, `*sdd.md`, or `adr.csv` — but doing so desyncs traceability and breaks the spec_concern handoff. Don't.

If executing your issue reveals a settled doc looks wrong:

### Spec concern handoff

1. **Stop.** Mark your issue `status: "blocked"` in `issues.json` (keep your claim fields — you're not releasing, just changing status):
   ```bash
   jq --arg id "<issue_id>" '.[$id].status="blocked"' \
      .specseed/project_management/issues.json > /tmp/i.json && mv /tmp/i.json .specseed/project_management/issues.json
   ```
2. **Write** `.specseed/project_management/issues/<issue_id>/spec_concern.md`:
   ```markdown
   # Spec concern: <issue_id>

   ## What's wrong
   ## Why I think so
   ## Proposed change
   ## Impact if not addressed
   ```
3. **Tell the user, verbatim:**
   > "Use `/specseed adapt` to address spec concern: .specseed/project_management/issues/<id>/spec_concern.md"
4. **Do NOT** edit the settled doc yourself. Adapt mode is the only path through.

No emergency override, no exception for "small" changes.

## Spike post-completion

If your issue's `type` is `spike`, before marking it `done` you MUST capture findings (else the learning vanishes). Write into the issue body (or its `notes`):
```
Spike report:
- Question:
- Investigation:
- Findings:
- Decision:
- Followups: <ADR row? new req? SDD update? — tell user via /specseed>
```
Then surface followups to the user (decision → `/specseed` for an ADR row; new/changed reqs or SDD pattern → `/specseed adapt`). Only AFTER the user has acted (or said "no spec changes needed") mark the spike `done`.

## Finish

When all plan steps `[x]` and tests pass:
1. (Spike only) complete the spike post-completion steps first.
2. Mark your issue done + clear your claim in `issues.json`:
   ```bash
   jq --arg id "<issue_id>" '.[$id].status="done" | .[$id].claimed_at=null | .[$id].claimed_by=null' \
      .specseed/project_management/issues.json > /tmp/i.json && mv /tmp/i.json .specseed/project_management/issues.json
   ```
   Recommended: mirror the same `status` into the issue's folder file `<issue_id>.md` frontmatter so the human-readable record stays current (`issues.json` is the operational truth; the folder is the authored record).
3. Refresh the ticket index (recomputes completion counts + auto-marks the parent ticket `done` when its last issue lands), then bump the views:
   ```bash
   python .specseed/scripts/tickets_assemble.py
   python .specseed/scripts/roadmap_render.py
   # If the project uses sprints, also refresh the sprint index + TIMELINE:
   python .specseed/scripts/sprints_assemble.py   2>/dev/null && \
     python .specseed/scripts/timeline_render.py
   ```
4. Sanity-check: `python .specseed/scripts/issues_validate.py`
5. Commit per `## Project conventions`.

(Dependent issues unblock automatically — `claim_issue.py` derives ticket completion live from `issues.json` — so the next agent can proceed even before step 3. The re-assemble is for counts, ROADMAP, and the ticket-done rollup.)

## What you CAN edit

- `issues.json` — your OWN issue's `status` (`todo`→`in_progress`→`done`/`blocked`), claim fields (set by `claim_issue.py`, cleared by you on done), `notes`, `artifacts.tests`/`artifacts.migrations` as work progresses
- Your issue's folder: `<issue_id>.md` frontmatter (mirror status; update artifacts/notes), `plan.md`, `spec_concern.md`, `step_reports/*`
- All source code, test files, build configs in your issue's scope

You may NOT edit other issues, any ticket or epic, `tickets.json`, or the spec docs — even if you think something is wrong. Surface to the user instead.

## Optional: drift check before claim

For long-running projects, before claiming you may run:
```bash
python .specseed/scripts/drift_check.py
```
If it flags drift in areas your issue touches (missing test files, stale settled docs vs recent commits), surface to the user before proceeding (`/specseed adapt`). Optional; skip if not present or the project is small/recent.

## Project conventions

> The skill fills this section with the project's actual conventions. The former `CONTRIBUTING.md`, folded in. Keep it short and concrete.

- **Folder structure:** where source, tests, configs live. Spec/PM artifacts live under `.specseed/` (never edit those except as allowed above; only `/specseed` does).
- **Branching:** branch naming + how work maps to branches/PRs.
- **Versioning:** scheme (semver, calver, none) and where the version is set.
- **Release:** how a release is cut, where artifacts are stored.
- **Release gates:** coverage thresholds, smoke checks, manual sign-off — define if relevant; else leave a placeholder.
- **Build/test commands:** canonical commands (or per-component, in each component's `CLAUDE.md`).

---END TEMPLATE---

## Skill-side notes (not written to user's CLAUDE.md)

The template is INTENTIONALLY medium-length. It includes:
- Issue pickup + atomic claim via `claim_issue.py` (no-arg auto-pick = pick-and-claim under one lock; `--skip` for light parallel work; sprint-scoped when `sprints.json` exists — active sprint first, spill to next)
- The three-tier work model (epic/ticket/issue) and the folders-vs-JSON source-of-truth split (folders = authored content, JSON = live runtime state)
- `issue_info.py` for the issue + parent ticket + reqs join (reqs live on the ticket)
- Plan + step-report conventions under `issues/<id>/`
- Don't-edit-settled-docs contract with the structured `spec_concern.md` handoff (now under `issues/<id>/`)
- Spike post-completion checklist
- Finish flow: edit `issues.json`, re-assemble tickets (auto-rolls-up ticket done), `roadmap_render.py` to bump counts, validate
- Optional drift-check hook
- `## Project conventions` (former `CONTRIBUTING.md`)

It does NOT include:
- Detailed multi-agent orchestration rules (add via adapt mode if needed)
- Implementation patterns (those go in code or per-component `CLAUDE.md`)
- Deep per-component build/test detail when multi-component — that lives in each component's `CLAUDE.md`

For multi-component repos, bootstrap stage 12 proposes a per-component `CLAUDE.md`; root `AGENTS.md` directs agents to read those.
