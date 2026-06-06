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
    {"tier": "ticket", "title": "PROJ-0001 ...", "body": "...",
     "labels": ["ticket", "ticket:status:todo"]},
    {"tier": "issue", "title": "FEAT-0001 ...", "body": "Parent: #{id:PROJ-0001 ...}",
     "labels": ["issue", "issue:status:todo", "type:feature", "difficulty:hard"]}
  ],
  "edits":   [{"post_id": 12, "body": "..."}],
  "labels":  [{"post_id": 12, "add": ["ticket:status:done"],
               "remove": ["ticket:status:todo"]}],
  "comments":[{"post_id": 12, "body": "..."}],
  "closes":  [34],
  "deletes": [],
  "apr":         {"id": "APR-0001", "summary": "..."},
  "plan_summary": "## Proposed plan\n- EPIC-0001 ... — one line\n- FEAT-0001 ... — one line",
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

**`plan_summary` + `apr` are required for a proposing run** (one that creates work or
settles docs). `plan_summary` is the human-readable plan the runtime posts for approval:
a short overall summary, then EVERY epic/ticket/issue you will create as a titled bullet
with a one-line description. `apr` is the approval token (see the approval gate). **Issues
are born `issue:status:todo`, never `awaiting_approval`** — the plan approval is the gate;
no post exists until it is approved.

**Cross-references between creates:** post ids don't exist until `apply.py` runs, so
a child body references its parent as `{id:<parent title>}` — the template below
substitutes the real id at create time. Never hardcode guessed ids (`#1`, `#2`).

`apply.py` reads `plan.json` and applies it — but only AFTER approval (see the gate).
Keeping the data and the executor separate means a human can eyeball the delta and the
script stays generic.

## apply.py

Self-contained. Imports `resolve_remote()` (the **configured** provider:
local / GitHub / GitLab) and applies `plan.json` through the tracking contract.
Every tracking call returns `TrackingResult(ok, error, data)`; check `ok` and
abort on the first failure so a half-applied run is obvious.

**apply.py runs ONLY after a human approves the plan** (the runtime enqueues it
then — see the approval gate). So it is the DOER that actually creates the posts.
It must NOT touch the request's `spec-change:status` to `awaiting_approval` (the
propose step already parked it). Its job: create the work, then **finalize the
request** — put `REQUEST_ID` in `plan.json.closes` so apply.py closes it after the
work exists (the runtime already swapped the request to `spec-change:status:done`
on approval). A re-trigger before approval REWRITES this script; only the approved
copy ever runs.

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

    # Idempotency ledger: title -> created post id, persisted NEXT TO this
    # script. A crashed run gets retried by the runtime; the ledger makes the
    # retry skip posts that already exist instead of duplicating them.
    ledger_path = _HERE.parent / "created.json"
    created = (json.loads(ledger_path.read_text(encoding="utf-8"))
               if ledger_path.exists() else {})

    for spec in plan.get("creates", []):
        title = spec["title"]
        if title in created:
            continue  # a previous (crashed) run already made this one
        body = spec.get("body") or ""
        for parent_title, parent_id in created.items():
            body = body.replace("{id:" + parent_title + "}", str(parent_id))
        # .data is a TrackingPostId-shaped object - the id lives at .id
        new = _ok(remote.add_entry(title, body=body, labels=spec.get("labels")),
                  f"create {title!r}")
        created[title] = new.id
        ledger_path.write_text(json.dumps(created, indent=1), encoding="utf-8")
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

**Payload shapes:** `result.data` is a typed object, NEVER a dict — don't index it.
`add_entry`/`add_entry_comment`/`delete_entry` → object with the new/affected id at
`.data.id`. `get_entry` → post details (`.title`, `.body`, `.labels`, `.is_open`,
`.comments`). Pass BARE ids (`int | str`) into calls, not the id objects.

A **status swap** = `remove_entry_label(id, "<tier>:status:<old>")` then
`add_entry_label(id, "<tier>:status:<new>")`. A **SCHEDULE refresh** =
`edit_entry(schedule_id, body=<rendered markdown>)` (ROADMAP + Current sprint are
runtime-rendered — do not hand-edit). See `remote-posts.md` for the post/label model.

## Approval gate (APR-NNNN): nothing is created until the plan is approved

**Plan-first. A run that creates work or settles spec docs does NOT create
anything — it proposes a PLAN and stops.** No epic, ticket, or issue is posted to
the tracker before a human approves. The gate is the spec-change request itself;
there is no per-issue status gate.

How it works:

1. You write the spec edits (unsettled), `plan.json` (with `plan_summary` + `apr`),
   and `apply.py` (the doer that creates the posts) — but you do NOT run apply.py.
   You enqueue a **proposal** instead (see Enqueue + stop).
