---
name: specseed
description: Documentation-driven development spec creation skill. Use whenever user wants to create, draft, update, or revise software specification artifacts — vision, SRS (Software Requirements Spec), SAD (Software Architecture Doc), ADRs, SDD, requirements JSON, tickets JSON — for greenfield projects or adapting existing codebases. Trigger on `/specseed` slash command and on phrases like "spec out", "draft requirements", "plan this project", "update the SRS", "add a requirement", "generate tickets", "design doc", "what should we build". Also trigger when user starts a new software project and discusses scope, requirements, or architecture without naming a doc — they likely need this.
---

# specseed

Skill for producing and maintaining software specification artifacts. Routes by mode. Produces a `spec/` tree (repo) or chat artifacts.

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
- `references/ticket-formation.md` — INVEST, vertical slices, critical path, spike post-completion, sizing heuristics

## Memory protocol

Session scratch at `spec/memory.md` (repo) or chat artifact (no-repo). Holds:
- Current workflow stage + sub-step
- Per-component summaries when components done
- Locked decisions not yet on disk
- State needed to survive a context compression or `/specseed stop`

**Cadence — revision-gated:**
- After each round: write ONLY IF round had revisions/pivots. Pure-OK rounds skip (matches no-noise rule from `question-protocol.md`)
- At every stage boundary: always write a stage rollup

**Compression hooks:** at natural semantic checkpoints — after each per-component stage done, after settle, after critical-path review — skill *proposes* "consider compress now?" and waits. Does not auto-compress.

**After any context compression: reread `SKILL.md`, then `memory.md` to resume.** Bake this into the last line of `memory.md`: `On resume: reread SKILL.md first, then this file.`

**Delete `memory.md` at session end** (or move salient bits to a changelog if user asks). On `/specseed stop` mid-session, KEEP `memory.md` intact so resume works on next invocation.

## Output destination

Detect at session start:

**Repo mode (any agent harness with writeable filesystem):**
- Write all files to disk under the canonical hierarchy below
- After any script-touched edit (reqs.json regen, tickets.json change, etc), agent runs the relevant analyzer/validator script itself
- Tell user what was written, not the file contents

**Chat mode (no repo / no filesystem):**
- Deliver files as chat artifacts
- **Bootstrap:** progressive delivery — each major artifact handed off as its stage completes (vision after stage 2, per-component srs after stage 5, sad after stage 6, etc). At session end, deliver a *manifest* artifact listing every file with its canonical path
- **Adapt:** only the changed files as artifacts + a diff summary in chat
- **Tweak:** only the changed file(s). Nothing else
- After any edit that would trigger a script (reqs.json regen, validation), **remind** the user with copy-paste-ready commands. Do not assume the user has scripts wired
- **Naming:** deliver each file with its full canonical path as its identifier (e.g. `spec/api-srs.md`, not bare `srs.md`) so multiple files of the same type don't collide visually in the chat history

## Output hierarchy (canonical)

```
README.md                       # user-facing, normal English, brief, anti-fluff
CLAUDE.md                       # agent runtime entry — write from references/CLAUDE_template.md
AGENTS.md                       # one line: "Read ./CLAUDE.md. In dirs you work on, read corresponding CLAUDE.md too."
docs/
└── CONTRIBUTING.md             # folder structure, branching, versioning, release artifacts, release gates
spec/
├── vision.md
├── sad.md
├── adr.csv                     # columns: Decision,Justification
├── srs.md                      # OR per-component (<component>-srs.md)
├── sdd.md                      # OR per-component (<component>-sdd.md)
├── cross-cutting-srs.md        # OPTIONAL — virtual component for cross-cutting concerns
├── reqs.json                   # generated from SRS tables
├── tickets.json                # source of truth (NOT generated from md)
├── memory.md                   # session scratch — deleted at end; preserved on /specseed stop
├── milestones.md               # OPTIONAL — only if user asks
├── deployment.md               # OPTIONAL — only if Operations theme flagged in component questioning
├── ticket_tracking/
│   └── <ticket_id>/
│       ├── plan.md
│       ├── spec_concern.md     # OPTIONAL — written by impl agent if settled doc looks wrong mid-ticket
│       └── step_reports/
│           └── <X>_<step>_<desc>.md
└── scripts/
    ├── requirements_generate_json.py   # parses SRS table rows → reqs.json
    ├── requirements_analyze.py         # USER-PROVIDED — cycle/orphan detection
    ├── tickets_validate.py     # schema + ID uniqueness + dangling-ref checks
    ├── tickets_analyze.py      # USER-PROVIDED — critical path, build order
    ├── ticket_info.py          # ticket info + linked reqs lookup
    ├── claim_ticket.py         # atomic flock-based claim + stale-claim recovery
    ├── verification_map.py     # inverse map: reqs → tickets → test files
    └── drift_check.py          # mechanical spec-vs-reality drift surface
```

