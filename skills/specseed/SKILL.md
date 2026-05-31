---
name: specseed
description: Documentation-driven development spec creation skill. Use whenever user wants to create, draft, update, or revise software specification artifacts — vision, SRS (Software Requirements Spec), SAD (Software Architecture Doc), ADRs, SDD, requirements JSON — plus the project-management work breakdown (ROADMAP, epics, tickets, issues) — for greenfield projects or adapting existing codebases. Trigger on `/specseed` slash command and on phrases like "spec out", "draft requirements", "plan this project", "update the SRS", "add a requirement", "generate tickets", "break into issues", "roadmap", "design doc", "what should we build". Also trigger when user starts a new software project and discusses scope, requirements, or architecture without naming a doc — they likely need this.
---

# specseed

Skill for producing and maintaining software specification artifacts. Routes by mode. Produces a `.specseed/` tree (repo) or chat artifacts.

**Hard rule — never touch the main repo's own files.** All spec artifacts live under `<repo_root>/.specseed/`. The skill does NOT create or edit `spec/`, `docs/`, or any pre-existing source layout. The ONLY files the skill writes into the main repo proper are: `README.md`, root `CLAUDE.md`, per-component `CLAUDE.md` files, and top-level `AGENTS.md` — and each of those, if it already exists, triggers the merge protocol (see "Main-repo files & merge protocol" below). No `CONTRIBUTING.md` is ever written — its content folds into `CLAUDE.md`.

Agent-agnostic — works in Claude Code, Codex, or any agent harness with filesystem access. Detects repo vs chat at session start and delivers accordingly.

## Step 0: Communication style

**Doc writing:** always concise and signal-dense — caveman *spirit*. Read `references/caveman.md` for the style. Apply to all spec docs (vision, SRS, SAD, SDD, ADRs, tickets descriptions, etc): lean prose, fragments OK where unambiguous, no filler, technical terms exact. Agents and humans may read these later without this skill loaded, so prefer lean clarity over maximum compression.

**README.md exception:** normal English, brief, anti-fluff — see `references/bootstrap.md` stage 11.

**User comm:** first message in normal English. At the end of the first message, propose a switch:

> "From now on I'll be terse and compact to save tokens and context. Say `talk normal` any time to switch back."

After the first message, default to caveman-spirit terse comm (no filler, fragments OK, drop articles when unambiguous). If user says `talk normal` at any point, revert to normal English for the rest of the session. Doc-writing style is independent — always caveman-spirit regardless of comm setting.

## Mode detection

Read user message + conversation. Pick ONE mode, commit for the session, do not drift. Auto-escalation between modes only happens where explicitly specified (see `tweak.md`).

| Mode | Trigger | Route |
|------|---------|-------|
| **bootstrap** | New project, no prior spec, user wants full spec from scratch | `references/bootstrap.md` |
| **adapt** | Existing spec present, user wants to update/extend/revise non-trivially | `references/adapt.md` |
| **tweak** | Tiny single-doc edit ("add this one req to SRS", "change priority of REQ-X") | `references/tweak.md` (may auto-escalate to adapt) |

Ambiguous → ask user once which mode. Don't guess.

**Mid-session stop:** at any time, user may say `/specseed stop` (or "stop", "exit", "end session"). On receipt:
1. Write rollup of current state to `memory.md` (or chat artifact in chat mode) — what's done, what's pending, what was about to happen next
2. Deliver any artifacts already drafted but not yet handed off
3. Tell user how to resume (re-invoke `/specseed`; the skill will detect existing state and pick up)
4. Exit cleanly. Do not push to finish, do not guilt the user, do not ask "are you sure"

## First-message template (all modes)

After mode picked, send ~12–15 lines:
- 2 lines: `specseed on. Mode: <mode>.` + one-line of what was detected
- Bullet: what's already there (artifacts found, named; or "none")
- Bullet: stages this session will cover (mode-specific, brief)
- Bullet: end deliverables (mode-specific)
- **Bootstrap mode only:** 1 line setting expectations — "Spec phase takes a beat upfront; the trade is that implementation should be faster, more parallel, and more independent of you afterwards."
- 1 line: `/specseed stop` available anytime to exit cleanly (state preserved for resume)
- 1 line: ask for initial context
- 1 line: terse-comm switch note (see Step 0 above) — only on the very first response of the session

Then route to mode file.

## Shared protocols (load when relevant)

- `references/question-protocol.md` — question round format, action prompts, no-noise rule, anti-max-bias, memory cadence, auto-skip rule for obvious Qs
- `references/component-questions.md` — per-component probing subroutine
- `references/work-breakdown.md` — roadmap + epic/ticket/issue formation: INVEST, vertical slices, critical path (ticket tier), spike post-completion, sizing heuristics

