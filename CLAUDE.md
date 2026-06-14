# CLAUDE.md

**Write caveman.** Every doc, comment, commit body, PR, and reply: terse, signal-dense, no
filler. Style ref: `skills/specseed/references_ext/caveman.md`. Spec PROSE
the skill *emits* (vision/SAD/SDD/entity bodies/ADR) ALSO gets the humanizer pass + em-dash ban:
`references_ext/humanizer.md`. Tell spawned agents the same. This rule saves tokens on every edit.
Memory: when asking for feedback/clarifications, use `skills/specseed/references/reply-protocol-base.md`.

## What this repo is

**specseed**: a headless, remote-driven spec+work engine. Two halves:

1. **Runtime** (`src/specseed_runtime/`) - polls a tracker, syncs it, queues work, drains queue,
   runs agents. Stdlib-only python. This is real code that *runs*.
2. **Skill** (`skills/specseed/`) - the non-interactive spec-change worker the runtime invokes
   when a post is labeled `spec-change:<route>`. Markdown instructions.

**The engine is never copied into the target, AND nothing specseed lives in the target (0.21+).**
It runs from this repo against a target repo: `src/specseed configure --target <target_repo>` then
`src/specseed run --target <target_repo>`. The target gets ONLY a git repo (init + root commit if
needed) - no `.specseed/`, no gitignore line. ALL per-repo data lives in the app home under a
single-purpose-split DATA ROOT (`$SPECSEED_HOME/repos/<slug>/`, `registry.data_root_for`), beside the
registry. Subdirs: `db/` (work queue) `tracker/` (local tracker cache) `config/` (configuration.json,
remote.json, token, version marker) `runtime/` (control/runner/seed/sessions) `logs/` (platform.log,
agent-output) `spec/` (live spec) `spec-change/<id>/` (staged spec + plan.json + apply.py)
`instructions/<route>/` (user stubs). `storage_paths.py` is the single seam mapping each file to its
subdir; the data root is the one dir passed everywhere (registry record `data_root`, `storage` is a
back-compat alias). Pre-0.21 in-target `.specseed/` is auto-relocated into the home on first resolve
(`migrating/relocate.py` + the 0.20->0.21 reshape hop). Configure via
`src/specseed_runtime/configuring/configure.py`. (Root `install.py` = a design-notes stub, not code.)

**One engine, many repos.** `src/specseed serve` (or bare `specseed`) launches ONE web UI
(`src/ui/`, vanilla JS, no deps; default port 5050 / `$PORT`) that manages every
registered repo. Runners stay SEPARATE - each repo's `run` is its own process; only the webpage is
shared. The shared index is a global registry at `$SPECSEED_HOME` (default `~/.specseed`),
`registry.py`. **Dev checkout** (`registry.is_dev()`: code under `src/specseed_runtime/`) flips
defaults: home `<repo>/data-dev/`, port 5051, UI shows "specseed DEV" (`/api/env`). `$SPECSEED_HOME`
/ `$PORT` / `--port` still override. CLI parity for headless boxes: `add`/`list`/`start`/`pause`/`resume`/`stop`/`status`.
Lifecycle is out-of-band: CLI/UI write `<storage>/control.json` (desired state); the running
scheduler reconciles it each tick and stamps `<storage>/runner.json` (heartbeat: pid+state+queue),
liveness = pid alive AND heartbeat fresh (`executing/runner_control.py`). Provider (local/github/
gitlab) is picked once per repo and is FINAL. UI tabs per repo: Monitor (queue/errors/log + runner
controls), Tracker (local only; github/gitlab = externally-managed link), Configuration (gated:
must pause/stop the runner first).

**`old_specseed/` = dead.** Old, badly-working interactive version. Ignore it. Do NOT copy its
patterns or follow its instructions. Only mined in rare occasions for features/processes that were present there for aligning with request if it makes sense.

## Core mental model

