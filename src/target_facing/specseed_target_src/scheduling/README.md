# scheduling/

Turns remote activity into queued work.

The pipeline is: **a remote is the source of truth → we sync it into the local
tracker → the differences become tasks on the DB queue → a scheduler drains
them.** This directory owns the middle arrow.

```
  remote (truth) ──sync_from_remote──▶ local ──▶ list[TrackingSyncChange]
                                                        │
                                                  sync_to_db.py
                                                        ▼
                                                  DB queue (../db)
```

## `sync_to_db.py`

`sync_to_db(local, remote, db=None)` calls `local.sync_from_remote(remote)` and
applies the resulting changes to the queue. It returns a summary dict
(`enqueued`, `superseded`, `coalesced`, `cleanups`, `interrupts_todo`,
`ignored`, …); on a failed sync it returns `{"ok": False, "error": ...}`.

It is the only place that holds reaction policy. Three behaviours:

### 1. Mapping — change → task

Each change maps to a typed task in `../tasks` (one class per file) or is ignored.

| change (`resource_type` / `action`) | task | notes |
| --- | --- | --- |
| `entry` / `create` | `HandleEntryCreated` | carries title + an `EntityRef` |
| `entry` / `update` | `HandleEntryUpdated` | per-field changes coalesced to one per entry |
| `entry` / `state` → open | `HandleEntryReopened` | |
| `entry` / `state` → closed | *teardown* | see below |
| `entry` / `delete` | *teardown* | see below |
| `entry_label` / `create` | `HandleLabelAdded` | `tier:`/`status:` labels add `EntityRef` to payload |
| `entry_label` / `delete` | `HandleLabelRemoved` | |
| `comment` / `create` | `HandleCommentAdded` | |
| `comment` / `update` | `HandleCommentUpdated` | |
| `reaction` / `create` | `HandleReactionAdded` | post_id is the *comment* (see task docstring) |
| `reaction` / `delete` | `HandleReactionRemoved` | |
| repo-level `label` / *, `pin` / * | — | ignored (no agent work) |

Ordering is inherited from `sync_from_remote` (tier-sorted creates so an epic
lands before its tickets; leaf-first deletes), so no extra sorting happens here.

### 2. Supersession — by resource identity

Before a task is enqueued, any **pending** task about the same remote resource is
removed. Identity is `task_base.resource_key` (e.g. `("comment", id)`,
`("entry_label", post, label)`, `("entry", post)`), independent of action. So:

- editing a comment supersedes the not-yet-handled "comment added" task — the
  edited version is what gets processed;
- a label add followed by a remove cancels out to a single "removed" task;
- duplicate change-storms collapse to one row.

In-progress tasks are never superseded (they're being worked).

### 3. Teardown — when an entry is closed or deleted

- every **pending** task for that entry is dropped (it's obsolete);
- for any **in-progress** task, a `CleanupTask` is enqueued (referencing it) and
  `_request_interrupt()` is called.

`_request_interrupt()` is a deliberate **no-op TODO**: there is no execution
engine yet, and an in-progress row must never be edited. When a runner exists,
it should cooperatively abort the running task before its `CleanupTask` runs.

## Dependencies

`sync_to_db` wires `../db`, `../tracking`, `../tasks` and `../entities` onto
`sys.path` at import time, matching the bare-import convention of the rest of the
scripts. Stdlib only.

## Tests

`tests/unit/python/test_sync_to_db.py` drives a `TrackingRemoteLocal` →
`TrackingLocal` sync through `sync_to_db` and asserts on the queue: tier-ordered
creation, entity context in payloads, comment-edit supersession, label
add/remove cancellation, close→sweep+cleanup of in-progress work, and idempotent
re-sync. No GitHub/GitLab.