## Memory protocol

Session scratch at `.specseed/memory.md` (repo) or chat artifact (no-repo). Holds:
- Current workflow stage + sub-step
- Per-component summaries when components done
- Locked decisions not yet on disk
- State needed to survive a context compression or `/specseed stop`

**Cadence — revision-gated:**
- After each round: write ONLY IF round had revisions/pivots. Pure-OK rounds skip (matches no-noise rule from `question-protocol.md`)
- At every stage boundary: always write a stage rollup

**Compression hooks:** at natural semantic checkpoints — after each per-component stage done, after settle, after critical-path review — skill *proposes* "consider compress now?" and waits. Does not auto-compress.

**After any context compression: reread `SKILL.md`, then `memory.md` to resume.** Bake this into the last line of `memory.md`: `On resume: reread SKILL.md first, then this file.`

**Delete `.specseed/memory.md` at session end** (or move salient bits to a changelog if user asks). On `/specseed stop` mid-session, KEEP `.specseed/memory.md` intact so resume works on next invocation.

## Output destination

Detect at session start:

**Repo mode (any agent harness with writeable filesystem):**
- Write all spec files to disk under `<repo_root>/.specseed/` per the canonical hierarchy below
- After any script-touched edit (reqs.json regen, tickets.json change, etc), agent runs the relevant analyzer/validator script itself
- Tell user what was written, not the file contents
- **Inform the user, early (first message of repo-mode session) and again at session end:** all spec artifacts live under `.specseed/` and their existing `spec/`/`docs/` (if any) is left untouched; the only files placed in the main repo are `README.md`, `CLAUDE.md` (root + per-component), and `AGENTS.md`

**Chat mode (no repo / no filesystem):**
- Deliver files as chat artifacts
- **Bootstrap:** progressive delivery — each major artifact handed off as its stage completes (vision after stage 2, per-component srs after stage 5, sad after stage 6, etc). At session end, deliver a *manifest* artifact listing every file with its canonical path
- **Adapt:** only the changed files as artifacts + a diff summary in chat
- **Tweak:** only the changed file(s). Nothing else
- After any edit that would trigger a script (reqs.json regen, validation), **remind** the user with copy-paste-ready commands. Do not assume the user has scripts wired
- **Naming:** deliver each file with its full canonical path as its identifier (e.g. `.specseed/spec/api-srs.md`, not bare `srs.md`) so multiple files of the same type don't collide visually in the chat history

## Output hierarchy (canonical)

Everything the skill produces lives under `<repo_root>/.specseed/`, EXCEPT the agent-/user-facing entry files (`README.md`, `CLAUDE.md`(s), `AGENTS.md`) which must sit at their conventional locations in the main repo.