**Remote is source of truth. Local is our copy. We never act on our own state alone - we sync a
remote in and react to the diff.**

```
remote (TrackingRemoteGitHub/GitLab/Local)  --sync_from_remote-->  TrackingLocal
        truth                                  list[TrackingSyncChange]
                                                      |
                                       sync_to_db: change -> typed task
                                                      v
                                          DB queue (db/database.py)
                                                      |
                                  Scheduler poll->sync->drain (own thread)
                                                      v
                                  dispatch: judge state+perms in code, run agent
```

The **skill worker** is the other direction: edits local `spec/`, emits an `apply.py` that mutates
remote posts, enqueues it. Never runs it itself, never touches git/code.

## Layout

```
src/
  specseed                           # bash shim: picks python3/python and runs runtime CLI
  specseed_runtime/                  # the RUNTIME (stdlib-only python)
    specseed.py                      # command router: serve/add/list/start/pause/resume/stop/status + configure/run
    registry.py                      #   global multi-repo index at $SPECSEED_HOME (~/.specseed/registry.json). provider FINAL per repo
    tracking/                        #   provider-neutral tracker layer. README inside. entry=neutral resource
    scheduling/                      #   remote diff -> DB queue (sync_to_db) + spec_change enqueue. README inside
    db/database.py                   #   durable sqlite work queue (tasks + task_errors; not_before = scheduled retries). thread-safe singleton
    tasks/                           #   one typed task class per change kind (handle_*) + task_base + cleanup
    platform_identity.py             #   who the platform is on the tracker: platform_username + "specseed: " comment prefix (self-retrigger guard)
    executing/                       #   scheduler(poll loop) + dispatch + advance + agent_runner + control + permissions + runner_control(control.json/runner.json) + recovery(retries + platform_error posts) + inflight(orphan reclaim)
    entities/                        #   epic/ticket/issue = meaning over neutral entries (tier/status/links). EntityRef
    state_machines/                  #   legal status transitions + approvals. 0.15.0: a gate's 👍/👎/❤️ counts on its REQUEST COMMENT (latest platform comment w/ the approval-request marker), NOT the post — `_gate_reaction_users`; post reactions only when no request comment (post body is the ask). approve/reject/merge cmds still scoped to live APR/post id
    configuring/                     #   configure.py interactive setup -> config
    migrating/                       #   storage migrations (hops); 0.3.1->0.4.0 deletes copied code; 0.4.0->0.5.0 + 0.5.0->0.7.0 drop the seed marker so new labels re-seed; 0.5.0->0.7.0 also adds tasks.not_before; 0.11.0->0.12.0 renames dev_branch->specseed_primary_branch (+ merge_to_primary/push_primary); 0.12.0->0.13.0 drops the dead permissions.remote.make_prs switch; 0.14.0->0.16.0 drops the seed marker so the new `awaiting_merge` status re-seeds; 0.20.0->0.21.0 reshapes the relocated flat data root into single-purpose subdirs (db/tracker/config/runtime/logs + instructions/<route>/) — the in-target->home move itself is `migrating/relocate.py`
  ui/                  # the SHARED web UI (vanilla JS modules, no deps): server.py (multi-repo API) + shell/ + features/{repos,monitor,tracker,configuration} + theme.css
skills/specseed/                     # the spec-change worker skill (markdown + helper scripts), at repo root
  SKILL.md                           #   START HERE. router: routes, contract, hard rules
  routes/                            #   adopt/adapt/tweak/inject/plan-next-sprint
  references/                        #   spec-change-protocol, work-breakdown, remote-posts, component-questions, reply-protocol, chat-mode
  references_ext/                    #   caveman.md (density) + humanizer.md (naturalness)
  scripts/                           #   stdlib helpers over local spec/ + plan.json ONLY: requirements_generate_json, requirements_analyze, critical_path, sprint_pack
  templates/entity_templates/        #   epic/ticket/issue/bug/feature emitted into target
data-dev/repos/<slug>/               # per-repo DATA ROOT in dev ($SPECSEED_HOME=data-dev); single-purpose subdirs (db/tracker/config/runtime/logs/spec/spec-change/instructions)
tests/unit/python/                   # default test suite (units)
tests/integration/python/            # opt-in integration tests (marker: integration)
```

