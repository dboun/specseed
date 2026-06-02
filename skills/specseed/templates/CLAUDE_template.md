# Agent runtime entry: CLAUDE.md template

This file is **the content the specseed skill writes to the user's repo root as `CLAUDE.md`**. It tells the implementation agent (Claude Code, Codex, or any other) how to pick up and execute the next **issue** (issues are the technical, claimable unit; epics + tickets are the PM layer above them).

The skill writes this template by default, with three customizations: (1) the bash one-liners in the "Claim" section if the user has a different command preference; (2) the **`## Project conventions`** section, which the skill fills with the project's folder structure, branching, versioning, release process, and release gates (the former `CONTRIBUTING.md` content, now folded in — the skill no longer writes a separate `CONTRIBUTING.md`); (3) the **`## ⚠️ Operating policy`** block, which the skill generates from `.specseed/memory/config.json` by running `python .specseed/scripts/core/config.py render-claude` and **pastes at the very top of the file** (right after the `# CLAUDE.md` heading, before "Agent entry point"). That block is the HITL action-gate + git-workflow contract; it's READ-FIRST and must never be reordered below other sections. Re-run the render and replace the block whenever `/specseed configure` changes the policy.

When writing to the user's repo, write the content below (everything between the `---BEGIN TEMPLATE---` and `---END TEMPLATE---` markers) as the file `CLAUDE.md` at the repo root — and splice the rendered operating-policy block in at the marked spot.

**If a `CLAUDE.md` already exists at the target path**, do NOT overwrite blindly — follow the merge protocol in `SKILL.md` ("Main-repo files & merge protocol").

---BEGIN TEMPLATE---

# CLAUDE.md

<!-- ⚠️ SKILL: paste the output of `python .specseed/scripts/core/config.py render-claude`
     here — the "## ⚠️ Operating policy — READ FIRST, ALWAYS" block (action gates +
     park-and-continue + git workflow), generated from .specseed/memory/config.json.
     It MUST be the first section of the file. Omit only if no config.json exists
     (older repos); then the action-gate/git contract is undefined and the agent
     should ask the user before any push / docker / network / destructive action. -->

Agent entry point. If no other instructions given, your default task is to **handle the next issue in the build order**.

## Work model

Work is organized in three tiers under `.specseed/project_management/`:
- **epics** (`EPIC-NNNN`) and **tickets** (`PROJ-NNNN`) — the PM / non-technical layer (outcomes, user-visible value). Tickets carry the requirements (`satisfies_reqs`) and the critical-path `depends_on` DAG.
- **issues** (`FEAT/BUG/CHORE/SPIKE/QA-NNNN`) — the technical layer. **This is what you claim and execute.** An issue belongs to a ticket (its `ticket` field). A `QA-NNNN` issue is the terminal QA pass for a ticket (see *QA issues* below).

Folders are the authored source of truth; `tickets.json` / `issues.json` are the generated indexes that carry **live runtime state** (status, claims) during execution.

## Initial reads

Always read first:
1. `.specseed/spec/vision.md` — why this project exists
2. The `## Project conventions` section below

## Pick + claim the next issue

One atomic call picks the next ready issue AND claims it (no pick/claim race):

```bash
python .specseed/scripts/core/claim_issue.py
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

**Sprints (if `sprints.json` exists):** auto-pick is sprint-scoped — it prefers issues whose ticket is in the **in_progress** sprint and only spills to the next planned sprint when none are ready (default `--sprint-scope spill`). So normally you just run `claim_issue.py` and get current-sprint work. Use `--sprint-scope current` to refuse spill (stop when the in_progress sprint is drained), or `all` to ignore sprints. No `sprints.json` → unscoped.

To see the ticket-level critical path / build order (priority context):
```bash
python .specseed/scripts/core/tickets_analyze.py .specseed/project_management/tickets.json
```

## Load only relevant context

```bash
python .specseed/scripts/core/issue_info.py <issue_id>
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

## QA issues (`type: qa`)

If your claimed issue is `type: qa`, it's the **terminal QA pass for a ticket** — it `depends_on` all the ticket's other issues, so it runs last. You are not adding features; you are checking the ticket's work holds together. Stay inside a bounded checklist (your technical acceptance criteria spell it out):

- Smoke + regression over the paths the ticket's issues touched (`artifacts.touches`), plus the obvious integration paths between them. Scratch/throwaway work goes in `/tmp`.
- **File every problem as a NEW `bug` issue under the SAME ticket** (run `claim_issue.py`-adjacent scaffolding or hand-author the folder). Make it `priority: high` if it blocks the ticket's value. Do **NOT** silently fix things inside the QA issue, and do **NOT** expand scope beyond the checklist — QA complements dev, it doesn't redo it.
- The ticket can't roll up to `done` until QA and any bugs it spawned are resolved (that's the point — QA holds the ticket open until its findings are addressed).

Finish the QA issue normally once the checklist is run and findings are filed.

## Review & approval gates