2. The runtime posts your `plan_summary` + an `APR-NNNN` approval request onto the
   request post and parks it `spec-change:status:awaiting_approval`. **Nothing is
   created.**
3. A human approves (`approve APR-NNNN` comment **or** 👍 on the request) or rejects
   (`reject APR-NNNN` / 👎).
4. On approval the runtime settles the docs, swaps the request to
   `spec-change:status:done`, and runs your `apply.py` — which NOW creates the
   epics/tickets/issues. On rejection nothing is created and the request closes.

Consequences for what you write:

- **Issues are born `issue:status:todo`**, never `awaiting_approval`. The plan
  approval already gated them; once created they are immediately claimable and the
  executor implements them (per `auto_implement_issue`). Epics/tickets are `todo` too.
- **`plan_summary` (required):** the human-readable plan the runtime posts for
  approval — a short overall summary, then EVERY epic/ticket/issue you will create as
  a titled bullet with a one-line description. Markdown, into `plan.json`.
- **`apr` (required):** allocate one token at **plan time** and write `{id, summary}`
  into `plan.json` (stable across re-runs — do NOT re-allocate in apply.py):

   ```python
   from specseed_runtime.executing.approvals import next_apr_id
   apr_id = next_apr_id(STORAGE_DIR)   # "APR-0001", monotonic, persisted
   ```

  You do NOT hand-write or post the approval comment — the runtime builds it from
  `apr` (verbatim `approval_request_comment`, hidden marker + how-to-approve text)
  and posts it. `STORAGE_DIR` = `<specseed_dir>/storage`.
- **apply.py finalizes the request:** put `REQUEST_ID` in `plan.json.closes` so the
  doer closes it after the work exists. Do NOT set the request `awaiting_approval`
  in apply.py — the propose step did that.

**Settling on approval.** Approving the plan is also what **settles the spec**: the
runtime stamps `settled: true` + `settled_at` on every `plan.json.settle_docs` path
and moves the request to `done`. A run that only edits the spec (no new work) still
proposes with an `APR-NNNN` if it touched a settled-track doc — the approval is the
settle, and the runtime closes the request with no apply.py to run. The skill never
writes `settled` itself; `adapt` is the only route that may reopen a settled doc.

When no approver list is configured, any non-bot human may approve (the default).

## Enqueue + stop

After writing `plan.json` and `apply.py`, enqueue and stop. Pick ONE:

**Proposing run** — created work and/or settled spec docs (the common case). The
runtime posts the summary + APR, parks the request, and runs your `apply.py` only
once a human approves:

```python
from specseed_runtime.db.database import Database
from specseed_runtime.scheduling.spec_change import enqueue_spec_change_propose
enqueue_spec_change_propose(REQUEST_ID, route=ROUTE,
                            db=Database.instance(STORAGE_DIR / "specseed.db"))
```

**Direct apply** — a mechanical run that creates NO work and settles NO doc (e.g. a
sprint label shuffle that needs no sign-off). apply.py runs straight away:

```python
from specseed_runtime.db.database import Database
from specseed_runtime.scheduling.spec_change import enqueue_spec_change_run
enqueue_spec_change_run(script_path, request_id=REQUEST_ID, route=ROUTE,
                        db=Database.instance(STORAGE_DIR / "specseed.db"))
```

`script_path` absolute; `db` explicit so the task lands in the TARGET's queue
(the bare default falls back to `$SPECSEED_STORAGE`, then the engine's dev
storage).

The executor (`executing/`) drains the queue: a proposal posts the summary + APR
and parks (running `apply.py` only on approval); a direct apply runs `apply.py`
straight away as a permission-gated subprocess. Either way your job ends at the
enqueue: do not run `apply.py` yourself, do not touch git or code.

**Chat mode** (no runner; see `references/chat-mode.md`): there is nothing to
enqueue and no remote. Still write `plan.json` + `apply.py` (inert here, runs
later under a real runner), then bundle the working dir into one downloadable zip
and offer it — early and refreshed, never pasted into the output.

## Idempotency

A spec-change may be re-triggered (the human comments again, a poll repeats), and a
CRASHED `apply.py` is retried by the runtime — a naive script then duplicates every
post it made before the crash. Write `apply.py` so re-running is safe: keep the
`created.json` ledger from the template (skip already-created titles), prefer label
add/remove and comments (idempotent), check current state from `plan.json`. When
unsure, comment rather than duplicate.

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
`spec-change:status:awaiting_approval`, record the round in `plan.json.questions`. A
clarification creates no work and settles no doc, so enqueue it as a **direct apply**
(`enqueue_spec_change_run`, not a proposal), then stop. The human replies on the remote
(a one-word `OK` accepts all your suggestions); the next poll re-triggers this route
with their answers in the comments.
A round may carry multiple questions; the rule is one round then park, not one question.