## Key components

- **tracking/** - `TrackingBase` (ABC) defines the contract + data shapes (`TrackingResult` envelope,
  `TrackingEntrySummary`, `TrackingSyncChange`). `TrackingLocal`=sqlite copy. `TrackingRemote*`=sources
  of truth (`Local`=no-net stand-in, `GitHub`/`GitLab`=real). `resolve_local()`/`resolve_remote()` pick.
- **scheduling/sync_to_db.py** - only place holding reaction policy: maps each change->task, supersedes
  pending tasks by resource identity, tears down (interrupt+cleanup) on close/delete.
- **executing/scheduler.py** - daemon-thread poll->sync->drain loop. RUNNING/PAUSED/STOPPED, driven by
  operator commands on the CONTROL post. Each task runs on a worker thread w/ wall-clock backstop +
  cooperative cancel (`cancellation` registry).
- **executing/dispatch.py** - switches on `task["action"]`: `run_spec_change_script` (gated subprocess
  runs `apply.py`) vs work handlers (read entity, judge state+perms in code, build prompt, run agent).
- **db/database.py** - NOT a mirror; a queue of work derived from sync diffs. WAL + `BEGIN IMMEDIATE`
  claim so two workers never grab one task. Pure accessors, no policy.
- **agent context model** (`executing/prompts.py` + `executing/agent_sessions.py`) - INFO CONTROL by
  construction (0.21). Data is no longer in the agent's cwd, so each `build_*_prompt` states, as
  ABSOLUTE paths, ONLY the data dirs that route may touch (impl/review/ask: `spec/` + `instructions/<route>/`,
  NO tracker/config/db; spec route alone also gets `tracker/` to plan over the entity tree;
  platform-error gets the whole data root to diagnose). The post body + comment thread are INJECTED into
  the prompt (`_thread_block`, fed by `(entity, conversation)` from `context.load_entity`) - agents never
  read a db for messages. Permissions are restated inline every run (`render_action_gates`).
  **Continuity:** each run records the provider session id per post (`agent_sessions`, `runtime/sessions.json`);
  the next turn passes `--resume <id>` (claude) so the agent keeps its own memory, falling back to the
  injected thread when there is no id (first turn / codex). `RunnerChains` resumes only the PRIMARY spec.
- **labels** (`tracking/supported_values.py` + `populate_defaults.py`) - tier + status, plus
  `type:<feature|bug|chore|spike|qa>` and `difficulty:<easy|hard>` on work posts. Seeded on startup;
  existing targets re-seed via the 0.5.0 migration (drops the seed marker).
- **failure recovery** (`executing/recovery.py`, wired in `scheduler._recover`) - a retryable failure
  requeues with backoff (1'/5'/15'..., `tasks.not_before`, cap `config.recovery.max_retries`) AND
  becomes remote state: one `platform_error` post per task (runtime-created, marker-linked), each retry
  outcome auto-commented, recovered -> closed. Human closes the post = retries cancel. The
  `resolve_platform_errors` runner chain investigates + converses on the post (engaged on creation /
  exhaustion / human reply; thread-as-memory, no provider sessions). Never recovers itself. All
  platform comments carry the `specseed: ` prefix (`platform_identity.py`); `sync_to_db` drops
  platform-own comments so the bot never re-triggers on its own words (`platform_username` config).
  `executing/inflight.py` ledgers child pids; startup kills orphans + requeues stranded `in_progress`.
- **plan-first approval / settle-on-approval** (`executing/dispatch.propose_spec_change` +
  `advance.resolve_spec_change_request`, wired in `dispatch._run_work`) - NOTHING is created on the tracker
  before approval, and the GATE DECISION IS CODE-OWNED, not the agent's (0.17.0). The worker writes
  `plan.json` + `apply.py` + STAGED spec, then STOPS - it never enqueues and never picks whether the run
  gates. After the run `dispatch._classify_spec_change`/`_enqueue_spec_change_followup` read the OUTPUT and
  decide: a run that creates work or touches spec (staged spec file, `plan.json` creates/settle_docs/closes/
  deletes, or an edit/label/comment aimed at another post) PROPOSES; only a pure clarification round (touches
  just the request post) applies directly. A proposal enqueues `propose_spec_change` (`scheduling/spec_change`):
  the runtime posts `plan.json.plan_summary` + the `APR-NNNN` request and parks the REQUEST
  `spec-change:status:awaiting_approval` (creates no posts, live spec untouched). On 👍/`approve` the runtime
  PROMOTES the staged spec (`advance._promote_staged_spec`: copies `storage/spec-change/<id>/spec/` into live
  `spec/`) THEN stamps `settled: true`+`settled_at` on `plan.json.settle_docs`, moves the request to `done`,
  and ENQUEUES the worker's deferred `apply.py` (`run_spec_change_script`, tagged `close_request`) which now
  creates the epics/tickets/issues (issues born `todo`); the RUNTIME closes the request in code after that
  apply succeeds (`dispatch._close_finalized_request`), NOT the agent's `plan.json.closes` (that list is only
  for OTHER posts a change retires). A spec-only run with nothing to apply closes in resolve.
  Reject -> closed, nothing promoted or created. Deterministic, no agent. The skill never writes `settled`;
  adapt is the only route that reopens a settled doc. **Spec is STAGED, never edited live** - an unapproved or
  buggy run can't corrupt `spec/` (matters now that spec is out of git). Helpers in `scheduling/spec_change.py`:
  `spec_change_spec_dir`/`staged_spec_files`.
- **merge gate** (`advance.WorkTransition{prepare,merge}` + `_settle_or_merge`/`open_merge_gate_ready`, run by
    `dispatch._run_gate_action`/`_prepare_and_gate`/`_execute_merge`) - advance owns remote STATE + decides whether a
    finished issue READIES/merges NOW; dispatch owns git + runs it. An issue is NOT done until its code is on primary,
    so an unmerged issue never reads done/closed. `merge_to_primary` ON -> `WorkTransition(merge=True)`, dispatch merges.
    OFF (default) -> `WorkTransition(prepare=True)`: a GATE only opens once the branch READIES clean. **Readiness-first
    (0.15.0):** dispatch `prepare_merge` brings primary INTO the branch first; a conflict runs the **merge-conflicts
    agent** (`AgentIntent.MERGE_CONFLICTS` -> `merge_conflicts` chain); if it can't be readied -> park `blocked`, no
    gate (the human never approves a merge that can't run). So the approve->checkout-fails->reopen LOOP is gone. **Approval
    is bound to the LIVE gate COMMENT, not the post:** `advance._gate_signals` reads reactions ON the latest
    `MERGE_GATE_MARKER`/`WORK_MERGE_GATE_MARKER` comment (+ `approve/merge APR-<id>` naming that comment's minted APR).
    A re-opened gate is a NEW comment with no reactions, so a standing 👍 / a superseded gate's approval is dead - the
    consume mechanism, no extra storage. PURE gate (work accepted) - 👍/❤️ on the comment re-readies + merges, 👎 declines
    -> `awaiting_merge` (**0.16.0:** NOT done; nothing is done until on primary - the branch is left for a manual merge,
    the issue stays open, dependents stay held). An `awaiting_merge` issue resolves via `advance.resolve_awaiting_merge`:
    👍/❤️/`approve`/`merge` re-readies + merges (idempotent, so it also just confirms a hand-merge) -> done; prose/`retry`
    reworks. COMBINED gate (review under the bar AND merge gated) - ❤️/`merge`
    approves work + readies + merges; 👍/`approve` approves work + opens a follow-up pure merge gate; 👎/prose reworks.
    Plain HITL/below-bar gates keep POST-reaction approval (`_resolve_plain_gate`). **Invalidate-on-primary-change:** after
    a successful merge `_reprepare_after_primary_change` re-readies every OTHER open merge gate against the new primary
    (new gate comment = fresh APR, conflict -> merge-conflicts agent, can't ready -> blocked). `resolve_blocked` clears a
    block ONLY on a fresh explicit `approve/merge <id>` COMMENT (never a durable post reaction). UI gate buttons react on
    the request comment (`data-gate-react`/`reactComment`) for EVERY gate. A comment reaction syncs keyed on the COMMENT,
    so `dispatch._run_work` maps it to the owning entry via `TrackingLocal.entry_id_for_comment` before resolving.
    specseed does NOT open PRs/MRs yet; the issue-branch merge is the unit.
- **dependency gate** (`dispatch._classify_deps`, run at the IMPLEMENT gate in `_run_work`) - an issue may only implement
    once EVERY dep it declares is `done`, and `done` means MERGED to primary (`advance.close_issue_done` runs only after a
    real merge), so this is the strict merged-to-primary rule judged in code, never the agent's call. Deps are body links
    (`Depends on: #NN`, two tiers: the issue's own + its parent ticket's). A dep still in flight -> HOLD (requeue each poll).
    A dep terminally CANCELLED (`wont_do`/`deprecated`, its code will never land) -> the dependent parks `blocked` and one
    draft `spec-change:adapt` per cancelled dep (idempotent via a `dep-cancelled #NN` marker) lists every blocked dependent
    so a human triages (rework / continue / cancel) instead of deadlocking forever (`advance.block_on_cancelled_dep`).

## Testing (enforced)

- **stdlib only. No third-party deps anywhere.** No PyYAML etc.
- **Every script change ships test changes in the same commit.** Hard rule, applies to you too.
- Units live in `tests/unit/python/`. Run: `python3 -m pytest` (configured to units only).
- **Integration tests live in `tests/integration/python/`, marked `@pytest.mark.integration`** - like
  old_specseed did. Opt-in: `python3 -m pytest tests/integration/python/`. Add them when a new script
  flow would otherwise only be checked by hand. Keep fast, isolated under `tmp_path`/`/tmp`, clean up.
- See `tests/integration/python/CLAUDE.md` before adding integration coverage; focus remote-local
  state changes, queue side effects, teardown, supersession, and reaction/comment/label churn.
- **No test hits real GitHub/GitLab.** `TrackingRemoteLocal` is the authoritative stand-in. (CLAUDE rule:
  no tests involving actual remotes in `tests/*/python`.)
- Tests MUST NOT invoke an agent or consume tokens.

## Versioning, storage & migrations

- Version: `version.txt` (repo root) + `skills/specseed/version.txt`. Same value,
  bump BOTH. Format `X.Y.Z`. This is the engine's running code version (read from
  `skills/specseed/version.txt` in the engine repo); the data root stores a `config/version.txt` marker.
- Only user bumps `X`. Bump `Y` for anything that breaks without a migration - the proverbial API:
  storage layout, db schema, config keys, script CLI/function contracts. Bump `Z` for normal changes;
  skip only for same-change follow-up.
- **ALL generated runtime data lives in the home DATA ROOT, split into single-purpose subdirs**
  (`db/ tracker/ config/ runtime/ logs/`). `storage_paths.py` is the seam that maps every file to its
  subdir; new data files route through it (never build paths by hand). The data root is in the app
  home, NEVER in the target.
- `config/version.txt` = what version last shaped the data root (pre-0.21 marker was flat at the root;
  `migrate.storage_version` falls back there). Code version vs marker diff drives migrations. Pre-0.3.0
  unsupported (missing marker = 0.3.0).
- Y/X bump that touches storage shape -> author a hop `specseed_runtime/migrating/m_<from>__<to>.py`
  (`FROM`/`TO` consts + `run(storage, specseed_dir)`, where `storage` = the data root), append to
  `MIGRATIONS` in `migrating/migrate.py`. One hop spans consecutive migration-bearing versions; hops
  chain, run one by one, never restate older hops. Hops touch ONLY the data root (`storage`) - never the
  target; target-side cleanup (relocation, gitignore strip, old router blocks) lives in
  `migrating/relocate.py`, which runs once at resolve time.
- Migrations idempotent: safe twice, preserve user-custom values, never clobber an existing dest,
  only rewrite old/default-shaped data. May delete old files when clearly superseded.
- Entrypoints that migrate-before-read: `executing/run.py` (startup, via the `src/specseed`
  launcher), `configuring/configure.py` (main). 0.20->0.21 relocates pre-0.21 in-target `.specseed/`
  into the home + reshapes flat storage into the single-purpose subdirs.
- Hop tests: old-shape fixture -> `run_migrations()` -> assert upgraded files + marker; run twice for
  idempotency. Copy the pattern in `tests/unit/python/test_migrating.py`. Verify each entrypoint
  triggers.

## Conventions

- Every tracking op returns `TrackingResult(ok, error, data)` - check `ok`, fail loud. Never raise across
  the interface, never reach around the tracker to mutate a remote.
- Entry ids are `int | str` (GitHub ints, GitLab iids) - store as text.
- GitHub can't hard-delete issues: use `set_entry_closed`, not `delete_entry` (fine on local/GitLab).
- Bare imports + `sys.path` wiring at import time - match surrounding files.
- Each `scheduling/` and `tracking/` dir has its own README. Read it before editing there.

## Git rules

- Session start: check uncommitted changes. If any, stop + tell user.
- Switch to `dev` branch or confirm there. `dev` = primary working branch. Never touch main.
- Worktrees OK (claude? → `.claude/`; codex? → `.codex/`).
- Each request gets own branch.
- Whenever changes happen and you must stop: commit + push to your created branch. Don't ask. Commit + push regularly. Don't wait for very end. One commit per logical point. Not spammy (no per-file).
- Make PR for it. PRs always target `dev`.
- One request branch per user-initiated session. No multiple branches/PRs same session, even if feels like they should split — unless user asks.
- After commit + push: fetch, check if `dev` ahead.
- If `dev` ahead: try bring it in. If clean or minor issues you can confidently fix → fix + surface.
- If hard: don't bring `dev` in. Put in PR message + surface.
- PR message stays concise:
  - Description: 1-5 sentences (scale to work size). Goal, why needed.
  - Key Changes: 1-5 bullets. Major changes only.
  - How to test: step-by-step verify instructions, especially from greenfield target-repo view if possible. Concise — not 100 lines. Aim 1-15, flexible higher for big changes.
  - Screenshots: if applicable.
  - Risks: what could break? 0-3 bullets typical.
  - Deployment notes: ~1-4 lines, if needed.
  - Checklist: high-level 1-10 bullets. Steps done = checked, pending = unchecked.


## dev machine

- If the machine you are in is called 'dev' and I tell you to restart something (from 'claude'/'claude-personal', 'codex', 'specseed installed', 'specseed dev') you go in the logs (/workspace/logs/) find the relevant pid. kill -9 that pid. It has to be explicit, not 'I think that's what he wants'.
- 'Create a local env for me to play with' or wording like that, means running `python3 qa/local_greenfield/prepare.py --option claude-personal-haiku` (default, other options are claude-work-haiku and codex-gpt-5.4-mini)