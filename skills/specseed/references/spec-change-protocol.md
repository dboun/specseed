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
from specseed_runtime.tracking.resolve_remote import resolve_local
local = resolve_local(storage=STORAGE_DIR)    # STORAGE_DIR = <specseed_dir>/storage
post = local.get_entry(REQUEST_ID).data       # title, body, comments
work = local.list_entries(is_open=None).data  # current epics/tickets/issues
```

**Always pass `storage=` explicitly.** Bare `resolve_local()`/`resolve_remote()`
fall back to `$SPECSEED_STORAGE` (the runner exports it) and then to the ENGINE
repo's dev storage - the wrong db when the engine runs against a target.

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
     "labels": ["ticket", "ticket:status:todo"]},
    {"tier": "issue", "title": "...", "body": "...",
     "labels": ["issue", "issue:status:awaiting_approval", "type:feature", "difficulty:hard"]}
  ],
  "edits":   [{"post_id": 12, "body": "..."}],
  "labels":  [{"post_id": 12, "add": ["ticket:status:done"],
               "remove": ["ticket:status:todo"]}],
  "comments":[{"post_id": 12, "body": "..."}],
  "closes":  [34],
  "deletes": [],
  "apr":     {"id": "APR-0001", "summary": "..."},
  "settle_docs": ["spec/api-srs.md", "spec/sad.md"],
  "questions": {},
  "risk": {}
}
```

`settle_docs` lists the spec docs this run created or reopened; on approval the runtime
stamps `settled: true` + `settled_at` on each (the producer for `settled` — see the
approval gate). `questions` records any clarification round already asked (so a
re-trigger never re-asks; format in `references/question-protocol.md`). `risk` holds the
risk-detection & gating pass output (`references/work-breakdown.md`).

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

# The engine is not in the target repo. The scheduler runs this script with the
# engine's src/ on PYTHONPATH; in the dev repo the walk below also finds it.
_HERE = pathlib.Path(__file__).resolve()
for _root in _HERE.parents:
    if (_root / "src" / "specseed_runtime").is_dir():
        sys.path.insert(0, str(_root / "src"))
        break

# This file lives at <storage>/spec-change/<id>/apply.py - storage is two
# levels up. Self-locating: correct even run by hand, without the runner's env.
_STORAGE = _HERE.parents[2]

from specseed_runtime.platform_identity import platform_comment
from specseed_runtime.tracking.resolve_remote import resolve_remote


def _ok(result, what):
    if not result.ok:
        raise SystemExit(f"FAILED {what}: {result.error}")
    return result.data


def main() -> int:
    plan = json.loads((_HERE.parent / "plan.json").read_text(encoding="utf-8"))
    remote = resolve_remote(_STORAGE)

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
        # platform_comment prefixes "specseed: " - marks the comment as the
        # platform's own so the next sync never re-triggers the route on it.
        _ok(remote.add_entry_comment(comment["post_id"], platform_comment(comment["body"])),
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
   from specseed_runtime.executing.approvals import next_apr_id, approval_request_comment
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

**Settling on approval.** Approving the batch is also what **settles the spec**: on
approval the runtime stamps `settled: true` + `settled_at` on every path in
`plan.json.settle_docs` and moves the request to `done`. So a run that only changed
the spec (no new issues) still parks `awaiting_approval` with an `APR-NNNN` if it wrote
or reopened any settled-track doc — the approval is the settle. The skill never writes
`settled` itself; `adapt` is the only route that may reopen an already-settled doc.

When no approver list is configured, any non-bot human may approve (the default).

## Enqueue + stop

After writing `plan.json` and `apply.py`, enqueue the run and stop:

```python
from specseed_runtime.db.database import Database
from specseed_runtime.scheduling.spec_change import enqueue_spec_change_run
enqueue_spec_change_run(script_path, request_id=REQUEST_ID, route=ROUTE,
                        db=Database.instance(STORAGE_DIR / "specseed.db"))
```

`script_path` absolute; `db` explicit so the task lands in the TARGET's queue
(the bare default falls back to `$SPECSEED_STORAGE`, then the engine's dev
storage).

The executor (`executing/`) drains the queue and runs `apply.py` as a
permission-gated subprocess. Your job ends at the enqueue: do not run `apply.py`
yourself, do not touch git or code.

**Chat mode** (no runner; see `references/chat-mode.md`): there is nothing to
enqueue and no remote. Still write `plan.json` + `apply.py` (inert here, runs
later under a real runner), then bundle the working dir into one downloadable zip
and offer it — early and refreshed, never pasted into the output.

## Idempotency

A spec-change may be re-triggered (the human comments again, a poll repeats).
Write `apply.py` so re-running is safe: prefer label add/remove and comments
(idempotent), check current state from `plan.json`, and do not blindly re-create
a post that already exists (record created ids back into the request dir if a
second pass needs them). When unsure, comment rather than duplicate.

**A re-trigger REWRITES the request dir.** The `plan.json` + `apply.py` you find
there are a previous run's output - usually the clarification round the human
just answered. Re-enqueueing that stale script is an idempotent no-op: nothing
posts, the human gets silence. Every run writes a fresh `plan.json` + `apply.py`
for what IT decided (carry forward bookkeeping: `questions`, `apr`, recorded
created ids), then enqueues.

## Async clarification

Cannot proceed safely? Do not guess. Post a **clarification round** — possibly several
questions in the confidence/suggestion format of `references/question-protocol.md`,
delivered as comment(s) on the spec-change post into `plan.json.comments` — add label
`spec-change:status:awaiting_approval`, record the round in `plan.json.questions`,
enqueue, stop. The human replies on the remote (a one-word `OK` accepts all your
suggestions); the next poll re-triggers this route with their answers in the comments.
A round may carry multiple questions; the rule is one round then park, not one question.
