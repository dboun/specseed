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
`add_entry_label(id, "<tier>:status:<new>")`. A **dashboard refresh** =
`edit_entry(dashboard_id, body=<rendered markdown>)`. See `remote-posts.md` for
the post/label model.

## Enqueue + stop

After writing `plan.json` and `apply.py`, enqueue the run and stop:

```python
from specseed_target_src.scheduling.spec_change import enqueue_spec_change_run
enqueue_spec_change_run(script_path, request_id=REQUEST_ID, route=ROUTE)
```

> **Executor not implemented.** Nothing drains the queue yet (`executing/` is
> empty). The task sits `pending`. This is expected and correct for now: the
> skill's job ends at enqueue. Do not try to run `apply.py` yourself.

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
