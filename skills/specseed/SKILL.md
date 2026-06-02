---
name: specseed
description: Documentation-driven development spec creation skill. Use whenever user wants to create, draft, update, or revise software specification artifacts — vision, SRS (Software Requirements Spec), SAD (Software Architecture Doc), ADRs, SDD, requirements JSON — plus the project-management work breakdown (ROADMAP, epics, tickets, issues, sprints, TIMELINE) — for greenfield projects or adapting existing codebases. Trigger on `/specseed` slash command and on phrases like "spec out", "draft requirements", "plan this project", "update the SRS", "add a requirement", "generate tickets", "break into issues", "roadmap", "plan sprints", "assign to a sprint", "design doc", "what should we build", "recover/reverse-engineer a spec from this codebase", "onboard this existing project", "adopt this repo", "configure specseed", "set up github/gitlab tracking", "mirror issues to github". Also trigger when user starts a new software project and discusses scope, requirements, or architecture without naming a doc — they likely need this.
---

# specseed

Skill for producing and maintaining software specification artifacts. Routes by mode. Produces a `.specseed/` tree (repo) or chat artifacts.

**Hard rule — never touch the main repo's own files.** All spec artifacts live under `<repo_root>/.specseed/`. The skill does NOT create or edit `spec/`, `docs/`, or any pre-existing source layout. The ONLY files the skill writes into the main repo proper are: `README.md`, root `CLAUDE.md`, per-component `CLAUDE.md` files, and top-level `AGENTS.md`; if a GitHub/GitLab mirror is enabled and the user opts in, it may also project user-facing entity templates to the provider's top-level issue-template directory. Each main-repo file, if it already exists, triggers the merge protocol (see "Main-repo files & merge protocol" below). No `CONTRIBUTING.md` is ever written — its content folds into `CLAUDE.md`.
Similarly, when running from the target repo to spec it out, the skills doesn't edit / auto-improve itself unless the user is very clear about it.

Agent-agnostic — works in Claude Code, Codex, or any agent harness with filesystem access. Detects repo vs chat at session start and delivers accordingly.

## Step 0: Communication style

**Doc writing — two axes, both apply:**

- **Density (caveman *spirit*)** — `references_ext/caveman.md`. Lean, signal-dense, no filler, fragments OK where unambiguous, technical terms exact. Prefer lean clarity over maximum compression (agents + humans read these later without the skill loaded).
- **Naturalness (humanizer)** — `references_ext/humanizer.md`. Spec prose must not read as AI-generated. **Before finalizing any human-readable prose doc, run a humanizer pass**: cut significance/legacy inflation, promotional language, `-ing` padding, rule-of-three, vague attributions, copula avoidance (use *is/are/has*), elegant variation, false ranges, filler, hedging, signposting, generic upbeat conclusions, diff-anchored phrasing; drop the boldface/emoji/title-case/curly-quote tells; and **remove every em/en dash** (`—` / `–` — the strongest single tell), replacing each with a period, comma, colon, or parentheses. Use the lightweight scan (look for *clusters* of tells + the hard em-dash check), not humanizer's full standalone draft→audit→final deliverable.

**Scope + precedence:**
- Humanize the **human-readable prose**: `vision.md`, `README.md`, SAD/SDD prose sections, epic/ticket/issue prose (story, description, acceptance criteria), ADR justifications. Do NOT humanize machine artifacts (frontmatter, `*.json`, SRS requirement-table rows) or the agent-runtime `CLAUDE.md` — those follow fixed formats.
- The two axes mostly agree (both kill puffery). Where they meet: density governs *structure*; humanizer governs *word choice* + the em-dash ban.
- **Personality/voice OFF for specs.** Specs are reference text — per humanizer's own rule, neutral and plain *is* the correct human voice there. Do NOT inject opinions, first person, or manufactured voice. (README may carry a light natural voice but stays plain and brief.)

**README.md** is normal English (not caveman density), brief, anti-fluff — see `routes/bootstrap.md` stage 11 — and gets the full humanizer pass.

**User comm:** first message in normal English. At the end of the first message, propose a switch:

> "From now on I'll be terse and compact to save tokens and context. Say `talk normal` any time to switch back."

After the first message, default to caveman-spirit terse comm (no filler, fragments OK, drop articles when unambiguous). If user says `talk normal` at any point, revert to normal English for the rest of the session. Doc-writing style is independent — always caveman-spirit regardless of comm setting.

