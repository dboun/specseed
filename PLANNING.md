# PLANNING.md

Living planning ledger for specseed. Keep terse. Update on `dev` first.

## How To Use

- When user gives ideas or direction, add them here on `dev`.
- If currently on another branch: record current branch and dirty state, update `dev`, then return.
- Merge or rebase `dev` back into active work when the planning change should travel with it.
- Keep `Current Work` fresh so "what are we doing?" has one obvious source.
- Promote scratch items into Backlog with priority, status, and next action.

## Current Work

- Active: spec-change rejection safety. Status: implemented JSON plan executor 2026-07-28; tests passed.
- Repo restart audit. Status: done 2026-07-27. Unit tests passed on `dev`.
- Branch cleanup decision. Status: pending. Need decide whether local `dev` stays private, gets pushed, or tracks rewritten `origin/main`.
- Next likely work: session id continuity audit.

## Repo Status Snapshot

Date: 2026-07-27.

- Current checkout when audited: `dashboard-post-links`.
- `dashboard-post-links` is already merged into local `dev`.
- `assignees-and-interval` is already merged into local `dev`.
- No local branch has commits missing from local `dev`.
- Remote after fetch: only `origin/main` exists. Old `origin/dev`, `origin/dashboard-post-links`, and `origin/assignees-and-interval` are gone.
- `origin/main` was force-updated and appears to be a release/simplified repo history. Do not merge it into `dev` blindly.
- Local `dev` is checked out in `.claude/worktrees/bridge-cse_017gT52daZcq6UPSC2mUWGKZ`; lock PID was stale during audit.

## Design Notes

### Spec-Change Apply Model

Current model:

- LLM writes staged spec files, `plan.json`, and `apply.py` under `storage/spec-change/<id>/`.
- Runtime reads outputs and decides gate in code.
- Human approval promotes staged spec into live `spec/`.
- Runtime then queues generated `apply.py` only if remote changes are needed.
- `apply.py` mutates tracker posts through the tracking contract. It must be idempotent.

Question:

- Should LLMs stop generating `apply.py` and emit only JSON, with one fixed runtime executor applying `plan.json`?

Likely direction:

- Prefer fixed executor if `plan.json` can cover creates, edits, labels, comments, closes, deletes, and id substitution.
- Keep generated `apply.py` only for cases JSON cannot express cleanly.
- This would reduce code-exec surface and duplicate-script risk, but needs migration path and tests over local/GitHub/GitLab tracker semantics.

### Plan-First Safety

Planning decisions:

- Pre-approval safety target: no mutation outside the request post. Locked 2026-07-28.
- Apply model: fixed JSON executor for normal ops, generated script escape hatch. Locked 2026-07-28.
- Done bar: unit tests plus local lifecycle integration. Locked 2026-07-28.
- Clarification path: runtime posts clarification from JSON; avoid generated code. Locked 2026-07-28.

Current state looks mostly fixed:

- Spec writes are staged under `storage/spec-change/<id>/spec/`, not live `spec/`.
- Work posts are not created before approval.
- Runtime owns approval decision and staged-spec promotion.
- Reject closes request with no spec promotion and no post creation.

Need verify:

- Clarification-only direct apply cannot mutate non-request posts.
- Generated `apply.py` cannot escape intended tracker operations beyond tracking contract use.
- Failed or retried `apply.py` cannot duplicate creates.

## Backlog

### High Priority

#### Compliant SRS done, do rest

Status: needs clarification.

Next:

- Inspect current spec templates and SRS coverage.
- Identify remaining docs: likely SAD, SDD, entity templates, ADR, verification map.

#### Spec-change rejection safety

Status: partly done, verify.

Intent:

- Spec-change workers operate in staged temp dirs under storage.
- Only code-owned approval promotes into live spec and creates tracker posts.

Next:

- Audit classification and direct-apply path in `dispatch.py`.
- Add regression tests if missing.

### Medium Priority

#### Session id continuity

Status: important, verify.

Intent:

- If an agent run started and later resumes after interruption or rate limit, reuse same provider session id.
- If run failed before provider session existed, do not invent one.

Next:

- Audit `src/specseed_runtime/executing/agent_sessions.py` and runner adapters.
- Test interrupted/resumed run behavior.

#### Auto-fetch issue branches

Status: design needed.

Intent:

- Fetch issue branches automatically.
- Maybe pull/adapt user issue branches like `spec-change:*`.
- Handle users merging manually.

Next:

- Define branch ownership rules.
- Decide when to fast-forward, merge, or leave human changes alone.

### Low Priority

#### Support Gemini CLI

Status: idea.

#### Support OpenCode

Status: idea.

#### Support OpenRouter

Status: idea.

Scope:

- OpenRouter API + key support for Claude Code, Codex, OpenCode.
- Not Gemini CLI.

#### Support `:/` reaction

Status: idea.

Intent:

- Add reaction support for GitHub/GitLab/local UI.
- No behavior yet.

Later:

- Maybe drive issue splitting from this reaction.
- Keep split behavior separate and gated.

#### Issue splitting with emoji

Status: idea.

Need:

- Verify current issue implementation gate.
- Decide trigger semantics and human approval.

#### Agent handcrafted skills

Status: idea.

Note:

- No custom-skill system needed yet; agents can already add skills.
- Useful examples may include frontend-design.

#### Licensing and editions

Status: needs split.

Ideas:

- Community self-host.
- Enterprise self-host with token/phone-home.
- Pro edition.
- Show edition tag near DEV tag in top-left UI.

#### Easy and hard implementation models

Status: design needed.

Intent:

- Split implementation agent config into "Implementation Easy" and "Implementation Hard".
- Add selector above agent runners:
  - One option for all functions.
  - Separate selection.
- Config stays normalized as separate per-function values.
- UI infers "one option" mode whenever every function uses same model.

#### Interface page

Status: idea.

Intent:

- Page with artifacts, link to dev, screenshots, and related outputs.
