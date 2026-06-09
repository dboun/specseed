# Remote posts model

How the work breakdown lives on the remote tracker. The spec docs are local
files; the **work layer is remote posts**, and the local tracker is a synced
read cache of them. Labels are the canonical vocabulary in
`specseed_runtime/tracking/supported_values.py` (seeded by
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

Each post carries exactly one tier label and one status label. (adopt may create a
single `EPIC-0000 Phase 0 — Already built` epic at `:status:done` whose body lists
shipped capabilities as plain titles, with no child posts — see `routes/adopt.md`.)

## Status labels

`<tier>:status:<status>` where status is one of:
`todo, in_progress, blocked, in_review, awaiting_approval, awaiting_merge, done, wont_do, deprecated`.
(`in_review` + `awaiting_merge` are meaningful for issues; epics use a coarse subset in practice.)
Terminal = `{done, wont_do, deprecated}`. `wont_do` = never built; `deprecated`
= was real, now retired. `done` means MERGED to primary — an accepted-but-unmerged
issue is `awaiting_merge` (runtime-managed), never `done`, so a dependent stays held
until it lands.

**A status change is a swap:** `remove_entry_label(id, "<tier>:status:<old>")`
then `add_entry_label(id, "<tier>:status:<new>")`. The spec-change worker plans
epics/tickets AND **issues at `:status:todo`** — but creates nothing until the plan
is approved (plan-first; the request is the gate, see `spec-change-protocol.md`). It
*deprecates* retired ones; live status transitions belong to the impl agents, not
this skill.

## Type + difficulty labels

Beyond tier + status, a work post may carry:

- **`type:<kind>`** — `feature` / `bug` / `chore` / `spike` / `qa`. Issues always get
  one; tickets may (never `qa`). Drives body shape (`templates/entity_templates/`) and
  lets the runtime filter (e.g. a `type:qa` issue is a ticket's terminal QA pass).
- **`difficulty:<level>`** — `easy` / `hard` (issues, optional). Modifies the code-review
  gate: `hard` issues never auto-approve, always landing in `awaiting_approval` for a
  human even at high review confidence. Set at formation (`work-breakdown.md`).

Both are seeded in `supported_values.py` / `populate_defaults.py`, so the tracker knows
them like any tier/status label.

## Settled spec docs (lifecycle)

The work posts live on the remote; the spec docs are local files with frontmatter. A
spec doc carries `settled: true` + `settled_at` once a human has approved the change
that produced it. The **producer** is the approval gate: when an `APR-NNNN` batch is
approved, the runtime stamps every path the worker listed in `plan.json.settle_docs`.
The skill never writes `settled` itself. `adapt` is the only route that may reopen an
already-settled doc (and it re-settles on the next approval); `plan-next-sprint` and
`tweak` never touch settled content.

## Relationships (body links, not labels)

In a ticket body, link its epic and its issues; in an issue body, link its
parent ticket and any dependencies. Plain markdown:

```
Epic: #12
Issues: #41, #42, #43
Depends on: #40
```

`satisfies_reqs` (ticket → SRS req ids) and dependencies are written as body
text too. Posts are flat; the structure is the links.

**`Depends on:` is the only thing the runtime gate reads to hold a dependent.** Declare
every issue that needs another issue's code/interface/output (tests -> the code they test,
a consumer -> its producer); an undeclared dep is a race, not a soft order. The `#` is
mandatory. For an item created in the same plan use the title placeholder
`Depends on: #{id:<exact title>}` (apply.py substitutes the real id); for an existing post
use `#NN`. Validate with `scripts/dependencies_validate.py` (see `work-breakdown.md` ->
Issue dependencies).

## Dashboards (permanent management posts)

`populate_defaults.py` seeds four permanent posts labeled `management`
(`current_sprint` also on the sprint board):

| Post | Role | Pinned |
|------|------|:------:|
| ROADMAP | strategic map: phases -> epics -> ticket titles | yes |
| SCHEDULE | tactical schedule: sprints in execution order, each listing its tickets | yes |
| CONTROL | command/ops channel | yes |
| CURRENT SPRINT | the active sprint's board | no |

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
- **ROADMAP** and **CURRENT SPRINT** are rendered automatically by the runtime
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
`spec-change:status:<state>` label: `open, awaiting_input, awaiting_approval,
approved, done, rejected`. The worker never enqueues; the runtime owns the status. It
reads `plan.json` + the staging dir after the run: a run that stages spec or plans
work / settles docs / touches any post but the request is a **proposal**, so the runtime
moves the request to `awaiting_approval` (posting the plan summary + `APR-NNNN`) and, on
approval, to `done` (promoting the staged spec, then running the deferred `apply.py` that
creates the posts). You set a status yourself only for a clarification round (the one
ungated run, touching only the request post): `awaiting_input`, never
`awaiting_approval`. Record intent in `plan.json`. Replies to the request go on this post
as comments.

## Reactions + the approval gate

Posts (not just comments) carry **reactions**: `add_entry_reaction(id, kind)`,
read back on `get_entry(...).data.reactions`. The executor's deterministic
approval system reads two on a post: **👍 `thumbs_up` = approve**, **👎
`thumbs_down` = reject**, by an allowed approver (any non-bot human when no
approver list is configured).

This backs the **approval gate** — which is **plan-first**: a work-creating run posts
NOTHING to the tracker. The runtime posts the `plan_summary` + an `APR-NNNN` request on
the spec-change request and parks it `awaiting_approval`. A human approves the token
(`approve APR-NNNN` comment **or** 👍 on the request) or rejects it (`reject APR-NNNN` /
👎). On approval the runtime settles the docs and runs `apply.py`, which creates the
epics/tickets/issues — issues born `issue:status:todo`, immediately claimable. Nothing
exists before approval; rejection creates nothing. Contract + helpers:
`spec-change-protocol.md` ("Approval gate (APR-NNNN)").

## Draft / ignore

Unlabeled entries are auto-moved to `draft` by `populate_defaults` and ignored.
Do not treat `draft` posts as work. `question` marks clarification threads.

## What the worker may write

Only through the `apply.py` reconcile script, and only what the route plans:
create work posts, edit bodies (incl. the SCHEDULE dashboard — but NOT ROADMAP or
CURRENT SPRINT, which the runtime scheduler owns), swap status labels, comment,
close/delete retired posts. Never write the local cache directly; the remote is
the system's source of truth and the next poll re-syncs the cache from it.