## Session-start reconnaissance (run FIRST — before mode detection)

**Mode is chosen from disk evidence, not the user's words alone.** In repo mode, STAT the filesystem before committing a mode. Deterministic, in order:

0. **Version check (run before everything else when a `.specseed/` tree exists).** Compare `<repo>/.specseed/version.txt` against the skill's own `version.txt` (sibling of this `SKILL.md`). Missing tree stamp → treat as `0.1.0`. If the tree's **minor** (or major) version is BEHIND the skill's → the tree may be in an outdated format. **OFFER MIGRATE FIRST** (route `routes/migrate.md`): tell the user "this `.specseed/` was built by `<tree_ver>`, skill is `<skill_ver>`; migrate before continuing?" and recommend yes. A patch-only gap (same x.y, lower z) is non-breaking → don't interrupt; migrate silently re-stamps at the end of whatever runs, or skip. Tree version > skill version → warn the skill install is stale (`install.sh`), don't downgrade. Tree current → proceed to step 1. (No `.specseed/` yet → skip; fresh trees get stamped at creation.)
1. **`.specseed/memory/session_state.md` exists?** → an interrupted session. **OFFER RESUME FIRST.** Read it, tell the user "you were at `<stage>`, about to `<next>`", and ask: **resume / start fresh**. Do NOT auto-resume; do NOT ignore it and start a new flow on top. (On resume: reread this `SKILL.md`, then `session_state.md`, then re-enter the mode/stage it names.)
2. **`.specseed/spec/` exists with content** (any `vision.md` / `*-srs.md` / `sad.md` / `sdd.md`)? → a spec is ALREADY present. **Bootstrap is OFF the table** unless the user explicitly says "start over / throw it away". Even if the user's words sound greenfield ("spec out the payments feature"), an existing tree means **adapt** (change/extend settled docs), **plan-next** (roadmap has un-detailed titles + user wants the next slice), or **tweak** (tiny edit). Name what was found, route accordingly, confirm if ambiguous — never silently bootstrap over it.
3. **Source files present but NO `.specseed/spec/` content** → existing code, no spec → **adopt**. Recover the spec FROM the codebase (+ any docs already there) into `.specseed/`. NOT bootstrap — bootstrap is for a blank slate and would spec from conversation, drifting from the real code on day one. If a partial/config-only `.specseed/` exists (for example `config.json`, `remote.json`, or work-layer folders but no spec content and no `session_state.md`), name it as a partial setup and continue with adopt. `config.json` / `remote.json` are config only, not an existing spec.
4. **Empty / near-empty, no `.specseed/spec/` content** → greenfield → **bootstrap**. If a partial/config-only `.specseed/` exists, keep its config and bootstrap the spec/work layer; do not treat it as a prior spec.

**Chat mode (no writeable FS):** skip the probe; route from conversation + any artifacts the user pasted.

This probe is the safety net for misrouting — it is evidence, not a guess. When it and the user's words disagree, surface the conflict and ask; do not let intent words override disk state.

**Configure preamble (first run only).** When the probe lands on **bootstrap** (case 4) or **adopt** (case 3) AND there is **no `.specseed/memory/config.json`** yet, run **configure mode** first (technical setup — `routes/configure.md`): ≤2 rounds, heavy defaults (local-only + default git/gates is one keystroke), then continue into bootstrap/adopt. This gets the plumbing + autonomy decisions out of the way up front. The `.specseed/memory/config.json` (always — the portable "how-you-work" file) and `remote.json` (per-repo mirror state, only if a mirror) it leaves behind are **config only** — NOT a spec, and do NOT affect the routing above (mode still keys off `.specseed/spec/`). `/specseed configure` re-runs it anytime to change settings.

## Mode detection

Read user message + conversation, **constrained by the reconnaissance above** (an existing `.specseed/spec/` forbids bootstrap; a present `session_state.md` means offer resume first). Pick ONE mode, commit for the session, do not drift. Auto-escalation between modes only happens where explicitly specified (see `tweak.md`).

