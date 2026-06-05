# Spec-change protocol (shared by every route)

This is the spine. Each route (`adopt`, `adapt`, `tweak`, `inject`,
`plan-next-sprint`) decides *what* changes; this file owns *how* the change is
produced and handed off. Read it once; the routes only describe their own logic.

## The request

You are invoked with:

- a **route** (the `spec-change:<route>` label suffix), and
- a **request id** = the id of the remote spec-change post that triggered it.

The post's title + body + comments are the instructions. Read them and the
current work posts from the **local** tracker only.

```python
from specseed_target_src.tracking.resolve_remote import resolve_local
local = resolve_local()                      # TrackingLocal, the read cache
post = local.get_entry(REQUEST_ID).data       # title, body, comments
work = local.list_entries(is_open=None).data  # current epics/tickets/issues
```

Never call `resolve_remote()` to *read*: the local cache exists so a planning
pass never hits the provider API. The remote is touched only by the script you
emit, when the executor runs it.

## The two outputs

1. **Spec edits** under `<specseed_dir>/spec/` (local files), when the route
   calls for them. Edit them in place.
2. **A reconcile script** + its plan, under the request dir:

```
<specseed_dir>/storage/spec-change/<request_id>/
├── plan.json     # the work-breakdown delta you decided (inspectable)
└── apply.py      # mutates the REMOTE posts to match plan.json
```

Use `scheduling/spec_change.py:spec_change_dir(request_id)` to resolve the dir;
create it if missing.

## plan.json

Write the delta you computed as JSON before you write `apply.py`, so a human (and
`apply.py`) can inspect the intended remote changes without reading code. Shape
is yours to fit the route, but keep it explicit. Suggested:

```json
{
  "request_id": "<id>",
  "route": "adapt",
  "creates": [
    {"tier": "ticket", "title": "...", "body": "...",
     "labels": ["ticket", "ticket:status:todo"]}
  ],
  "edits":   [{"post_id": 12, "body": "..."}],
  "labels":  [{"post_id": 12, "add": ["ticket:status:done"],
               "remove": ["ticket:status:todo"]}],
  "comments":[{"post_id": 12, "body": "..."}],
  "closes":  [34],
  "deletes": []
}
```

`apply.py` reads `plan.json` and applies it. Keeping the data and the executor
separate means a human can eyeball the delta and the script stays generic.

## apply.py

Self-contained. Imports `resolve_remote()` (the **configured** provider:
local / GitHub / GitLab) and applies `plan.json` through the tracking contract.
Every tracking call returns `TrackingResult(ok, error, data)`; check `ok` and
abort on the first failure so a half-applied run is obvious.

Canonical header (resolves the dev import root by walking up to the package):

