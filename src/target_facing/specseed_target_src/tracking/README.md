# tracking/

Provider-neutral **issue tracking** layer for specseed.

The whole design rests on one idea: **a remote is always the source of truth,
and `tracking_local` is the local copy.** We never decide what work to do by
reading our own state in isolation — we *sync a remote into the local* and act
on the differences the sync reports.

```
        remote (source of truth)              local (our copy)
   ┌──────────────────────────────┐      ┌──────────────────────┐
   │ TrackingRemoteGitHub         │      │                      │
   │ TrackingRemoteGitLab         │ ───▶ │   TrackingLocal      │
   │ TrackingRemoteLocal (no net) │ sync │                      │
   └──────────────────────────────┘      └──────────────────────┘
                                   returns list[TrackingSyncChange]
```

## Files

| File | Class | Role |
| --- | --- | --- |
| `tracking_base.py` | `TrackingBase` (ABC) | The provider-neutral contract plus the `Tracking*` data shapes (`TrackingResult`, `TrackingEntrySummary`, `TrackingSyncChange`, …). Vocabulary is deliberately neutral: the normalized resource is an **entry**, not an "issue". |
| `tracking_local.py` | `TrackingLocal` | The local copy. A full sqlite implementation of the contract (entries, labels, comments, reactions, pins). Default db: `tracking_local.db`. |
| `tracking_remote_local.py` | `TrackingRemoteLocal` | A **remote** that has no network — it inherits `TrackingLocal` unchanged and only swaps its default db to `tracking_remote_local.db`. Used as a stand-in source of truth for local development and tests. |
| `tracking_remote_github.py` | `TrackingRemoteGitHub` | The real GitHub-backed remote. |
| `tracking_remote_gitlab.py` | `TrackingRemoteGitLab` | The real GitLab-backed remote. |

`TrackingLocal` is *local*; everything prefixed `TrackingRemote*` is a *remote*
(a potential source of truth). The local-vs-remote distinction is the reason the
package was renamed from the old, confusing `remote/` directory.

## Syncing

`TrackingBase.sync_from_remote(remote)` pulls another tracker's state into this
one and returns an ordered `list[TrackingSyncChange]` describing exactly what
changed (create / update / state / delete across labels → entries →
entry_labels/pins → comments → reactions). It uses entry `updated_at`
timestamps to skip child resources that did not change. Those changes are what
the scheduler reacts to — see `../db/database.py` for the work queue they feed.

## Conventions

- **stdlib only.** No third-party dependencies anywhere in this package.
- Every operation returns a `TrackingResult(ok, error, data)` envelope rather
  than raising provider exceptions across the interface.
- Entry ids are `int | str` (GitHub ints, GitLab iids), so downstream code
  stores them as text.

## Tests

`tests/unit/python/test_tracking_local.py` covers `TrackingLocal` and exercises
sync using `TrackingRemoteLocal` as the source of truth. **No tests hit real
GitHub/GitLab** — the local stand-in is authoritative for the test suite.
