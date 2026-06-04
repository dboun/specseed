# Remote posts model

How the work breakdown lives on the remote tracker. The spec docs are local
files; the **work layer is remote posts**, and the local tracker is a synced
read cache of them. Labels are the canonical vocabulary in
`specseed_target_src/tracking/supported_values.py` (seeded by
`tracking/populate_defaults.py`).

## One post per work item

Every **epic, ticket, and issue is one flat post** (a GitHub/GitLab issue). No
nesting API, no parent/child labels. Relationships are **markdown links in the
body** (identical on both providers and in the local stand-in).

| Tier | Meaning | Tier label |
|------|---------|-----------|
| epic | outcome-level grouping (PM, non-technical) | `epic` |
| ticket | user-visible slice that satisfies requirements (PM) | `ticket` |
| issue | the executable unit an impl agent claims (technical) | `issue` |

Each post carries exactly one tier label and one status label.

## Status labels

`<tier>:status:<status>` where status is one of:
`todo, in_progress, blocked, in_review, awaiting_approval, done, wont_do, deprecated`.
(`in_review` is meaningful for issues; epics use a coarse subset in practice.)
Terminal = `{done, wont_do, deprecated}`. `wont_do` = never built; `deprecated`
= was real, now retired.

**A status change is a swap:** `remove_entry_label(id, "<tier>:status:<old>")`
then `add_entry_label(id, "<tier>:status:<new>")`. The spec-change worker creates
epics/tickets at `:status:todo`, creates **issues at `:status:awaiting_approval`**
(the approval gate, see below), and *deprecates* retired ones; live status
transitions belong to the impl agents, not this skill.

## Relationships (body links, not labels)

In a ticket body, link its epic and its issues; in an issue body, link its
parent ticket and any dependencies. Plain markdown:

```
Epic: #12
Issues: #41, #42, #43
```

`satisfies_reqs` (ticket → SRS req ids) and dependencies are written as body
text too. Posts are flat; the structure is the links.

## Dashboards (permanent management posts)

`populate_defaults.py` seeds four permanent posts labeled `management`
(`current_sprint` also on the sprint board):

| Post | Role | Pinned |
|------|------|:------:|
| ROADMAP | strategic map: phases -> epics -> ticket titles | yes |
| SCHEDULE | tactical schedule: sprints in execution order, each listing its tickets | yes |
| CONTROL | command/ops channel | yes |
| Current sprint | the active sprint's board | no |

ROADMAP and SCHEDULE are **orthogonal**: ROADMAP groups by outcome (what + why),
SCHEDULE groups by time (which tickets ship in which sprint). SCHEDULE never
repeats the strategic map; ROADMAP never lists sprints.

### SCHEDULE body format

Sprints in execution order, each a `##` section; one line per ticket. `★` marks a
ticket on the project critical path. Hours are the ticket estimate; `(done/total)`
counts its issues. Link the ROADMAP post and each ticket post (`[PROJ-0001](#NN)`).

```
# SCHEDULE

Sprints in execution order: the tactical schedule. For the strategic map see the
[ROADMAP](#<roadmap_id>) post. The sprint-planning routes keep this in sync.

★ = on the critical path (the longest dependency chain; it sets minimum delivery time).

## SPRINT_2026_W23_A — Foundation sprint  (done)
- [PROJ-0001](#12) Local storage foundation — 4h (2/2) ★
- [PROJ-0002](#13) Core task commands — 5h (2/2) ★

## SPRINT_2026_W24_A — Workflow sprint  (ongoing)
- [PROJ-0003](#14) Due dates and tags — 4h (2/2) ★
- [PROJ-0004](#15) CLI polish and docs — 3h (1/2) ★
```

Sprint state is one of `done | ongoing | planned`. Until tickets carry hours/CP,
keep the seed's empty-state line.

Find them by title via `local.list_entries(...)`. (GitHub pins cap at 3, which is
why there are three pinned; GitLab has no pinning and `pin_entry` no-ops there.)

**Who refreshes which dashboard:**
- **ROADMAP** and **Current sprint** are rendered automatically by the runtime
  scheduler (`executing/dashboards.py`) from the live work posts, idempotently. Do
  NOT hand-edit them. Create/update the work posts (epics/tickets/issues + labels)
  and the scheduler reflects the change on its next poll. There is no permission
  switch for this; it is unconditional.
- **SCHEDULE** has no runtime renderer, so the sprint-planning routes maintain it by
  hand: `edit_entry(schedule_id, body=<rendered markdown>)`. Refresh it only when a
  route changes sprint composition.

(The old `config.permissions.remote.post_dashboards` gate is gone — dashboards are
not opt-out.)

## Spec-change post (the request itself)

The triggering post carries `spec-change:<route>` plus a
`spec-change:status:<state>` label: `open, awaiting_approval, approved, done,
rejected`. As the worker, you advance its status with the same swap pattern:
moving it to `awaiting_approval` when you ask a question **or when the run created
gated issues** (the approval gate), and to `done` only for a spec-only change with
no new issues. Record intent in `plan.json`. Replies to the request go on this
post as comments.

## Reactions + the approval gate

Posts (not just comments) carry **reactions**: `add_entry_reaction(id, kind)`,
read back on `get_entry(...).data.reactions`. The executor's deterministic
approval system reads two on a post: **👍 `thumbs_up` = approve**, **👎
`thumbs_down` = reject**, by an allowed approver (any non-bot human when no
approver list is configured).

This backs the **approval gate**: a newly-specced **issue** is created
`issue:status:awaiting_approval`, so no impl agent claims it. The worker posts an
`APR-NNNN` approval-request comment naming the batch. A human approves the token
(`approve APR-NNNN` comment **or** 👍 on the issue) or rejects it (`reject
APR-NNNN` / 👎). The executor then flips the issue `awaiting_approval -> todo`
(claimable) or parks it `blocked`, with no agent run. Contract +
helpers: `spec-change-protocol.md` ("Approval gate (APR-NNNN)").

## Draft / ignore

Unlabeled entries are auto-moved to `draft` by `populate_defaults` and ignored.
Do not treat `draft` posts as work. `question` marks clarification threads.

## What the worker may write

Only through the `apply.py` reconcile script, and only what the route plans:
create work posts, edit bodies (incl. the SCHEDULE dashboard — but NOT ROADMAP or
Current sprint, which the runtime scheduler owns), swap status labels, comment,
close/delete retired posts. Never write the local cache directly; the remote is
the system's source of truth and the next poll re-syncs the cache from it.