Notes:
- **No `test_plan.md`.** Verification traceability lives in ticket `artifacts.tests` field; `verification_map.py` produces the inverse map (req → tests) on demand. SRS does NOT carry a `verified_by` column — that data would duplicate and drift.
- `milestones.md` and `deployment.md` are optional and created only when explicitly relevant.
- `cross-cutting-srs.md` is optional — bootstrap proposes it only when context indicates cross-cutting concerns materially matter (security, observability, i18n, accessibility). Treated as a virtual component by all machinery (ID prefix `SRS-CC-NNN`, tickets, analyzers).
- `tickets.json` is source of truth (no source markdown). Skill writes it directly during bootstrap and updates it in adapt/tweak.
- `reqs.json` IS generated from SRS markdown tables (humans edit SRS, script extracts).
- `spec_concern.md` is written by the **implementation agent** (not this skill) when it discovers a settled doc looks wrong during ticket execution. Adapt mode picks these up as valid triggers — see `references/adapt.md` stage 2.
- `claim_ticket.py` replaces the raw `jq` claim one-liner in `CLAUDE.md`. Uses `fcntl.flock` for atomic read-verify-write; auto-recovers stale claims (default >3h old). Lock acquisition has a short timeout (default 10s) so a stuck/dead process can't block claims indefinitely. Lock itself releases on process exit.
- `drift_check.py` is a mechanical drift detector — runs as part of adapt mode assessment, optionally before impl agents claim long-running tickets.

## Settled vs editable

**Contract, not enforcement.** Settled is a soft-frozen marker honored by agents using this skill's `CLAUDE.md` template. The filesystem does not block edits. Other agents, scripts, or humans can edit settled docs freely — and doing so desyncs traceability (adapt mode assumes settled = stable). If your runtime doesn't follow the contract, the safety guarantees here don't apply.

After srs/sdd/sad/vision are **settled** (soft-frozen with user approval), the *implementation* agent (running via `CLAUDE.md` to pick up tickets) MUST NOT edit them. Only this skill can revise them, via adapt mode.

If the implementation agent finds a settled doc looks wrong mid-ticket, it MUST:
1. Mark its current ticket `status: "blocked"`
2. Write `spec/ticket_tracking/<ticket_id>/spec_concern.md` describing what's wrong, why, and what it would change
3. Tell the user: **"Use `/specseed adapt` to address spec concern: spec/ticket_tracking/<id>/spec_concern.md"**

The implementation agent does NOT edit the settled doc itself, ever. Adapt mode is the only path through. When adapt mode reopens a settled doc, it logs to `adr.csv`.

## User-provided scripts

`spec/scripts/requirements_analyze.py` and `spec/scripts/tickets_analyze.py` are USER-PROVIDED — existing tools the user has. If not present at session start when a flow stage would call them, skill asks the user to provide them or describe their interface before proceeding.

## Carry-forward (post-rewrite)

- [ ] Implementation bodies for `requirements_generate_json.py`, `tickets_validate.py`, `ticket_info.py`, `drift_check.py`, `claim_ticket.py`, `verification_map.py` (currently spec-only stubs)
- [ ] User-provided analyzers (`requirements_analyze.py`, `tickets_analyze.py`) — pending receipt. User to remove `requirements_analyze.py` check 4 (verification gaps) since `verified_by` no longer in reqs.json.
- [ ] Templates folder for vision/sad/srs/sdd skeletons (deferred)
- [ ] Evals / test cases for the skill itself (deferred)
- [ ] `spec_concern.md` template — for now, format is documented in `references/CLAUDE_template.md`