```python
#!/usr/bin/env python3
"""Reconcile remote posts for spec-change <request_id> (route: <route>)."""
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
for _root in _HERE.parents:
    if (_root / "src" / "target_facing" / "specseed_target_src").is_dir():
        sys.path.insert(0, str(_root))
        break

from specseed_target_src.tracking.resolve_remote import resolve_remote


def _ok(result, what):
    if not result.ok:
        raise SystemExit(f"FAILED {what}: {result.error}")
    return result.data


def main() -> int:
    plan = json.loads((_HERE.parent / "plan.json").read_text(encoding="utf-8"))
    remote = resolve_remote()

    for spec in plan.get("creates", []):
        _ok(remote.add_entry(spec["title"], body=spec.get("body"),
                             labels=spec.get("labels")), f"create {spec['title']!r}")
    for edit in plan.get("edits", []):
        _ok(remote.edit_entry(edit["post_id"], title=edit.get("title"),
                              body=edit.get("body")), f"edit {edit['post_id']}")
    for change in plan.get("labels", []):
        for label in change.get("remove", []):
            _ok(remote.remove_entry_label(change["post_id"], label),
                f"-label {label} on {change['post_id']}")
        for label in change.get("add", []):
            _ok(remote.add_entry_label(change["post_id"], label),
                f"+label {label} on {change['post_id']}")
    for comment in plan.get("comments", []):
        _ok(remote.add_entry_comment(comment["post_id"], comment["body"]),
            f"comment {comment['post_id']}")
    for post_id in plan.get("closes", []):
        _ok(remote.set_entry_closed(post_id), f"close {post_id}")
    for post_id in plan.get("deletes", []):
        _ok(remote.delete_entry(post_id), f"delete {post_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The header's import root resolution assumes the development layout (a `src/`
dir). Packaging is a separate, unfinished concern (`install.py` is a stub); when
it lands, this header is the single place to adjust.

## The tracking contract (what apply.py may call)

All on the object from `resolve_remote()`. All return `TrackingResult`.

| Method | Use |
|--------|-----|
| `add_entry(title, body=, labels=, assignees=)` | create an epic/ticket/issue post |
| `edit_entry(id, title=, body=)` | rewrite a post body/title (dashboards, revised bodies) |
| `add_entry_label(id, label)` | attach a tier/status/sprint label |
| `remove_entry_label(id, label)` | detach a label (idempotent) |
| `add_entry_comment(id, body)` | comment (async questions, change notes) |
| `set_entry_open(id)` / `set_entry_closed(id)` | reopen / close |
| `delete_entry(id)` | hard-delete (local + GitLab; **GitHub cannot**, close instead) |
| `ensure_label(name, color=, description=)` | make sure a label exists |
| `pin_entry(id)` | pin a dashboard (GitHub only; GitLab no-ops) |

A **status swap** = `remove_entry_label(id, "<tier>:status:<old>")` then
`add_entry_label(id, "<tier>:status:<new>")`. A **SCHEDULE refresh** =
`edit_entry(schedule_id, body=<rendered markdown>)` (ROADMAP + Current sprint are
runtime-rendered — do not hand-edit). See `remote-posts.md` for the post/label model.

## Approval gate (APR-NNNN): required before any new work

**A run is NOT finished until it asks for human approval of the work it created.**
You never make new work auto-implementable on your own. An impl agent claims an
**issue** the moment it is `issue:status:todo`; so any issue you newly spec must
be born **`issue:status:awaiting_approval`**, never `todo`. The executor flips it
to `todo` (claimable) only after a human approves. This is a status gate, not a
trust gate: there is no path where you create `todo` issues and "remember" to ask.

This is **not** epic-gating. The gate is per newly-created issue, via its status
label, plus one approval-request comment that names the batch.

Steps, every route that creates issues:

1. **Allocate one token** for the batch and a short summary of what you propose:

   ```python
   from specseed_target_src.executing.approvals import next_apr_id, approval_request_comment
   apr_id = next_apr_id(STORAGE_DIR)          # e.g. "APR-0001", monotonic, persisted
   request_body = approval_request_comment(apr_id, summary)  # carries the hidden marker
   ```

   Allocate at **plan time** and write `apr_id` + `summary` into `plan.json` so the
   token is stable across re-runs (do NOT re-allocate in `apply.py`; read it from
   `plan.json`). `STORAGE_DIR` = `<specseed_dir>/storage`.
2. **Create the new issues at `awaiting_approval`.** In `plan.json.creates`, every
   issue's labels are `["issue", "issue:status:awaiting_approval"]`. Tickets and
   epics (PM groupings, not claimed by impl agents) stay `:status:todo`.
3. **Post the approval request.** Put `request_body` as a `comments` entry on the
   spec-change request post (and the same marker comment on each gated issue, so a
   `approve APR-NNNN` on an issue resolves that issue). Swap the request status to
   `spec-change:status:awaiting_approval`.
4. **Stop.** The run ends parked. A human approves the token (`approve APR-NNNN`
   comment **or** 👍 thumbs-up reaction on the issue), or rejects it (`reject
   APR-NNNN` / 👎). The executor's deterministic approval system resolves the gate
   with no agent run: 👍/approve flips the issue `awaiting_approval -> todo`,
   👎/reject parks it `blocked`. See `remote-posts.md`.

When no approver list is configured, any non-bot human may approve (the default).

## Enqueue + stop

After writing `plan.json` and `apply.py`, enqueue the run and stop:

```python
from specseed_target_src.scheduling.spec_change import enqueue_spec_change_run
enqueue_spec_change_run(script_path, request_id=REQUEST_ID, route=ROUTE)
```

The executor (`executing/`) drains the queue and runs `apply.py` as a
permission-gated subprocess. Your job ends at the enqueue: do not run `apply.py`
yourself, do not touch git or code.

## Idempotency

A spec-change may be re-triggered (the human comments again, a poll repeats).
Write `apply.py` so re-running is safe: prefer label add/remove and comments
(idempotent), check current state from `plan.json`, and do not blindly re-create
a post that already exists (record created ids back into the request dir if a
second pass needs them). When unsure, comment rather than duplicate.

## Async clarification

Cannot proceed safely? Do not guess. Put a single clarifying **comment** on the
spec-change post into `plan.json.comments`, add label
`spec-change:status:awaiting_approval`, enqueue, stop. The human replies on the
remote; the next poll re-triggers this route with their answer in the comments.