| Mode | Trigger | Route |
|------|---------|-------|
| **configure** | TECHNICAL setup only — local vs github/gitlab mirror, credentials, runner opts (NOT spec content). `/specseed configure`, or the auto-preamble on a repo's first bootstrap/adopt. Editable anytime | `routes/configure.md` |
| **bootstrap** | New project, no prior spec, user wants full spec from scratch | `routes/bootstrap.md` |
| **adopt** | Existing CODE, no `.specseed/`. Recover the spec from the codebase (+ import any docs already present) into `.specseed/`. Reverse-bootstrap; specseed never edits code | `routes/adopt.md` |
| **plan-next** | Existing `incremental`-bootstrapped spec; user wants to spec + break down the NEXT roadmap slice (`/specseed plan-next`, "plan the next sprint/phase"). Roadmap has un-detailed ticket titles (no folders). Append-only forward — no settled-doc changes | `routes/plan-next.md` |
| **adapt** | Existing spec present, user wants to update/extend/revise non-trivially (incl. *changing* settled docs) | `routes/adapt.md` |
| **tweak** | Tiny single-doc edit ("add this one req to SRS", "change priority of REQ-X") | `routes/tweak.md` (may auto-escalate to adapt) |
| **change-request** | A filed spec-change request (`CR-NNNN`) processed headless by the runner: async clarifying conversation → plan → explicit approval → regenerate via adapt, isolated on a `cr/` branch. NOT a human-typed mode (the runner invokes it); humans at the machine use `adapt` | `routes/change-request.md` |
| **approve** | Resolve pending human-approval gates (the impl agent parked gated work). `/specseed approve`, "next thing needing approval", "approve/reject/hold <ID>". Read-only on code; touches `approval.md` + issue status | `routes/approve.md` |
| **migrate** | A `.specseed/` tree built by an older skill version needs bringing up to the running skill's format. Auto-offered by the reconnaissance version check; or `/specseed migrate`. Edits only the tree (+ entry files); no re-spec | `routes/migrate.md` |

**plan-next vs adapt:** plan-next *extends forward* into un-specced roadmap titles (append-only, never reopens `settled` docs); adapt *changes* existing/settled specs. If unsure: does the work touch a settled doc? → adapt. Does it only add the next slice? → plan-next. See `routes/plan-next.md` "Boundary".

Ambiguous → ask user once which mode. Don't guess.

**Mid-session stop:** at any time, user may say `/specseed stop` (or "stop", "exit", "end session"). On receipt:
1. Write rollup of current state to `.specseed/memory/session_state.md` (or chat artifact in chat mode) — what's done, what's pending, what was about to happen next
2. Deliver any artifacts already drafted but not yet handed off
3. Tell user how to resume (re-invoke `/specseed`; the skill will detect existing state and pick up)
4. Exit cleanly. Do not push to finish, do not guilt the user, do not ask "are you sure"

## First-message template (all modes)

After mode picked, send ~12–15 lines:
- 2 lines: `specseed on. Mode: <mode>.` + one-line of what was detected
- Bullet: what's already there (artifacts found, named; or "none")
- Bullet: stages this session will cover (mode-specific, brief)
- Bullet: end deliverables (mode-specific)
- **Bootstrap mode only:** 1 line setting expectations — "Spec phase takes a beat upfront; the trade is that implementation should be faster, more parallel, and more independent of you afterwards. After we sketch the vision I'll propose a depth (lite / standard / incremental) so big projects don't get specced out for hours before any code."
- **Adopt mode only:** 1 line setting expectations — "I'll read the code (read-only — I never edit your source) and recover the spec from it into `.specseed/`, importing any docs you already have. After recon I'll propose a depth; built work gets mapped in the roadmap, remaining gaps get broken into work."
- 1 line: `/specseed stop` available anytime to exit cleanly (state preserved for resume)
- 1 line: ask for initial context
- 1 line: terse-comm switch note (see Step 0 above) — only on the very first response of the session

Then route to mode file.

## Shared protocols (load when relevant)

