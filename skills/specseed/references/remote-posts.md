# Remote posts model

How the work breakdown lives on the remote tracker. The spec docs are local
files; the **work layer is remote posts**, and the local tracker is a synced
read cache of them. Labels are the canonical vocabulary in
`specseed_runtime/tracking/supported_values.py` (seeded by
`tracking/populate_defaults.py`).

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/spec-change-protocol.md` | the approval gate + how status moves; this doc is the post/label model it acts on |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

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
shipped capabilities as plain titles, with no child posts — see `spec_subroutes/adopt.md`.)

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

- **`type:<kind>`** — `feature` / `bug` / `chore` / `spike`. Issues always get one;
  tickets may. Drives body shape (`templates/entity_templates/`) and lets the runtime
  filter. (`type:qa` still exists in the runtime vocabulary but the skill no longer creates
  it — verification is `impl` test issues + `operate` runs; see `work-breakdown.md`.)
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
parent ticket and any dependencies. Plain markdown, one token per line:

```
# in a ticket body            # in an issue body
Epic: #12                     Ticket: #12
Issues: #41, #42, #43         Depends on: #40
Depends on: #9
```

**The parent token is what the runtime reads to build the tree** (`entities/
entity_base.parse_parent`): a ticket's `Epic: #NN`, an issue's `Ticket: #NN`. The
generic `Parent: #NN` is accepted as a synonym for either. Anything else (no link,
a misspelled keyword) is NOT parsed, so the post lands in the orphan bucket ("Issues
without a ticket"). Every decomposed issue MUST carry a ticket link and every ticket
an epic link, so the tree is never inferred from order. `satisfies_reqs` (ticket →
SRS req ids) and dependencies are written as body text too. Posts are flat; the
structure is the links. `scripts/dependencies_validate.py` checks both the parent
tree and the dependency DAG over `plan.json.creates` (see `work-breakdown.md`).

**`Depends on:` is the only thing the runtime gate reads to hold a dependent.** Declare
every issue that needs another issue's code/interface/output (tests -> the code they test,
a consumer -> its producer); an undeclared dep is a race, not a soft order. The `#` is
mandatory. For an item created in the same plan use the title placeholder
`Depends on: #{id:<exact title>}` (plan.json substitutes the real id); for an existing post
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
counts its LIVE issues (cancelled ones get a `+ N wont_do` note, not a denominator
slot). Reference the ROADMAP post and each ticket post by **bare `#NN`** (e.g.
`#12 PROJ-0001`) — bare `#NN` autolinks on github, gitlab, AND the UI; a
`[label](#NN)` markdown link does NOT (it points at a same-page anchor, dead
everywhere). You write the COMPOSITION (which tickets, order, hours, ★) and seed the counter +
sprint state at any value; the runtime then keeps `(done/total)` and the trailing
`(done|ongoing|planned)` live (see below), so don't fret stale numbers.

```
# SCHEDULE

Sprints in execution order: the tactical schedule. For the strategic map see the
ROADMAP post #<roadmap_id>. The sprint-planning routes keep this in sync.

★ = on the critical path (the longest dependency chain; it sets minimum delivery time).

## SPRINT_2026_W23_A — Foundation sprint  (done)
- #12 PROJ-0001 Local storage foundation — 4h (2/2) ★
- #13 PROJ-0002 Core task commands — 5h (2/2) ★

## SPRINT_2026_W24_A — Workflow sprint  (ongoing)
- #14 PROJ-0003 Due dates and tags — 4h (2/2) ★
- #15 PROJ-0004 CLI polish and docs — 3h (1/2) ★
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
- **SCHEDULE** is split: the sprint-planning routes own its COMPOSITION (which
  tickets sit in which sprint, order, hours, ★) and write it by hand:
  `edit_entry(schedule_id, body=<rendered markdown>)` — only when a route changes
  composition. The runtime scheduler then refreshes the DERIVED bits IN PLACE every
  poll (`executing/dashboards.py` `refresh_schedule_body`): each ticket's
  counter from its issues + each sprint header's trailing `(done|ongoing|planned)`
  from its tickets. The counter is `(done/total)` over LIVE issues; a cancelled
  issue (`wont_do`/`deprecated`) leaves the denominator and is noted instead, e.g.
  `(1/2 + 1 deprecated + 1 wont_do)`. So a finished sprint stops reading stale even
  though you never re-plan it. The runtime touches NOTHING else in the body, and a
  header with no `(state)` / a line with no `(d/t)` counter is left alone.

(The old `config.permissions.remote.post_dashboards` gate is gone — dashboards are
not opt-out.)

## Spec-change post (the request itself)

The triggering post carries `spec-change:<route>` plus a
`spec-change:status:<state>` label: `open, awaiting_input, awaiting_approval,
approved, done, rejected`. The worker never enqueues; the runtime owns the status. It
reads `plan.json` + the staging dir after the run: a run that stages spec or plans
work / settles docs / touches any post but the request is a **proposal**, so the runtime
moves the request to `awaiting_approval` (posting the plan summary + `APR-NNNN`) and, on
approval, to `done` (promoting the staged spec, then running the deferred `plan.json` that
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
👎). On approval the runtime settles the docs and applies `plan.json`, which creates the
epics/tickets/issues — issues born `issue:status:todo`, immediately claimable. Nothing
exists before approval; rejection creates nothing and leaves the request OPEN and parked
`rejected`, so the human's next comment wakes you to redraft the plan under a NEW
`APR-NNNN`. Contract + helpers: `spec-change-protocol.md` ("Approval gate (APR-NNNN)").

## Draft / ignore

Unlabeled entries are auto-moved to `draft` by `populate_defaults` and ignored.
Do not treat `draft` posts as work. `question` marks clarification threads.

## What the worker may write

Only through the `plan.json` reconcile script, and only what the route plans:
create work posts, edit bodies (incl. the SCHEDULE dashboard — but NOT ROADMAP or
CURRENT SPRINT, which the runtime scheduler owns), swap status labels, comment,
close/delete retired posts. Never write the local cache directly; the remote is
the system's source of truth and the next poll re-syncs the cache from it.
