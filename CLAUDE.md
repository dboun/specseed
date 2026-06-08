# CLAUDE.md

**Write caveman.** Every doc, comment, commit body, PR, and reply: terse, signal-dense, no
filler. Style ref: `skills/specseed/references_ext/caveman.md`. Spec PROSE
the skill *emits* (vision/SAD/SDD/entity bodies/ADR) ALSO gets the humanizer pass + em-dash ban:
`references_ext/humanizer.md`. Tell spawned agents the same. This rule saves tokens on every edit.
Memory: when asking for feedback/clarifications, use `skills/specseed/references/question-protocol.md`.

## What this repo is

**specseed**: a headless, remote-driven spec+work engine. Two halves:

1. **Runtime** (`src/specseed_runtime/`) - polls a tracker, syncs it, queues work, drains queue,
   runs agents. Stdlib-only python. This is real code that *runs*.
2. **Skill** (`skills/specseed/`) - the non-interactive spec-change worker the runtime invokes
   when a post is labeled `spec-change:<route>`. Markdown instructions.

**The engine is never copied into the target.** It runs from this repo against a target repo:
`src/specseed configure --target <target_repo>` then `src/specseed run --target <target_repo>`
(`specseed_dir` default `.specseed`). The
target gets ONLY data - `<specseed_dir>/storage/` (dbs, config, logs, version marker) and the
generated `<specseed_dir>/spec/`. In THIS dev repo the target is this repo itself, so storage/spec
land at the repo root (gitignored). Configure a target via `src/specseed_runtime/configuring/configure.py`.
(Root `install.py` = just a design-notes stub for a future system-wide install, not code.)

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
    migrating/                       #   storage migrations (hops); 0.3.1->0.4.0 deletes copied code; 0.4.0->0.5.0 + 0.5.0->0.7.0 drop the seed marker so new labels re-seed; 0.5.0->0.7.0 also adds tasks.not_before; 0.11.0->0.12.0 renames dev_branch->specseed_primary_branch (+ merge_to_primary/push_primary); 0.12.0->0.13.0 drops the dead permissions.remote.make_prs switch; 0.14.0->0.16.0 drops the seed marker so the new `awaiting_merge` status re-seeds
  ui/                  # the SHARED web UI (vanilla JS modules, no deps): server.py (multi-repo API) + shell/ + features/{repos,monitor,tracker,configuration} + theme.css
skills/specseed/                     # the spec-change worker skill (markdown + helper scripts), at repo root
  SKILL.md                           #   START HERE. router: routes, contract, hard rules
  routes/                            #   adopt/adapt/tweak/inject/plan-next-sprint
  references/                        #   spec-change-protocol, work-breakdown, remote-posts, component-questions, question-protocol, chat-mode
  references_ext/                    #   caveman.md (density) + humanizer.md (naturalness)
  scripts/                           #   stdlib helpers over local spec/ + plan.json ONLY: requirements_generate_json, requirements_analyze, critical_path, sprint_pack
  templates/entity_templates/        #   epic/ticket/issue/bug/feature emitted into target
storage/                             # dev runtime data (gitignored); a target's lives at <specseed_dir>/storage/
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
  before approval. A work-creating / spec-settling run enqueues `propose_spec_change` (`scheduling/spec_change`);
  the runtime posts `plan.json.plan_summary` + the `APR-NNNN` request and parks the REQUEST
  `spec-change:status:awaiting_approval` (creates no posts). On 👍/`approve` the runtime stamps
  `settled: true`+`settled_at` on `plan.json.settle_docs`, moves the request to `done`, and ENQUEUES the worker's
  deferred `apply.py` (`run_spec_change_script`, tagged `close_request`) which now creates the
  epics/tickets/issues (issues born `todo`); the RUNTIME closes the request in code after that apply
  succeeds (`dispatch._close_finalized_request`), NOT the agent's `plan.json.closes` (that list is only
  for OTHER posts a change retires). A spec-only run with nothing to apply closes in resolve.
  Reject -> closed, nothing created. Deterministic, no agent. The skill never writes `settled`; adapt is the only
  route that reopens a settled doc. A mechanical run (clarification round, sprint label shuffle) skips propose and
  enqueues `run_spec_change_script` directly.
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
  `skills/specseed/version.txt` in the engine repo); the target only stores a `storage/version.txt` marker.
- Only user bumps `X`. Bump `Y` for anything that breaks without a migration - the proverbial API:
  storage layout, db schema, config keys, script CLI/function contracts. Bump `Z` for normal changes;
  skip only for same-change follow-up.
- **ALL generated runtime data lives flat in `<specseed_dir>/storage/`** (dbs, configuration.json,
  remote.json, token, logs, version.txt marker). Never module-adjacent - the engine isn't in the
  target, so anything not under `<specseed_dir>/` is lost. Default paths come from
  `specseed_runtime/storage_paths.py`; new data files route through it.
- `storage/version.txt` = what version last shaped storage. Code version vs marker diff drives
  migrations. Pre-0.3.0 storage unsupported (missing marker = 0.3.0).
- Y/X bump that touches storage shape -> author a hop `specseed_runtime/migrating/m_<from>__<to>.py`
  (`FROM`/`TO` consts + `run(storage, specseed_dir)`), append to `MIGRATIONS` in `migrating/migrate.py`.
  One hop spans consecutive migration-bearing versions only; hops chain, run one by one, never restate
  older hops.
- Migrations idempotent: safe twice, preserve user-custom values, never clobber an existing dest,
  only rewrite old/default-shaped data. May delete old files when clearly superseded.
- Entrypoints that migrate-before-read: `executing/run.py` (startup, via the `src/specseed`
  launcher), `configuring/configure.py` (main). The 0.3.1->0.4.0 hop deletes any engine code an old
  installer copied into a target's `<specseed_dir>/`.
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