```
# ---- main repo (the ONLY files the skill writes outside .specseed/) ----
README.md                       # user-facing, normal English, brief, anti-fluff. Merge protocol if exists
CLAUDE.md                       # agent runtime entry — write from references/CLAUDE_template.md. Merge protocol if exists
AGENTS.md                       # one line: "Read ./CLAUDE.md. In dirs you work on, read corresponding CLAUDE.md too." Merge protocol if exists
<component>/CLAUDE.md           # OPTIONAL per-component agent notes (multi-component repos). Merge protocol if exists
# NO docs/CONTRIBUTING.md — its content folds into CLAUDE.md (see references/CLAUDE_template.md)

# ---- .specseed/ (all spec artifacts + runtime) ----
.specseed/
├── memory.md                   # session scratch — deleted at end; preserved on /specseed stop
├── spec/                       # the WHAT/WHY/HOW layer (requirements & design)
│   ├── vision.md
│   ├── sad.md
│   ├── adr.csv                 # columns: Decision,Justification
│   ├── srs.md                  # OR per-component (<component>-srs.md)
│   ├── sdd.md                  # OR per-component (<component>-sdd.md)
│   ├── cross-cutting-srs.md    # OPTIONAL — virtual component for cross-cutting concerns
│   ├── reqs.json               # generated from SRS tables
│   └── deployment.md           # OPTIONAL — only if Operations theme flagged in component questioning
└── project_management/         # the WORK layer: epics → tickets → issues (3 tiers, always present)
    ├── ROADMAP.md              # phases → subsections → epics → ticket titles w/ "(X/Y complete)" issue counts
    ├── epics/
    │   └── <EPIC-NNNN>/
    │       └── <EPIC-NNNN>.md  # frontmatter + NON-TECHNICAL prose (goal / outcome / why)
    ├── tickets/
    │   └── <PROJ-NNNN>/
    │       └── <PROJ-NNNN>.md  # PM tier: frontmatter (satisfies_reqs, depends_on=critical path, issues, epic)
    │                           #          + prose (story, description, PRODUCT-level acceptance criteria)
    ├── issues/
    │   └── <FEAT-NNNN>/        # TECHNICAL tier — the claimable/executable unit (absorbs old ticket_tracking/)
    │       ├── <FEAT-NNNN>.md  # frontmatter (component, effort_hours, artifacts, claim fields, ticket parent)
    │       │                   # + prose (TECHNICAL acceptance criteria, notes)
    │       ├── plan.md
    │       ├── spec_concern.md # OPTIONAL — written by impl agent if a settled doc looks wrong mid-issue
    │       └── step_reports/
    │           └── <X>_<step>_<desc>.md
    ├── tickets.json            # GENERATED by tickets_assemble.py (folders are source of truth, NOT this)
    └── issues.json             # GENERATED by issues_assemble.py (folders are source of truth, NOT this)

# ---- .specseed/scripts/ ----
scripts/                        # may grow subfolders as more tooling is added
├── requirements_generate_json.py   # parses SRS table rows → reqs.json
├── requirements_analyze.py         # USER-PROVIDED — req cycle/orphan detection
├── issues_assemble.py              # issue folders → issues.json
├── tickets_assemble.py             # ticket folders → tickets.json (derives effort + X/Y counts from issues)
├── issues_validate.py              # issue-tier schema/refs/claim-invariant checks
├── tickets_validate.py             # ticket-tier schema/refs/cycle checks (kept SEPARATE — see below)
├── tickets_analyze.py              # USER-PROVIDED — ticket critical path + build order
├── issue_info.py                   # issue + parent ticket + reqs joined from parent ticket
├── claim_issue.py                  # atomic issue claim; no-arg auto-picks next ready issue; --skip; stale recovery
├── roadmap_render.py               # bump "(X/Y complete)" counts on ticket lines in ROADMAP.md from tickets.json
├── verification_map.py             # inverse map: req → ticket → issues → test files
└── drift_check.py                  # mechanical spec-vs-reality drift surface
```

Notes:
- **Three work tiers, always present:** `epic → ticket → issue`. **Epics + tickets are PM / non-technical** (outcomes, user-visible value). **Issues are technical** — the unit an agent claims and executes (carry `artifacts`, `effort_hours`, `plan.md`, `step_reports/`). An issue MAY belong to a ticket; a ticket MAY belong to an epic. There is NO separate "story" tier — a user story is a section inside a ticket's prose body.
- **Folders are the source of truth.** Each entity = a folder with a main `<id>.md`: YAML-ish **frontmatter** (machine fields) + markdown **prose** body (human text). `tickets.json` / `issues.json` are GENERATED by the assemble scripts — never hand-author them. Frontmatter is flat `key: value`; structured values (lists, objects) use inline JSON.
- **Critical path runs at the TICKET tier**, not issues. `depends_on` on tickets is the analyzed DAG (`tickets_analyze.py`). Issues may carry optional intra-ticket `depends_on` for local ordering. Per-ticket `effort_hours` is DERIVED (summed from child issues) by `tickets_assemble.py`.
- **Requirements live on TICKETS** (`satisfies_reqs`), not issues — a ticket is the unit of user-visible value that fulfills a requirement. Tests live on ISSUES (`artifacts.tests`). Verification therefore walks `req → ticket → issues → tests` (`verification_map.py`).
- **No `test_plan.md`.** SRS does NOT carry a `verified_by` column — that data would duplicate and drift.
- `deployment.md` is optional and created only when explicitly relevant. (Project grouping/sequencing is handled by ROADMAP phases; sprints may come later. No `milestones.md`.)
- `cross-cutting-srs.md` is optional — bootstrap proposes it only when context indicates cross-cutting concerns materially matter (security, observability, i18n, accessibility). Treated as a virtual component by all machinery (ID prefix `SRS-CC-NNN`).
- `reqs.json` IS generated from SRS markdown tables (humans edit SRS, script extracts).
- **Assemble before analyze/claim/validate.** After editing any ticket/issue folder: run `issues_assemble.py` then `tickets_assemble.py` (ticket effort + counts are summed from issues, so issues go first), then the validators / `tickets_analyze.py` / `claim_issue.py`.
- **`tickets_*` and `issues_*` scripts are deliberately separate** (separate assemble, separate validate). Tickets and issues may live in different stores once tool integrations land; each tier validates the refs it can resolve and degrades gracefully when the other tier is absent.
- `spec_concern.md` is written by the **implementation agent** (not this skill) when it discovers a settled doc looks wrong during issue execution. Adapt mode picks these up as valid triggers — see `references/adapt.md` stage 2.
- `claim_issue.py` replaces any raw `jq` claim. Uses `fcntl.flock` for atomic read-verify-write on `issues.json`; auto-recovers stale claims (default >3h old); no-arg call auto-picks the next ready issue and claims it in the same locked op; `--skip <ids>` excludes issues (lightweight parallel-agent support). Lock releases on process exit.
- `drift_check.py` is a mechanical drift detector — runs as part of adapt mode assessment, optionally before impl agents claim long-running issues.

