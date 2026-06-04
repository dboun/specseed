# CLAUDE.md

**Write caveman.** Every doc, comment, commit body, PR, and reply: terse, signal-dense, no
filler. Style ref: `src/target_facing/skills/specseed/references_ext/caveman.md`. Spec PROSE
the skill *emits* (vision/SAD/SDD/entity bodies/ADR) ALSO gets the humanizer pass + em-dash ban:
`references_ext/humanizer.md`. Tell spawned agents the same. This rule saves tokens on every edit.

## What this repo is

Source + installer for **specseed**: a headless, remote-driven spec+work engine. Two halves:

1. **Runtime** (`specseed_target_src/`) - polls a tracker, syncs it, queues work, drains queue,
   runs agents. Stdlib-only python. This is real code that *runs*.
2. **Skill** (`skills/specseed/`) - the non-interactive spec-change worker the runtime invokes
   when a post is labeled `spec-change:<route>`. Markdown instructions.

`src/specseed.py` is the installer: copies both into a target repo at `<repo>/.specseed/` (then
point user at `configuring/configure.py`). Re-run refreshes runtime, preserves target storage/config.
In THIS dev repo they live under `src/target_facing/{specseed_target_src,skills}`; installed they're
`<repo>/.specseed/...`. (Root `install.py` = just a design-notes stub, not code.)

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
  specseed.py                        # the installer: copies specseed_target_src + skills -> <target>/.specseed/
  target_facing/
    skills/specseed/                 # the spec-change worker skill (markdown)
      SKILL.md                       #   START HERE. router: routes, contract, hard rules
      routes/                        #   adopt/adapt/tweak/inject/plan-next-sprint
      references/                    #   spec-change-protocol, work-breakdown, remote-posts, component-questions
      references_ext/                #   caveman.md (density) + humanizer.md (naturalness)
      templates/entity_templates/    #   epic/ticket/issue/bug/feature emitted into target
    specseed_target_src/             # the RUNTIME (stdlib-only python)
      tracking/                      #   provider-neutral tracker layer. README inside. entry=neutral resource
      scheduling/                    #   remote diff -> DB queue (sync_to_db) + spec_change enqueue. README inside
      db/database.py                 #   durable sqlite work queue (tasks + task_errors). thread-safe singleton
      tasks/                         #   one typed task class per change kind (handle_*) + task_base + cleanup
      executing/                     #   scheduler(poll loop) + dispatch + advance + agent_runner + control + permissions
      entities/                      #   epic/ticket/issue = meaning over neutral entries (tier/status/links). EntityRef
      state_machines/               #   legal status transitions + approvals (👍/👎 reactions, approve/reject cmds)
      configuring/                   #   configure.py interactive setup -> config
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

## Conventions

- Every tracking op returns `TrackingResult(ok, error, data)` - check `ok`, fail loud. Never raise across
  the interface, never reach around the tracker to mutate a remote.
- Entry ids are `int | str` (GitHub ints, GitLab iids) - store as text.
- GitHub can't hard-delete issues: use `set_entry_closed`, not `delete_entry` (fine on local/GitLab).
- Bare imports + `sys.path` wiring at import time - match surrounding files.
- Each `scheduling/` and `tracking/` dir has its own README. Read it before editing there.
