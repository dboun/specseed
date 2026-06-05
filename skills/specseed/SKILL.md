---
name: specseed
description: Non-interactive spec-change worker. Invoked by the specseed scheduler when a remote spec-change post is labeled spec-change:<route> (adopt, adapt, tweak, inject, plan-next-sprint). Edits the local spec under <specseed_dir>/spec/ and emits a Python script that projects the matching work-breakdown changes onto the remote tracker posts.
---

# specseed (spec-change worker)

Skill not meant to run in interactive shell, but rather be interactive through 
asking questions and stopping when needed. 
User answers through reprompting and it continues.
It runs one spec-change route against one request and stops. 
It does two things, every time, though some routes may have no spec-file edits:

1. **Edits the spec if the route calls for it** under `<specseed_dir>/spec/`
   (vision, SRS, SAD, SDD, `adr.csv`, `reqs.json`).
2. **Writes a Python script** under `<specseed_dir>/storage/spec-change/<id>/apply.py`
   that mutates the **remote** tracker posts (epics / tickets / issues, their
   labels and comments) to match the new spec, then **enqueues** that script for
   the executor.

It never runs the script itself, never touches git or branches, and never edits
application code (adopt *reads* code; it never writes it).

> **Path mapping.** Installed, the specseed tree lives under `<repo>/<specseed_dir>/`:
> `<specseed_dir>/spec/`, `<specseed_dir>/storage/`, `<specseed_dir>/specseed_target_src/`,
> `<specseed_dir>/skills/specseed/`. In this development repo those map to
> `src/target_facing/{spec,storage,specseed_target_src,skills}`. Paths below use the
> installed `<specseed_dir>/...` form.

## What this skill is NOT

The old interactive specseed did far more. This worker deliberately drops it:

- **No interactive Q&A.** No live question rounds with a human. Input is the
  spec-change post (title + body + comments), read from the **local** tracker.
  When you genuinely cannot proceed, you ask **asynchronously** (see "Async
  clarification") and stop, you do not block.
- **No configure / migrate / change-request / approve / bootstrap routes.** Those are gone.
- **No local `project_management/` tree, no assemble/validate/claim scripts.**
  The work breakdown lives as **remote posts**, not local folders or JSON.
- **No branch, merge, PR, or git work.** Not this skill's job.

## Invocation

The scheduler invokes this skill when a remote spec-change post carries a
`spec-change:<route>` label. The route is the suffix:

| Label | Route | File |
|-------|-------|------|
| `spec-change:adopt` | adopt | `routes/adopt.md` |
| `spec-change:adapt` | adapt | `routes/adapt.md` |
| `spec-change:tweak` | tweak | `routes/tweak.md` |
| `spec-change:inject` | inject | `routes/inject.md` |
| `spec-change:plan-next-sprint` | plan-next-sprint | `routes/plan-next-sprint.md` |

You are told which route and which spec-change post id. The post id is the
**request id**: it names the work dir (`<specseed_dir>/storage/spec-change/<id>/`) and
is the `post_id` on the queued task.

## The contract (every route)

Read `references/spec-change-protocol.md` first. The shape is always:

1. **Read context — local only.** Read the spec-change post + its comments and
   the current work posts from the **local** tracker (`resolve_local()` /
   `tracking_local.db`). Never poll the remote to plan; that is what the local
   cache is for. Read the current spec under `<specseed_dir>/spec/`.
2. **Decide + edit the spec if needed.** Apply the route's logic to
   `<specseed_dir>/spec/`.
   Persist any intermediate reasoning (the planned work-breakdown delta) as JSON
   in the request dir so the script and a human can inspect it.
3. **Emit the reconcile script.** Write `apply.py` into the request dir. It
   imports `resolve_remote()` and applies the post mutations through the tracking
   contract: `add_entry`, `edit_entry` (title/body), `add_entry_label`,
   `remove_entry_label`, `add_entry_comment`, `set_entry_open`/`set_entry_closed`,
   `delete_entry`, `ensure_label`. `edit_entry` rewrites post bodies — including the
   SCHEDULE dashboard, but NOT ROADMAP or Current sprint, which the runtime scheduler
   renders from the work posts. A status swap is `remove_entry_label` then
   `add_entry_label`. See the protocol for the canonical header and the
   per-provider notes (GitHub cannot hard-delete issues, so close instead).
4. **Enqueue it.** Call `scheduling/spec_change.enqueue_spec_change_run(...)`.
   The scheduler (`specseed_target_src/executing/`) drains the queue and runs the
   script as a permission-gated subprocess. Your job ends at the enqueue: do not
   run the script yourself.

## Doc style (spec prose only)

Spec prose must read as human-written reference text, not AI filler.

- **Density** (`references_ext/caveman.md`): lean, signal-dense, no padding.
- **Naturalness** (`references_ext/humanizer.md`): no promotional language, no
  rule-of-three, no `-ing` padding, no hedging. **Remove every em/en dash**
  (`—` / `–`); use a period, comma, colon, or parentheses.

Applies to human-readable prose: `vision.md`, SAD/SDD prose, epic/ticket/issue
bodies, ADR justifications. Does NOT apply to machine artifacts (`reqs.json`,
SRS requirement-table rows, frontmatter) or post labels. Specs are neutral
reference text: no injected voice, opinions, or first person.

## Approval before work (mandatory, every route)

You never put new work into a state an impl agent can claim. **A run is not
finished until it posts an approval request and parks it.** An issue becomes
claimable the instant it is `issue:status:todo`; so every issue you newly spec is
born **`issue:status:awaiting_approval`**, and you post one `APR-NNNN`
approval-request comment naming the batch, then swap the request to
`spec-change:status:awaiting_approval` and stop. A human approves (`approve
APR-NNNN` or 👍 on the issue) before any code work begins; the executor then flips
the issue to `todo`. This is a status gate, not a promise. It is **not**
epic-gating. Full contract + helpers in `references/spec-change-protocol.md`
("Approval gate (APR-NNNN)").

## Async clarification (the only "question" path)

This worker cannot interview a human live. When a request is too ambiguous to
proceed safely:

1. Make the spec edits you ARE confident about (if any), or none.
2. In `apply.py`, the remote action is a **comment** on the spec-change post
   stating exactly what you need, plus adding the label
   `spec-change:status:awaiting_approval`.
3. Enqueue as normal and stop. The human answers on the remote; the next poll
   re-triggers this route with their reply in the post comments.

Do not guess past a material ambiguity. A focused async question beats a wrong
spec.

## Hard rules

- **Local truth for reading, remote truth for the system.** You read the local
  cache to plan; the remote is the system's source of truth, so every change you
  intend must go into `apply.py`, never applied to the local DB directly.
- **Spec edits are local files; work-breakdown changes are remote posts.** Keep
  the two outputs separate and consistent.
- **Stay within the tracking contract.** Mutate the remote only through the
  `resolve_remote()` tracker's methods; never reach around it. Every method
  returns a `TrackingResult(ok, error, data)`; the script must check `ok` and
  fail loudly. The one provider gap: GitHub issues cannot be hard-deleted, so
  use `set_entry_closed` there (`delete_entry` is fine on local and GitLab).
- **One request, one run.** Do the route, write the script, enqueue, stop.
- **No work without approval.** New issues are created `:status:awaiting_approval`,
  never `:status:todo`. Every run that creates issues posts an `APR-NNNN` request
  and parks the request `awaiting_approval`. You never release claimable work
  yourself. (See "Approval before work".)

## Action gates (awareness only)

The implementation agent honors config-driven **action-class gates**
(`permissions.agents` in `configuration.json`: container, heavy_compute, network,
deps, data_destructive, external_publish, outside_repo, secrets — each `block` /
`surface` / `auto` / `require_human_approval`). They fire mid-implementation, not
here. You do NOT evaluate or enforce them. But when an issue you spec obviously
demands a gated action (a deploy, a destructive migration, a new dependency), say so
in the issue body so the human reading the plan is not surprised when the impl agent
parks for approval. The runtime renders the live policy into the impl prompt; the
authoritative list lives in `specseed_target_src/executing/permissions.py`.