## Main-repo files & merge protocol

The skill writes four kinds of file into the main repo (everything else goes under `.specseed/`): `README.md`, root `CLAUDE.md`, per-component `CLAUDE.md`, and top-level `AGENTS.md`. `CONTRIBUTING.md` is NOT one of them — its content (folder structure, branching, versioning, release, release gates, conventions) folds into `CLAUDE.md`.

**Inform the user** (first message of a repo-mode session, and again at session end) which of these will land in the main repo and that everything else is confined to `.specseed/`.

**If the file does NOT already exist:** write the skill's version directly.

**If the file ALREADY exists** (`README.md`, any `CLAUDE.md`, or `AGENTS.md`): do NOT silently overwrite. Discuss a merge strategy with the user before writing:
1. Read the existing file
2. **Default recommendation: replace** with the skill's version (its structure is what the implementation agent + downstream machinery expect).
3. **But scan the existing file for interesting/non-obvious additions** (project-specific commands, gotchas, conventions, links) the skill's version would drop. If any exist, surface them and **propose appending** them into the skill's version (e.g. under a "Project-specific notes" section) rather than losing them.
4. Present the choice: **replace** / **replace + append-their-extras** / **keep theirs (skip)**. Recommend option 2 when the existing file has real content worth keeping, option 1 when it's boilerplate/stale.
5. Never destroy the user's content without an explicit OK.

This applies in both bootstrap (stage 11) and adapt/tweak when these files are (re)generated.

## Settled vs editable

**Contract, not enforcement.** Settled is a soft-frozen marker honored by agents using this skill's `CLAUDE.md` template. The filesystem does not block edits. Other agents, scripts, or humans can edit settled docs freely — and doing so desyncs traceability (adapt mode assumes settled = stable). If your runtime doesn't follow the contract, the safety guarantees here don't apply.

After srs/sdd/sad/vision are **settled** (soft-frozen with user approval), the *implementation* agent (running via `CLAUDE.md` to pick up issues) MUST NOT edit them. Only this skill can revise them, via adapt mode.

If the implementation agent finds a settled doc looks wrong mid-issue, it MUST:
1. Mark its current issue `status: "blocked"`
2. Write `.specseed/project_management/issues/<issue_id>/spec_concern.md` describing what's wrong, why, and what it would change
3. Tell the user: **"Use `/specseed adapt` to address spec concern: .specseed/project_management/issues/<id>/spec_concern.md"**

The implementation agent does NOT edit the settled doc itself, ever. Adapt mode is the only path through. When adapt mode reopens a settled doc, it logs to `adr.csv`.

## User-provided scripts

`.specseed/scripts/requirements_analyze.py` and `.specseed/scripts/tickets_analyze.py` are USER-PROVIDED — existing tools the user has. If not present at session start when a flow stage would call them, skill asks the user to provide them or describe their interface before proceeding.

## Carry-forward (post-rewrite)

- [ ] User-provided analyzers (`requirements_analyze.py`, `tickets_analyze.py`) — `tickets_analyze.py` now runs on the assembled `project_management/tickets.json` (per-ticket `effort_hours` is derived from child issues). User to remove `requirements_analyze.py` check 4 (verification gaps) since `verified_by` no longer in reqs.json.
- ROADMAP `(X/Y complete)` counts: `roadmap_render.py` bumps them in-place from `tickets.json` (`issues_done`/`issues_total`). Runs in the issue finish flow (after `tickets_assemble.py`) and the adapt/tweak cascades; `--check` reports drift without writing. Counts are display-only — `claim_issue.py` derives unblocking from `issues.json`, not ROADMAP.
- [ ] Templates folder for vision/sad/srs/sdd skeletons (deferred)
- [ ] Evals / test cases for the skill itself (deferred)
- [ ] `spec_concern.md` template — for now, format is documented in `references/CLAUDE_template.md`
