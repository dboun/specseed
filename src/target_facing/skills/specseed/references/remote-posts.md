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
then `add_entry_label(id, "<tier>:status:<new>")`. The spec-change worker mostly
*creates* items at `:status:todo` and *deprecates* retired ones; live status
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
| TIMELINE | sprint schedule in execution order | yes |
| CONTROL | command/ops channel | yes |
| Current sprint | the active sprint's board | no |

Find them by title via `local.list_entries(...)`; **refresh a dashboard with
`edit_entry(dashboard_id, body=<rendered markdown>)`**. (GitHub pins cap at 3,
which is why there are three pinned; GitLab has no pinning and `pin_entry`
no-ops there.) Only touch dashboards when the route says to and when
`config.permissions.remote.post_dashboards` would allow it; otherwise leave them
and note it.

## Spec-change post (the request itself)

The triggering post carries `spec-change:<route>` plus a
`spec-change:status:<state>` label: `open, awaiting_approval, approved, done,
rejected`. As the worker, you advance its status with the same swap pattern:
moving it to `awaiting_approval` when you ask a question, to `done` when the
reconcile is enqueued (or leave that to the executor; record intent in
`plan.json`). Replies to the request go on this post as comments.

## Draft / ignore

Unlabeled entries are auto-moved to `draft` by `populate_defaults` and ignored.
Do not treat `draft` posts as work. `question` marks clarification threads.

## What the worker may write

Only through the `apply.py` reconcile script, and only what the route plans:
create work posts, edit bodies (incl. dashboards), swap status labels, comment,
close/delete retired posts. Never write the local cache directly; the remote is
the system's source of truth and the next poll re-syncs the cache from it.