- `references_ext/caveman.md` — doc-writing density style (lean, signal-dense); see Step 0
- `references_ext/humanizer.md` — anti-AI-tell finish-pass for human-readable prose docs (vision/README/SAD-SDD prose/ticket prose/ADR justifications); see Step 0 for scope + the em-dash ban
- `references/question-protocol.md` — question round format, action prompts, no-noise rule, anti-max-bias, memory cadence, auto-skip rule for obvious Qs
- `references/component-questions.md` — per-component probing subroutine
- `references/work-breakdown.md` — roadmap + epic/ticket/issue formation: INVEST, vertical slices, critical path (ticket tier), spike post-completion, sizing heuristics
- `routes/configure.md` — technical setup route (local vs github/gitlab mirror, credentials, runner opts). Runs as a first-run preamble before bootstrap/adopt, or on `/specseed configure`. Writes the portable `.specseed/memory/config.json` (always) + per-repo `remote.json` (mirror only) — config, not spec
- `references/remote.md` — OPTIONAL, opt-in github/gitlab mirror (single-dev phone-driven workflow). Local stays ground truth; the remote is a mirror + bug-inbox + CONTROL command channel driven by an always-on `agents_runner.py`. Offered once at onboarding (bootstrap stage 13.5 / adopt 9.5); entirely off unless the user opts in
- `routes/approve.md` — the `approve` route: walk + resolve pending HITL gates (`approval.md` requests the impl agent parked). Local human channel; also what the mirror's CONTROL `approve`/`reject` verbs invoke
- `routes/change-request.md` — the `change-request` route: the headless conductor the runner invokes per relay turn to drive ONE filed `CR-NNNN` (async conversation → approval → regenerate via adapt's stages, on an isolated `cr/` branch). NOT detected from disk — invoked explicitly by the runner; do not add it to session-start reconnaissance
- `routes/migrate.md` — the `migrate` route: bring an older-version `.specseed/` tree up to the running skill's format. Reads the skill's `version.txt` + the tree's `.specseed/version.txt`, applies the in-range files from `migrations/`. See `migrations/README.md` for the x.y.z model + file format. Auto-offered by the reconnaissance version check (step 0)
- **HITL policy** — the action-gate + git-workflow contract the impl agent obeys lives in `.specseed/memory/config.json` (the portable config; written by configure mode, ALWAYS — even local-only) and is rendered into the READ-FIRST block of `CLAUDE.md` by `scripts/core/config.py`. Per-issue gating (which issues get an `approval_required` sign-off) is refined at work-breakdown time — see `references/work-breakdown.md` "Risk-detection & gating pass"

## Memory protocol

**Rule: ALL skill memory lives under `.specseed/memory/`** (a directory, never a bare file at `.specseed/` root). Session/planning memory:
- `session_state.md` — **session scratch**, the resume/compression-survival file (was `.specseed/memory.md`). Deleted at session end; preserved on `/specseed stop`.
- `sprint_planning.md` — **reusable** sprint-planning preferences that PERSIST across sessions (e.g. "keep auth + session tickets in one sprint", "leave ~15% slack"). Write ONLY durable, generalizable prefs the user states during planning — keep it tiny, not session chatter, not one-off placements. Never deleted at session end.

Also co-located here but **config, not memory** (written by configure mode, never deleted at session end): `config.json` (the PORTABLE "how-you-work" file — HITL action-gates + git workflow + backend choice + runner knobs + code-review gate + QA policy; copy it between repos) and `remote.json` (per-repo mirror STATE: repo, allowlist, issue map, cursors — NOT portable, mirror only).

`session_state.md` (repo) or chat artifact (no-repo) holds:
- Current workflow stage + sub-step
- Per-component summaries when components done
- Locked decisions not yet on disk
- State needed to survive a context compression or `/specseed stop`

**Cadence — revision-gated:**
- After each round: write ONLY IF round had revisions/pivots. Pure-OK rounds skip (matches no-noise rule from `question-protocol.md`)
- At every stage boundary: always write a stage rollup

**Compression hooks:** at natural semantic checkpoints — after each per-component stage done, after settle, after critical-path review — skill *proposes* "consider compress now?" and waits. Does not auto-compress.

**After any context compression: reread `SKILL.md`, then `.specseed/memory/session_state.md` to resume.** Bake this into the last line of `session_state.md`: `On resume: reread SKILL.md first, then this file.`

**Delete `.specseed/memory/session_state.md` at session end** (or move salient bits to a changelog if user asks). On `/specseed stop` mid-session, KEEP it intact so resume works on next invocation. `sprint_planning.md` is NOT deleted — it is durable cross-session memory.

## Output destination

Detect at session start:

**Repo mode (any agent harness with writeable filesystem):**
- Write all spec files to disk under `<repo_root>/.specseed/` per the canonical hierarchy below
- After any script-touched edit (reqs.json regen, tickets.json change, etc), agent runs the relevant analyzer/validator script itself
- Tell user what was written, not the file contents
- **Inform the user, early (first message of repo-mode session) and again at session end:** all spec artifacts live under `.specseed/` and their existing `spec/`/`docs/` (if any) is left untouched; the only files placed in the main repo are `README.md`, `CLAUDE.md` (root + per-component), and `AGENTS.md`

**Chat mode (no repo / no filesystem):**
- Deliver files as chat artifacts
- **Bundle protocol:** once 2+ files exist OR the folder structure matters, also deliver a zip artifact named `specseed-bundle.zip` with the full canonical tree (`.specseed/...` plus root entry files). Refresh the zip at coherent checkpoints (major stage end, after work breakdown, session end), not after every tiny edit. In each round, attach individual artifacts only for files created/changed in that round; the zip is the complete handoff.
- Tell the user when the bundle appears: "The zip is enough to unpack into a repo with the right structure; the separate artifacts are only the files changed/created this round for review." If the host cannot attach zip/binary artifacts, say so and fall back to manifest + individual file artifacts.
- **Bootstrap:** progressive individual-file delivery — each major artifact handed off as its stage completes (vision after stage 2, per-component srs after stage 5, sad after stage 6, etc). At session end, deliver a *manifest* artifact listing every file with its canonical path
- **Adapt:** only the changed files as individual artifacts + a diff summary in chat; bundle refresh follows the bundle protocol above
- **Tweak:** only the changed file(s) as individual artifacts. Refresh the zip only if a mature file set already exists and the change should replace the user's local tree; skip zip for typo-only noise
- After any edit that would trigger a script (reqs.json regen, validation), **remind** the user with copy-paste-ready commands. Do not assume the user has scripts wired
- **Naming:** deliver each file with its full canonical path as its identifier (e.g. `.specseed/spec/api-srs.md`, not bare `srs.md`) so multiple files of the same type don't collide visually in the chat history

## Output hierarchy (canonical)

Everything the skill produces lives under `<repo_root>/.specseed/`, EXCEPT the agent-/user-facing entry files (`README.md`, `CLAUDE.md`(s), `AGENTS.md`) which must sit at their conventional locations in the main repo.

```
# ---- main repo (the ONLY files the skill writes outside .specseed/) ----
README.md                       # user-facing, normal English, brief, anti-fluff. Merge protocol if exists
CLAUDE.md                       # agent runtime entry — write from templates/CLAUDE_template.md. Merge protocol if exists
AGENTS.md                       # one line: "Read ./CLAUDE.md. In dirs you work on, read corresponding CLAUDE.md too." Merge protocol if exists
<component>/CLAUDE.md           # OPTIONAL per-component agent notes (multi-component repos). Merge protocol if exists
.github/ISSUE_TEMPLATE/*.md     # OPTIONAL GitHub projection: bug / feature / change-request only, if mirror + user opt-in
.gitlab/issue_templates/*.md    # OPTIONAL GitLab projection: bug / feature / change-request only, if mirror + user opt-in
# NO docs/CONTRIBUTING.md — its content folds into CLAUDE.md (see templates/CLAUDE_template.md)

# ---- .specseed/ (all spec artifacts + runtime) ----
.specseed/
├── version.txt                 # the skill version this tree was last built/migrated to (single line x.y.z). Missing → assume 0.1.0. Drives the migrate route. Stamped at creation (configure Persist), re-stamped by migrate
├── README.md                   # HUMAN operator manual (run/kill the runner, approve, configure, github/gitlab). Written at first setup from templates/specseed-README_template.md
├── entity_templates/           # canonical templates agents use to scaffold epics/tickets/issues + user-facing bug/feature/CR intake
│   ├── epic.md
│   ├── ticket.md
│   ├── issue.md
│   ├── bug.md
│   ├── feature.md
│   └── change-request.md
├── memory/                     # ALL skill memory lives here (dir, not a single file)
│   ├── session_state.md        #   session scratch — deleted at end; preserved on /specseed stop
│   ├── sprint_planning.md      #   reusable sprint-planning prefs (tiny, persists across sessions)
│   ├── config.json             #   CONFIG (PORTABLE): hitl gates + git workflow + backend choice + runner knobs + review gate + qa policy (configure mode; ALWAYS present)
│   └── remote.json             #   STATE (per-repo, NOT portable): mirror repo/allowlist/issue-map/cursors (only if mirror; see remote.md)
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
    ├── ROADMAP.md              # STRATEGIC map: phases → subsections → epics → ticket titles w/ "(X/Y complete)". Sprints NEVER appear here.
    ├── TIMELINE.md             # TACTICAL schedule: sprints in execution order (GENERATED by timeline_render.py)
    ├── APPROVALS.md            # pending HITL gates, human view (GENERATED by approvals_render.py)
    ├── approvals.json          # pending HITL gates, machine index (GENERATED by approvals_render.py)
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
    │       ├── approval.md     # OPTIONAL — HITL gate requests (action-gate / run-action / completion). Resolved via approve route
    │       └── step_reports/
    │           └── <X>_<step>_<desc>.md
    ├── sprints/
    │   └── <SPRINT_YYYY_WWW_X>/
    │       └── <SPRINT_YYYY_WWW_X>.md  # frontmatter (status, starts/ends, tickets[]) + prose (goal, carry-over notes)
    ├── tickets.json            # GENERATED by tickets_assemble.py (folders are source of truth, NOT this)
    ├── issues.json             # GENERATED by issues_assemble.py (folders are source of truth, NOT this)
    └── sprints.json            # GENERATED by sprints_assemble.py (folders are source of truth, NOT this)

# ---- .specseed/scripts/ ----
scripts/
├── agents_runner.py            # human-run entry — start/kill the always-on orchestrator loop (+ --write-shim). Top-level on purpose. Runs the work step + (if review on) a per-loop review step; per-function/difficulty agent (claude|codex) via config.runner.agents
├── add_work.py                 # human-run entry — add a MANUAL work item (ticket + issue) out of band; high-priority → current sprint, else backlog; NO sprint replan
├── core/                       # agent-invoked plumbing + analysis seams (humans don't run these directly)
│   ├── requirements_generate_json.py   # parses SRS table rows → reqs.json
│   ├── requirements_analyze.py         # req cycle/orphan detection (shipped; editable analysis seam)
│   ├── issues_assemble.py              # issue folders → issues.json
│   ├── tickets_assemble.py             # ticket folders → tickets.json (derives effort + X/Y counts from issues)
│   ├── sprints_assemble.py             # sprint folders → sprints.json (derives effort + ticket counts from tickets)
│   ├── issues_validate.py              # issue-tier schema/refs/claim-invariant checks
│   ├── tickets_validate.py             # ticket-tier schema/refs/cycle checks (kept SEPARATE — see below)
│   ├── sprints_validate.py             # sprint-tier: refs, back-consistency, NO backward sprint deps, budget
│   ├── tickets_analyze.py              # ticket critical path + build order (shipped; editable analysis seam) — PROJECT-level, NOT per-sprint
│   ├── sprint_plan.py                  # ADVISORY sprint packing proposal (cohesion-aware, CP-first, budget); never writes
│   ├── issue_info.py                   # issue + parent ticket + reqs joined from parent ticket
│   ├── claim_issue.py                  # atomic issue claim; no-arg auto-picks next ready issue; sprint-scoped (--sprint-scope); priority + created_at ordering; --skip; stale recovery
│   ├── review_gate.py                  # code-review decision seam: reads issues/<id>/review.json + config.review + difficulty → auto-approve / awaiting_approval / changes (--apply mutates status)
│   ├── roadmap_render.py               # bump "(X/Y complete)" counts on ticket lines in ROADMAP.md from tickets.json
│   ├── timeline_render.py              # regenerate TIMELINE.md (sprint schedule) from sprints.json + tickets.json
│   ├── verification_map.py             # inverse map: req → ticket → issues → test files
│   ├── drift_check.py                  # mechanical spec-vs-reality drift surface
│   ├── config.py                       # PORTABLE config: load/validate config.json (hitl + git + backend + runner + review + qa); render-claude → CLAUDE.md block (incl. review/QA completion-gate contract)
│   ├── entity_templates.py             # writes .specseed/entity_templates and optional GitHub/GitLab issue-template projection
│   └── approvals_render.py             # scan issues/*/approval.md → APPROVALS.md + approvals.json (pending HITL gates)
└── remote/                     # OPTIONAL mirror cluster (only present/used if the user opts in — see references/remote.md)
    ├── github_functions.py             # stdlib GitHub REST wrapper (+ GraphQL pin)
    ├── gitlab_functions.py             # stdlib GitLab REST wrapper (sibling shape)
    ├── remote_config.py                # remote.json STATE I/O + load_runtime (merge backend provider) + provider-agnostic adapter
    ├── remote_sync.py                  # local→remote mirror engine (init/reconcile/push/dashboards)
    └── remote_control.py               # CONTROL-issue command channel (poll/authorize/dispatch)
```

The remote-mirror scripts + `.specseed/memory/remote.json` exist ONLY when the user opts into the mirror. They are shipped plumbing (not analysis seams) and never run otherwise. Host issue-template dirs are also opt-in projection only; canonical templates always stay in `.specseed/entity_templates/`.

Notes:
- **Three work tiers, always present:** `epic → ticket → issue`. **Epics + tickets are PM / non-technical** (outcomes, user-visible value). **Issues are technical** — the unit an agent claims and executes (carry `artifacts`, `effort_hours`, `plan.md`, `step_reports/`). An issue MAY belong to a ticket; a ticket MAY belong to an epic. There is NO separate "story" tier — a user story is a section inside a ticket's prose body.
- **Folders are the source of truth.** Each entity = a folder with a main `<id>.md`: YAML-ish **frontmatter** (machine fields) + markdown **prose** body (human text). `tickets.json` / `issues.json` are GENERATED by the assemble scripts — never hand-author them. Frontmatter is flat `key: value`; structured values (lists, objects) use inline JSON.
- **Critical path runs at the TICKET tier**, not issues, and is **PROJECT-level** — the dep DAG crosses sprint boundaries, so CP is computed over all tickets (`tickets_analyze.py`), NOT per sprint. `depends_on` on tickets is the analyzed DAG. Issues may carry optional intra-ticket `depends_on` for local ordering. Per-ticket `effort_hours` is DERIVED (summed from child issues) by `tickets_assemble.py`.
- **Sprints are a 4th, ORTHOGONAL grouping** (time-box; ~168h soft budget). An epic groups tickets by outcome; a sprint groups them by time — a ticket has one of each (`epic:` + `sprint:`). Sprints drive `TIMELINE.md` + claim ordering, never `ROADMAP.md`. CP *informs* sprint assignment (front-load CP tickets); sprints don't change how CP is computed. See `references/work-breakdown.md` ("Sprints").
- **Status model (per-tier enum + gates):** one `status` per tier — issue `{todo, in_progress, blocked, in_review, awaiting_approval, done, wont_do, deprecated}`, ticket drops `in_review`, epic is coarse (`todo, in_progress, done, wont_do, deprecated`), sprint `{planned, in_progress, done, deprecated}` (`in_progress` = the claim-target, replaced the old `active`). Terminal/"resolved" = `{done, wont_do, deprecated}`. `wont_do` (never built) ≠ `deprecated` (was real, retired). Mandatory gates: issue `review_required` / `approval_required`, ticket `approval_required` — the agent that did the work may NOT self-bypass a gate (runtime contract). Validators check enum + claim invariants only, NOT a transition graph (agents stay flexible). Full spec in `references/work-breakdown.md` ("Status model & lifecycle").
- **Requirements live on TICKETS** (`satisfies_reqs`), not issues — a ticket is the unit of user-visible value that fulfills a requirement. Tests live on ISSUES (`artifacts.tests`). Verification therefore walks `req → ticket → issues → tests` (`verification_map.py`).
- **No `test_plan.md`.** SRS does NOT carry a `verified_by` column — that data would duplicate and drift.
- `deployment.md` is optional and created only when explicitly relevant. (Strategic grouping → ROADMAP phases; tactical scheduling → sprints / `TIMELINE.md`. No `milestones.md`.)
- `cross-cutting-srs.md` is optional — bootstrap proposes it only when context indicates cross-cutting concerns materially matter (security, observability, i18n, accessibility). Treated as a virtual component by all machinery (ID prefix `SRS-CC-NNN`).
- `reqs.json` IS generated from SRS markdown tables (humans edit SRS, script extracts).
- **Assemble before analyze/claim/validate.** After editing any ticket/issue/sprint folder: run `issues_assemble.py` → `tickets_assemble.py` → `sprints_assemble.py` (each tier's derived fields read the tier below: ticket effort sums from issues, sprint effort sums from tickets — so order matters), then the validators / `tickets_analyze.py` / `claim_issue.py` / `timeline_render.py`.
- **`tickets_*` and `issues_*` scripts are deliberately separate** (separate assemble, separate validate). Tickets and issues may live in different stores once tool integrations land; each tier validates the refs it can resolve and degrades gracefully when the other tier is absent.
- `spec_concern.md` is written by the **implementation agent** (not this skill) when it discovers a settled doc looks wrong during issue execution. Adapt mode picks these up as valid triggers — see `routes/adapt.md` stage 2.
- `claim_issue.py` replaces any raw `jq` claim. Uses `fcntl.flock` for atomic read-verify-write on `issues.json`; auto-recovers stale claims (default >3h old); no-arg call auto-picks the next ready issue and claims it in the same locked op; `--skip <ids>` excludes issues (lightweight parallel-agent support). Pickable = `{todo, blocked}`; `in_review`/`awaiting_approval` issues keep their claim and are NOT auto-picked or stolen (handoff in flight). **Sprint-scoped:** when `sprints.json` exists, auto-pick prefers issues in the `in_progress` sprint and only spills to the next planned sprint when none are ready (`--sprint-scope current` forbids the spill; `all` ignores sprints). No `sprints.json` → unscoped, exactly as before. **Ordering within scope:** sprint tier → priority (issue `priority` override else parent ticket's else medium) → `created_at` (FIFO among manual items; `+inf` for spec-derived, so an all-spec tree is unchanged) → topo ranks → id. So a `high`-priority manual item in the active sprint sorts to the top. Lock releases on process exit.
- `drift_check.py` is a mechanical drift detector — runs as part of adapt mode assessment, optionally before impl agents claim long-running issues.

## Main-repo files & merge protocol

The skill writes four kinds of file into the main repo (everything else goes under `.specseed/`): `README.md`, root `CLAUDE.md`, per-component `CLAUDE.md`, and top-level `AGENTS.md`. `CONTRIBUTING.md` is NOT one of them — its content (folder structure, branching, versioning, release, release gates, conventions) folds into `CLAUDE.md`.

**The `.specseed/spec/` tree is protected separately** (not by this merge protocol): an existing spec tree is caught by the session-start reconnaissance (routes to adapt/plan-next, not bootstrap) and by bootstrap's own collision-guard precondition. This merge protocol governs only the four main-repo entry files below.

**Inform the user** (first message of a repo-mode session, and again at session end) which of these will land in the main repo and that everything else is confined to `.specseed/`.

**First-run `.gitignore` choice.** During bootstrap/adopt, ask at the same moment you explain/write the `.specseed/` tree and main-repo entry files:
> "Add specseed artifacts to `.gitignore`? Default/recommended: **no**. Keeping `.specseed/`, `CLAUDE.md`, and `AGENTS.md` tracked makes the spec and agent contract transfer with the repo. Ignore them only if this repo's specseed setup is private/local."

If user says **yes**, append missing entries to `.gitignore` for `.specseed/`, root `CLAUDE.md`, root `AGENTS.md`, and any selected per-component `CLAUDE.md` paths. If `.gitignore` does not exist, create it. Never add these ignore rules by default. Do not add `README.md` or provider issue-template projections to `.gitignore` unless user explicitly asks.

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

## Analysis-seam scripts (shipped, editable)

`.specseed/scripts/core/requirements_analyze.py`, `tickets_analyze.py`, and `sprint_plan.py` are **shipped by the skill** (stdlib defaults) but are the intended **analysis/scheduling seam** — orgs with their own tooling (a PM system, custom critical-path or scheduling logic) may replace them, as long as the documented I/O contract holds. The mechanical plumbing (assemble/validate/claim/render) is NOT a seam — don't swap it. If a seam script is somehow missing at session start when a stage would call it, fall back to the shipped version (or describe the contract and proceed).

## Carry-forward (post-rewrite)

- ROADMAP `(X/Y complete)` counts: `roadmap_render.py` bumps them in-place from `tickets.json` (`issues_done`/`issues_total`). Runs in the issue finish flow (after `tickets_assemble.py`) and the adapt/tweak cascades; `--check` reports drift without writing. Counts are display-only — `claim_issue.py` derives unblocking from `issues.json`, not ROADMAP.
- ROADMAP `(X/Y complete)` counts: `roadmap_render.py` bumps them in-place from `tickets.json` (`issues_done`/`issues_total`). Runs in the issue finish flow (after `tickets_assemble.py`) and the adapt/tweak cascades; `--check` reports drift without writing. Counts are display-only — `claim_issue.py` derives unblocking from `issues.json`, not ROADMAP.
- [ ] Templates folder for vision/sad/srs/sdd skeletons (deferred)
- [ ] Evals / test cases for the skill itself (deferred)
- [ ] `spec_concern.md` template — for now, format is documented in `templates/CLAUDE_template.md`
