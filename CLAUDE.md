# CLAUDE.md

**Write caveman.** Every doc, comment, commit body, PR, and reply: terse, signal-dense, no
filler. Style ref: `skills/specseed/references_ext/caveman.md`. Spec PROSE
the skill *emits* (vision/SAD/SDD/entity bodies/ADR) ALSO gets the humanizer pass + em-dash ban:
`references_ext/humanizer.md`. Tell spawned agents the same. This rule saves tokens on every edit.

## What this repo is

**specseed**: a headless, remote-driven spec+work engine. Two halves:

1. **Runtime** (`src/specseed_runtime/`) - polls a tracker, syncs it, queues work, drains queue,
   runs agents. Stdlib-only python. This is real code that *runs*.
2. **Skill** (`skills/specseed/`) - the non-interactive spec-change worker the runtime invokes
   when a post is labeled `spec-change:<route>`. Markdown instructions.

**The engine is never copied into the target.** It runs from this repo against a target repo:
`python3 src/specseed.py <target_repo> [specseed_dir]` (`specseed_dir` default `.specseed`). The
target gets ONLY data - `<specseed_dir>/storage/` (dbs, config, logs, version marker) and the
generated `<specseed_dir>/spec/`. In THIS dev repo the target is this repo itself, so storage/spec
land at the repo root (gitignored). Configure a target via `src/specseed_runtime/configuring/configure.py`.
(Root `install.py` = just a design-notes stub for a future system-wide install, not code.)

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
  specseed.py                        # launcher: run engine against a target (target_repo + specseed_dir)
  specseed_runtime/                  # the RUNTIME (stdlib-only python)
    tracking/                        #   provider-neutral tracker layer. README inside. entry=neutral resource
    scheduling/                      #   remote diff -> DB queue (sync_to_db) + spec_change enqueue. README inside
    db/database.py                   #   durable sqlite work queue (tasks + task_errors). thread-safe singleton
    tasks/                           #   one typed task class per change kind (handle_*) + task_base + cleanup
    executing/                       #   scheduler(poll loop) + dispatch + advance + agent_runner + control + permissions
    entities/                        #   epic/ticket/issue = meaning over neutral entries (tier/status/links). EntityRef
    state_machines/                  #   legal status transitions + approvals (👍/👎 reactions, approve/reject cmds)
    configuring/                     #   configure.py interactive setup -> config
    migrating/                       #   storage migrations (hops); 0.3.1->0.4.0 deletes copied code; 0.4.0->0.5.0 drops the seed marker so new labels re-seed
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
- **settle-on-approval** (`executing/advance.resolve_spec_change_request`, wired in `dispatch._run_work`) -
  the spec-change REQUEST parks `spec-change:status:awaiting_approval`; when an approver 👍s / `approve`s it,
  the runtime stamps `settled: true` + `settled_at` on the docs the worker listed in `plan.json.settle_docs`
  and moves the request to `done`. Deterministic, no agent. The skill never writes `settled`; adapt is the
  only route that reopens a settled doc.

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
- Entrypoints that migrate-before-read: `executing/run.py` (startup, via the `src/specseed.py`
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
