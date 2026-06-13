# Spec-change protocol (shared by every route)

This is the spine. Each route (`adopt`, `adapt`, `tweak`, `inject`,
`plan-next-sprint`) decides *what* changes; this file owns *how* the change is
produced and handed off. Read it once; the routes only describe their own logic.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/remote-posts.md` | the post/label model `apply.py` mutates |
| `references/work-breakdown.md` | how the breakdown that fills `plan.json` is formed + validated |
| `references/reply-protocol-base.md` | the async clarification-round format |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|
| `dependencies_validate.py` | validate `plan.json.creates` links before emitting `apply.py` |

## The request

You are invoked with:

- a **route** (the `spec-change:<route>` label suffix), and
- a **request id** = the id of the remote spec-change post that triggered it.

The post's title + body + comments are the instructions. Read them and the
current work posts from the **local** tracker only.

```python
from specseed_runtime.tracking.resolve_remote import resolve_local
local = resolve_local(storage=STORAGE_DIR)    # STORAGE_DIR = the DATA ROOT (absolute path in the prompt)
post = local.get_entry(REQUEST_ID).data       # title, body, comments
work = local.list_entries(is_open=None).data  # current epics/tickets/issues
```

`STORAGE_DIR` is the **data root** - the absolute path the runtime prompt names
(in the app home, NOT the target). The spec route is the one route allowed to read
the local tracker. **Always pass `storage=` explicitly.** Bare
`resolve_local()`/`resolve_remote()` fall back to `$SPECSEED_STORAGE` and then the
ENGINE repo's dev storage - the wrong db when the engine runs against a target.

Never call `resolve_remote()` to *read*: the local cache exists so a planning
pass never hits the provider API. The remote is touched only by the script you
emit, when the executor runs it.

## The three outputs

You read the live spec dir (absolute path in the prompt) for CONTEXT only. You never
write there. All three outputs land under your spec-change request dir (also an
absolute path the prompt names):

```
<request_dir>/                # = the spec-change dir the prompt gives you
├── spec/...      # STAGED spec edits, mirroring the live spec/ tree
├── plan.json     # the work-breakdown delta you decided (inspectable)
└── apply.py      # mutates the REMOTE posts to match plan.json
```

1. **Staged spec edits** under `<request_dir>/spec/`. Every created or edited doc goes
   here at the SAME relative path it has under live `spec/` (so `spec/sad.md` stages at
   `<request_dir>/spec/sad.md`). The runtime
   promotes these into live `spec/` ONLY after a human approves, so an unapproved or
   buggy run cannot corrupt the real spec. Use
   `scheduling/spec_change.py:spec_change_spec_dir(request_id)` for the staging path.
   Spec docs follow `templates/spec_doc_templates/` (vision/srs/sad/sdd/adr).
2. **plan.json** + **apply.py** in the request dir. Use
   `scheduling/spec_change.py:spec_change_dir(request_id)` to resolve the dir; create
   it if missing.

## plan.json

Write the delta you computed as JSON before you write `apply.py`, so a human (and
`apply.py`) can inspect the intended remote changes without reading code. Shape
is yours to fit the route, but keep it explicit. Suggested:

```json
{
  "request_id": "<id>",
  "route": "adapt",
  "creates": [
    {"tier": "epic", "title": "EPIC-0001 ...", "body": "...",
     "labels": ["epic", "epic:status:todo"]},
    {"tier": "ticket", "title": "PROJ-0001 ...", "body": "Epic: #{id:EPIC-0001 ...}",
     "labels": ["ticket", "ticket:status:todo"]},
    {"tier": "issue", "title": "FEAT-0001 ...", "body": "Ticket: #{id:PROJ-0001 ...}",
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
re-trigger never re-asks; format in `references/reply-protocol-base.md`). `risk` holds the
risk-detection & gating pass output (`references/work-breakdown.md`).

**`plan_summary` + `apr` are required for a proposing run** (one that creates work or
settles docs). `plan_summary` is the human-readable plan the runtime posts for approval:
a short overall summary, then EVERY epic/ticket/issue you will create as a titled bullet
with a one-line description. `apr` is the approval token (see the approval gate). **Issues
are born `issue:status:todo`, never `awaiting_approval`** — the plan approval is the gate;
no post exists until it is approved.

**Cross-references between creates:** post ids don't exist until `apply.py` runs, so
a child body references its parent as `#{id:<parent title>}` — the template below
substitutes the real id at create time while preserving the leading `#`. Never hardcode
guessed ids (`#1`, `#2`). The PARENT keyword matters: a ticket links its epic with
`Epic: #{id:...}`, an issue its ticket with `Ticket: #{id:...}` (`Parent:` also works);
the runtime builds the tree only from these, so a missing or misspelled keyword orphans
the post. Every issue links a ticket, every ticket an epic. For dependency lines, the
`#` is mandatory; bare `Depends on: {id:...}` / `Depends on: 9` is invalid because the
dependency gate will not enforce it. Validate both link kinds with
`scripts/dependencies_validate.py` before emitting.

`apply.py` reads `plan.json` and applies it — but only AFTER approval (see the gate).
Keeping the data and the executor separate means a human can eyeball the delta and the
script stays generic.

**The runtime derives the gate from `plan.json` + the staging dir** (see the approval
gate). It is a proposal needing approval if a staged spec file exists, or `creates` /
`settle_docs` / `closes` / `deletes` is non-empty, or any `edits` / `labels` /
`comments` entry targets a post OTHER than the request post itself. So write `plan.json`
honestly; the runtime reads it to decide whether a human must sign off.

## apply.py

Self-contained. Imports `resolve_remote()` (the **configured** provider:
local / GitHub / GitLab) and applies `plan.json` through the tracking contract.
Every tracking call returns `TrackingResult(ok, error, data)`; check `ok` and
abort on the first failure so a half-applied run is obvious.

**apply.py runs ONLY after a human approves the plan** (the runtime enqueues it
then — see the approval gate). So it is the DOER that actually creates the posts.
It must NOT touch the request's `spec-change:status` to `awaiting_approval` (the
propose step already parked it). Its job: create the work. It does NOT promote the
staged spec into live `spec/` — the runtime does that on approval, before running
apply.py. The **request closes itself** — the runtime swaps it to
`spec-change:status:done` on approval and closes the request post in code after this
apply succeeds. Do NOT put `REQUEST_ID` in `plan.json.closes`; that list is only for
OTHER posts the change retires (e.g. a superseded ticket). A re-trigger before approval
REWRITES this script; only the approved copy ever runs.

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

# This file lives at <data_root>/spec-change/<id>/apply.py - the data root is two
# levels up. Self-locating: correct even run by hand, without the runner's env.
_STORAGE = _HERE.parents[2]

from specseed_runtime.platform_identity import platform_comment
from specseed_runtime.tracking.resolve_remote import load_config, resolve_remote


def _ok(result, what):
    if not result.ok:
        raise SystemExit(f"FAILED {what}: {result.error}")
    return result.data


def main() -> int:
    plan = json.loads((_HERE.parent / "plan.json").read_text(encoding="utf-8"))
    remote = resolve_remote(_STORAGE)
    config = load_config(_STORAGE)  # drives the platform_comment prefix

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
        if "Depends on:" in body and "Depends on: #" not in body:
            raise SystemExit(f"FAILED create {title!r}: dependency refs must use # links")
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
        # platform_comment marks the comment as the platform's own so the next sync
        # never re-triggers the route on it. The "specseed: " prefix is added ONLY
        # when author alone can't tell platform from human (config decides); a
        # distinct bot account drops it, keeping a structured-envelope body clean.
        _ok(remote.add_entry_comment(comment["post_id"], platform_comment(comment["body"], config)),
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
`edit_entry(schedule_id, body=<rendered markdown>)` (ROADMAP + CURRENT SPRINT are
runtime-rendered — do not hand-edit). See `remote-posts.md` for the post/label model.

## Approval gate (APR-NNNN): the runtime decides, the worker never does

**Plan-first, code-enforced. The worker writes its three outputs and STOPS. It does
NOT enqueue anything and does NOT decide whether the run needs approval.** The runtime
reads `plan.json` + the staging dir after the run and derives the gate in code. No epic,
ticket, or issue is posted to the tracker before a human approves. The gate is the
spec-change request itself; there is no per-issue status gate.

Two outcomes the runtime can pick:

- **Gated (proposal, needs approval).** ANY of these makes it a proposal: a staged spec
  file exists, OR `plan.json` has non-empty `creates` / `settle_docs` / `closes` /
  `deletes`, OR any `edits` / `labels` / `comments` entry targets a post OTHER than the
  request post itself. The runtime posts your `plan_summary` + an `APR-NNNN` request onto
  the request post and parks it `spec-change:status:awaiting_approval`. It creates and
  promotes NOTHING. A human approves (`approve APR-NNNN` comment, 👍 on the request, or
  the UI button) or rejects (`reject APR-NNNN` / 👎). On approval the runtime promotes
  the staged spec into live `spec/`, stamps `settled`, swaps the request to
  `spec-change:status:done`, and runs your `apply.py` (which NOW creates the
  epics/tickets/issues). On rejection nothing is created or promoted and the request closes.
- **Direct (clarification round, no approval).** The ONE ungated run: it touches ONLY
  the request post (a clarifying comment plus flipping the request's own
  `spec-change:status` label), creates no work and stages no spec. The runtime runs
  apply.py straight away so the question reaches the human, and parks `awaiting_input`.

Plainly: **every spec-change that creates work or touches the spec needs explicit human
approval. The one ungated run is a clarification round (asking the human a question).**

Consequences for what you write:

- **Issues are born `issue:status:todo`**, never `awaiting_approval`. The plan
  approval already gated them; once created they are immediately claimable and the
  executor implements them (per `auto_implement_issue`). Epics/tickets are `todo` too.
- **`plan_summary` (required):** the human-readable plan the runtime posts for
  approval — a short overall summary, then EVERY epic/ticket/issue you will create as
  a titled bullet with a one-line description. Markdown, into `plan.json`.
  `plan_summary` + `apr` are required for any run that is not a pure clarification (the
  runtime's propose step fails loud without `apr.id`).
- **`apr` (required):** allocate one token at **plan time** and write `{id, summary}`
  into `plan.json`:

   ```python
   from specseed_runtime.executing.approvals import next_apr_id
   apr_id = next_apr_id(STORAGE_DIR)   # "APR-0001", monotonic, persisted
   ```

  You do NOT hand-write or post the approval comment — the runtime builds it from
  `apr` (verbatim `approval_request_comment`, hidden marker + how-to-approve text)
  and posts it. `STORAGE_DIR` = the data root (absolute path in the prompt).
- **Carry forward vs mint fresh — the gate the human sees must match the plan.** The
  runtime no-ops a propose run whose `apr.id` is ALREADY posted on the thread (idempotency
  — see the approval gate). So:
  - **Bare re-trigger of an UNCHANGED proposal** (poll repeat, crash retry, nothing new
    from the human): carry the SAME `apr.id` forward and never re-allocate in apply.py. The
    runtime no-ops the duplicate gate — correct, the standing gate still reflects this plan.
  - **REVISED proposal that supersedes one already posted** (you proposed, then the human
    answered a clarification round or asked for a change, and THIS run re-plans with
    different `creates`/`settle_docs`/staged spec): allocate a **FRESH** `apr_id`. The old
    gate reflects the OLD plan; reusing its id makes the runtime no-op and your revised plan
    NEVER reaches the human — the post just sits with their reply, looking ignored. A new id
    → the runtime posts the updated `plan_summary` + a new gate (the latest request comment
    is the live one). Keep the prior round's id in `plan.json.questions` bookkeeping if you
    track it, but `apr.id` itself moves to the new token.
- **The runtime finalizes the request, not you:** on approval it swaps the request
  to `spec-change:status:done` and closes the request post in code once apply.py
  succeeds. Do NOT put `REQUEST_ID` in `plan.json.closes` (that list is for OTHER
  posts the change retires) and do NOT set the request `awaiting_approval` in
  apply.py — the propose step did that.

**Settling on approval.** Approving the plan is also what **settles the spec**: the
runtime promotes the staged docs into live `spec/`, then stamps `settled: true` +
`settled_at` on every `plan.json.settle_docs` path and moves the request to `done`. A run
that only edits the spec (no new work) still gates as a proposal — a staged spec file is
enough to require approval — the approval is the settle, and the runtime closes the
request with no apply.py to run. The skill never writes `settled` itself; `adapt` is the
only route that may reopen a settled doc (the reopened doc is STAGED like any other).

When no approver list is configured, any non-bot human may approve (the default).

## Hand off + stop

Write the three outputs (staged spec, `plan.json`, `apply.py`), then STOP. You do NOT
enqueue anything and you do NOT choose whether the run needs approval — the runtime owns
that. It reads `plan.json` + the staging dir, gates the run in code (see the approval
gate), and drives it from there: a proposal posts the summary + APR and parks (promoting
the spec + running `apply.py` only on approval); a clarification round runs `apply.py`
straight away as a permission-gated subprocess. Either way your job ends when the files
are written: never run `apply.py`, never touch git or code.

**Chat mode** (no runner; see `references/chat-mode.md`): no remote, nothing to enqueue
anyway. Still write the staged spec + `plan.json` + `apply.py` (inert here, runs later
under a real runner), then bundle the working dir into one downloadable zip and offer it
(early and refreshed, never pasted into the output).

## Idempotency

A spec-change may be re-triggered (the human comments again, a poll repeats), and a
CRASHED `apply.py` is retried by the runtime — a naive script then duplicates every
post it made before the crash. Write `apply.py` so re-running is safe: keep the
`created.json` ledger from the template (skip already-created titles), prefer label
add/remove and comments (idempotent), check current state from `plan.json`. When
unsure, comment rather than duplicate.

**A re-trigger REWRITES the request dir.** The staged spec + `plan.json` + `apply.py`
you find there are a previous run's output - usually the clarification round the human
just answered. Every run writes a fresh set of outputs for what IT decided (carry
forward bookkeeping: `questions`, `apr`, recorded created ids), then stops; the runtime
re-derives the gate from the new output.

## Async clarification

Cannot proceed safely? Do not guess. Post a **clarification round** (round format,
sizing, the confidence/suggestion discipline, and the one-round-then-park rule all live
in `references/reply-protocol-base.md`) delivered as comment(s) on the spec-change post into
`plan.json.comments`, add label `spec-change:status:awaiting_input` (never
`awaiting_approval`; that is the runtime's APR plan gate), and record the round in
`plan.json.questions`. This is the ONE ungated run: it touches ONLY the request post,
creates no work, and stages no spec, so the runtime runs `apply.py` straight away (a
direct apply) instead of gating it. Write the files and stop; the next poll re-triggers
this route with the human's answers in the comments.

**A clarification plan carries ONLY the clarification.** Leave `creates`, `settle_docs`,
`closes`, `deletes` empty, stage NO spec files, and aim every `edits`/`labels`/`comments`
entry at the request post itself. Do NOT copy a prior run's proposal forward "to keep it
around" - a re-trigger REWRITES the dir, so emit a FRESH plan for what THIS run decided
(a clarification). Leftover `creates` (or any staged spec) make the runtime read the run
as a proposal, not a clarification: it gates on the already-posted APR, no-ops, and your
questions never reach the human. When you ask, ask and nothing else.

**Resuming after the human answers.** The next run reads their reply and emits the (now
revised) proposal. If a proposal gate was ALREADY posted before you diverted to clarify,
that standing gate is now stale — mint a **FRESH** `apr.id` for the revised proposal (see
the `apr` carry-forward-vs-fresh rule above), or the runtime no-ops and the answers you
just folded in never surface as a new gate.