Your issue (and its ticket) may carry mandatory gates:
- `review_required: true` → after coding, set your issue `status: "in_review"` (keep your claim) instead of jumping to done. **Also** leave the issue in `in_review` (don't self-close) when the **⚠️ Operating policy → Completion gates** block says code review is on and your issue's `difficulty` is in scope — even if the flag isn't set. A separate reviewer agent then writes `issues/<id>/review.json` (a confidence + verdict); `review_gate.py` advances the issue (auto-close if confidence clears the bar, else `awaiting_approval` for a human). You do NOT write `review.json` or review your own work. Review comes back `changes_requested` → the issue returns to `in_progress`; address the findings and keep going (no separate "changes requested" state).
- `approval_required: true` → when the work is otherwise complete, set `status: "awaiting_approval"` (keep your claim) and **stop**. A human signs off. (Review never bypasses this: an auto-approved review on an `approval_required` issue still lands in `awaiting_approval`.)

**Hard rule:** if a gate is `*_required: true`, **you — the agent that did the work — may NOT advance past it to `done` yourself.** Land it in the gate state (`in_review` / `awaiting_approval`) and hand off. A *different* actor (human, or a reviewer/automation) moves it to `done`. No self-approval, no exception. (If neither flag is set, finish normally below.)

These states keep your claim and are NOT auto-pickable, so nobody steals the issue mid-handoff. To reclaim work that bounced back, reset it to `in_progress`.

**Two kinds of gate, same `awaiting_approval` landing state — don't confuse them:**
- **Completion gate** (this section): `approval_required` / `review_required` ask *"is this finished unit accepted?"* — fired when the issue is otherwise done.
- **Action gate** (the **⚠️ Operating policy** block at the top): a *class of action* (push/docker/network/destructive…) is hit *mid-work*, regardless of which issue is active. You **park-and-continue**: write an `approval.md` request, set `awaiting_approval`, run `approvals_render.py`, and move to the next ready non-gated issue. When you're blocked because **the human must do something out of band** (download a model, provision creds, run a one-off migration), that's a `handoff`-kind park: put the steps in a sidecar `issues/<id>/handoff/` dir (`README.md` + optional helper scripts), point the gate's `Handoff:` field at it, and set a `Verify:` check if you can — full contract in the **⚠️ Operating policy** block.

Both surface to the human through `.specseed/project_management/issues/<id>/approval.md` + the generated `APPROVALS.md` index. A human resolves either by running **`/specseed approve`** (interactive, or via an agent: "next thing needing approval" / "approve <ID> <note>"), or — mirror on — by commenting `approve`/`reject`/`hold <APR-NNNN>` on the CONTROL issue OR directly on the work issue that carries the `🔔` (its own gate; `APR-NNNN` optional when it has a single open gate). Resolution writes a `## Resolved A<N>` marker and flips the issue back (`todo` to resume, `wont_do`/`blocked` if rejected/held). Re-run `approvals_render.py` after any change.

## Instruction inbox (when the runner hands you one)

A work issue can collect free-form human asks/questions in `issues/<id>/inbox.md` (e.g. comments left on the mirror's issue). When the orchestrator asks you to **process an issue's inbox** (a prompt naming the inbox + a cursor), this is NOT a normal claim — work it like this:

1. **Read fresh, never from memory.** Read every `### IN-<seq>` entry after the named cursor, then the issue body, its `plan.md`, `step_reports/`, and the **actual code + git diff** for the issue. Do not assume an earlier session's state — other agents may have changed the tree since.
2. **Treat the unprocessed entries as one batch** (so "don't do it that way" + "also add comments" stay coherent) and classify each message:
   - **Question** → answer it from the real code + step reports. No status change.
   - **In-scope instruction** → do the rework within the issue's **existing scope**. If the issue is already `done`, re-open it (set `in_progress`, re-claim), do the work, then re-close it through the normal **Finish** flow (re-running the review gate if it applies). If it's `blocked`, act anyway — the comment is often what redirects it.
   - **Out of bounds** → **reject that part** and name the door: a settled-doc / requirement / **scope** change → "file a CR" (`add_change_request`, or open a `change-request`-labeled issue on the mirror); genuinely **new work** → "use `add_work`". **Never** edit a settled spec doc from here, and **never** auto-file a CR yourself.
3. **End with one concise reply** summarizing what you did, answered, or rejected (and why) — the runner records it as the `agent (re: IN-…)` entry, posts it back on the issue, and advances the cursor. You do NOT edit `inbox.md` or `inbox.state` — that bookkeeping is the runner's.

This is the free-form counterpart to the deterministic approve/reject/hold gates: an instruction or a question, mediated by you; a *decision* never rides the inbox.

## Finish

When all plan steps `[x]`, tests pass, and any required gates are cleared (see above — do NOT skip a mandatory gate):
1. (Spike only) complete the spike post-completion steps first.
2. Mark your issue done + clear your claim in `issues.json` (skip if a gate left it in `in_review`/`awaiting_approval` — a different actor closes those out):
   ```bash
   jq --arg id "<issue_id>" '.[$id].status="done" | .[$id].claimed_at=null | .[$id].claimed_by=null' \
      .specseed/project_management/issues.json > /tmp/i.json && mv /tmp/i.json .specseed/project_management/issues.json
   ```
   Recommended: mirror the same `status` into the issue's folder file `<issue_id>.md` frontmatter so the human-readable record stays current (`issues.json` is the operational truth; the folder is the authored record).
3. Refresh the ticket index (recomputes completion counts + auto-marks the parent ticket `done` when its last issue lands), then bump the views:
   ```bash
   python .specseed/scripts/core/tickets_assemble.py
   python .specseed/scripts/core/roadmap_render.py
   # If the project uses sprints, also refresh the sprint index + TIMELINE:
   python .specseed/scripts/core/sprints_assemble.py   2>/dev/null && \
     python .specseed/scripts/core/timeline_render.py
   ```
4. Sanity-check: `python .specseed/scripts/core/issues_validate.py`
5. Commit, branch, merge, and push per the **⚠️ Operating policy → Git workflow** block (auto-merge into the integration branch only on a clean close; push only if the policy says `auto`). Action gates (e.g. `external_publish` on a push) still apply.

(Dependent issues unblock automatically — `claim_issue.py` derives ticket completion live from `issues.json` — so the next agent can proceed even before step 3. The re-assemble is for counts, ROADMAP, and the ticket-done rollup.)

## What you CAN edit

- `issues.json` — your OWN issue's `status` (`todo`→`in_progress`→ optional `in_review`/`awaiting_approval` gates →`done`; or `blocked`; `wont_do`/`deprecated` are set by the skill/human, not you), claim fields (set by `claim_issue.py`, cleared by you on done), `notes`, `artifacts.tests`/`artifacts.migrations` as work progresses
- Your issue's folder: `<issue_id>.md` frontmatter (mirror status; update artifacts/notes), `plan.md`, `spec_concern.md`, `approval.md` (append action-gate / run-action / handoff requests — never write your own `## Resolved` marker; only a human/approve-route does that), `handoff/` (a sidecar dir with a `README.md` + optional helper scripts when you park a `handoff`), `step_reports/*`
- All source code, test files, build configs in your issue's scope

You may NOT edit other issues, any ticket or epic, `tickets.json`, or the spec docs — even if you think something is wrong. Surface to the user instead.

## Optional: drift check before claim

For long-running projects, before claiming you may run:
```bash
python .specseed/scripts/core/drift_check.py
```
If it flags drift in areas your issue touches (missing test files, stale settled docs vs recent commits), surface to the user before proceeding (`/specseed adapt`). Optional; skip if not present or the project is small/recent.

## Remote mirror (only if enabled)

> The skill writes this section ONLY when the user opted into the github/gitlab mirror.

This repo mirrors its work layer to github/gitlab issues, driven by an always-on
`<repo>_agents_runner.py` loop. **You don't touch github issues directly** — the
runner projects status onto labels and posts `done`/`blocked` comments for you. Just
do your normal issue work; finishing an issue (status `done`/`blocked` in
`issues.json`) is what the runner mirrors.

- **Local `.specseed/` is the source of truth.** Never hand-edit github issues.
- Ingested bug reports arrive as draft tickets (`type: bug`, body `NEEDS TRIAGE`) —
  treat them like any other `todo` (triage, size, slot into the DAG).
- Don't start/stop the runner yourself unless asked; control is via the pinned CONTROL
  issue or `.specseed/memory/runner.ctl`.

## Project conventions

> The skill fills this section with the project's actual conventions. The former `CONTRIBUTING.md`, folded in. Keep it short and concrete.

- **Folder structure:** where source, tests, configs live. Spec/PM artifacts live under `.specseed/` (never edit those except as allowed above; only `/specseed` does).
- **Branching:** governed by the **⚠️ Operating policy → Git workflow** block at the top of this file (rendered from `config.json`). Don't restate or contradict it here; add only project-specific notes the policy doesn't cover.
- **Versioning:** scheme (semver, calver, none) and where the version is set.
- **Release:** how a release is cut, where artifacts are stored.
- **Release gates:** coverage thresholds, smoke checks, manual sign-off — define if relevant; else leave a placeholder.
- **Build/test commands:** canonical commands (or per-component, in each component's `CLAUDE.md`).

---END TEMPLATE---

## Skill-side notes (not written to user's CLAUDE.md)

The template is INTENTIONALLY medium-length. It includes:
- The **⚠️ Operating policy** block spliced in at the top (rendered by `config.py render-claude` from `config.json`): HITL action gates (block/surface/auto across the 8 categories), the park-and-continue protocol + `approval.md` template, and the git-workflow contract. READ-FIRST; re-rendered when `/specseed configure` changes the policy.
- The two-kinds-of-gate distinction (completion gate = is-this-unit-accepted; action gate = is-this-action-allowed-now) both landing in `awaiting_approval`, surfaced via `approval.md` + `APPROVALS.md`, resolved via the `/specseed approve` route or the CONTROL `approve`/`reject` verbs
- Issue pickup + atomic claim via `claim_issue.py` (no-arg auto-pick = pick-and-claim under one lock; `--skip` for light parallel work; sprint-scoped when `sprints.json` exists — in_progress sprint first, spill to next)
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
