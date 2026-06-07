---
name: specseed
description: Non-interactive spec-change worker (form-free; clarifies via the questions protocol). In runner mode the specseed scheduler invokes it when a remote post is labeled spec-change:<route> (adopt, adapt, tweak, inject, plan-next-sprint); it edits the local spec under <specseed_dir>/spec/ and emits a Python script projecting the work-breakdown onto the remote tracker. Also runs in plain Claude chat (web/app) with no runner: use it to draft or evolve a project spec + work breakdown from the conversation and download the artifacts as a zip to add to a repo or resume in Claude Code.
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

## Mode (runner vs chat)

Two ways in. **Runner mode is the default and the rest of this doc assumes it.**

- **Runner mode** (the scheduler invokes you): a `spec-change:<route>` label + a
  request id are handed in, the runtime is present. Read the local cache, edit
  `spec/`, emit `plan.json` + `apply.py`, enqueue, stop. Everything below applies.
- **Chat mode** (a human invokes you in plain Claude web/app, no scheduler, no
  runtime): inputs come from the conversation, outputs are bundled into one
  **downloadable zip** offered early and refreshed — never dumped in output — so
  the human can drop them into a repo or resume in Claude Code. No enqueue, no
  `apply.py` run. See `references/chat-mode.md`. **Do not let chat-mode steps leak
  into runner mode.**

Both modes stay non-interactive form-wise: no popup forms. When you must clarify,
use the questions protocol (runner: async comment; chat: live questions).

> **Path mapping.** The engine is never copied into the target. A target holds only
> `<specseed_dir>/spec/`, `<specseed_dir>/storage/`, and a version marker (default
> `<specseed_dir>` = `<target>/.specseed`). The engine code (`specseed_runtime/`,
> `skills/`) lives in the engine repo at `<engine>/src/specseed_runtime` and
> `<engine>/skills`. In this development repo the target IS this repo, so
> `<specseed_dir>/{spec,storage}` map to repo-root `{spec,storage}/`. Paths below use
> the `<specseed_dir>/...` form for target data and `specseed_runtime/...` for engine code.

## What this skill is NOT

The old interactive specseed did far more. This worker deliberately drops it:

- **No interactive Q&A.** No live question rounds with a human. Input is the
  spec-change post (title + body + comments), read from the **local** tracker.
  When you genuinely cannot proceed, you ask **asynchronously** (see "Async
  clarification") and stop, you do not block.
- **No configure / migrate / change-request / approve / bootstrap routes.** Those are
  gone (bootstrap is folded into `adapt` cold-start). Configure/migrate/approval are
  runtime concerns now, not skill routes.
- **No local `project_management/` tree, no assemble/validate/claim scripts.** The work
  breakdown lives as **remote posts**, not local folders or JSON. (The skill DOES carry
  deterministic helper scripts — `skills/specseed/scripts/` — but they only compute over
  the local `spec/` files + `plan.json`: reqs generation, cycle detection, critical
  path, sprint packing. See "Skill scripts".)
- **No branch, merge, PR, or git work.** Not this skill's job.

## Skill scripts

`skills/specseed/scripts/` holds stdlib-only python the route runs while planning. They
operate ONLY on local `spec/` files and the request's `plan.json` — never on the remote
or the local tracker DB. Use them instead of hand-computing what they own:

- `requirements_generate_json.py` — SRS requirement tables -> `reqs.json`.
- `requirements_analyze.py` — cycle / orphan / dangling-ref detection over `reqs.json`.
- `critical_path.py` — longest dependency chain over the ticket delta in `plan.json`.
- `sprint_pack.py` — cohesion-aware, dependency-respecting sprint packing of that delta.

They are helpers, not the contract: the two outputs are still the spec edits +
`apply.py`. Work-item type and difficulty are carried as `type:<kind>` and
`difficulty:<level>` **labels** on the posts (`references/remote-posts.md`).

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
   the current work posts from the **local** tracker
   (`resolve_local(storage=<specseed_dir>/storage)` / `tracking_local.db`).
   Always pass `storage=` — the bare default points at the engine repo, not the
   target. Never poll the remote to plan; that is what the local cache is for.
   Read the current spec under `<specseed_dir>/spec/`.
2. **Decide + edit the spec if needed.** Apply the route's logic to
   `<specseed_dir>/spec/`.
   Persist any intermediate reasoning (the planned work-breakdown delta) as JSON
   in the request dir so the script and a human can inspect it.
3. **Emit the reconcile script.** Write `apply.py` into the request dir. It
   imports `resolve_remote()` and applies the post mutations through the tracking
   contract: `add_entry`, `edit_entry` (title/body), `add_entry_label`,
   `remove_entry_label`, `add_entry_comment`, `set_entry_open`/`set_entry_closed`,
   `delete_entry`, `ensure_label`. `edit_entry` rewrites post bodies — including the
   SCHEDULE dashboard, but NOT ROADMAP or CURRENT SPRINT, which the runtime scheduler
   renders from the work posts. A status swap is `remove_entry_label` then
   `add_entry_label`. See the protocol for the canonical header and the
   per-provider notes (GitHub cannot hard-delete issues, so close instead).
4. **Enqueue it (runner mode).** A run that plans work or settles spec docs calls
   `scheduling/spec_change.enqueue_spec_change_propose(...)` — the runtime posts the
   plan summary + `APR-NNNN`, parks the request, and runs your `apply.py` only on
   approval (plan-first: nothing is created before then). A mechanical run that
   creates no work and settles no doc (e.g. a clarification round, a sprint label
   shuffle) calls `enqueue_spec_change_run(...)` instead — `apply.py` runs straight
   away. Either way the scheduler drains the queue under permission gating; your job
   ends at the enqueue, never run the script yourself. **Chat mode:** skip the enqueue
   — bundle the artifacts into a zip and offer it for download (`references/chat-mode.md`).

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

**Plan-first: nothing is created on the tracker until the human approves the plan.**
A run that creates work (or settles spec docs) does not create posts — it writes the
plan, then enqueues a **proposal** and stops. The runtime posts a human-readable
`plan_summary` + one `APR-NNNN` request on the spec-change post and parks it
`spec-change:status:awaiting_approval`. A human approves (`approve APR-NNNN` or 👍 on
the request) or rejects (`reject` / 👎). Only on approval does the runtime settle the
docs and run your `apply.py`, which creates the epics/tickets/issues — issues born
`issue:status:todo` (the plan approval was the gate; no per-issue gate). Full contract
+ helpers in `references/spec-change-protocol.md` ("Approval gate (APR-NNNN)").

## Async clarification (the question path in runner mode)

In runner mode this worker cannot interview a human live, but it is not limited to
one question. When a request is too ambiguous to proceed safely, post a **clarification
round** (the confidence/suggestion format in `references/question-protocol.md` — a
round may carry several questions), then park and wait. The headless rule is "one
round, then stop," not "one question."

1. Make the spec edits you ARE confident about (if any), or none.
2. In `apply.py`, the remote action is the question round posted as a **comment (or
   comments)** on the spec-change post, plus adding the label
   `spec-change:status:awaiting_approval`. Record the round in `plan.json` (the
   `questions` key) so a re-trigger does not re-ask.
3. Enqueue as normal and stop. The human answers on the remote (a one-word `OK` takes
   all your suggestions); the next poll re-triggers this route with their reply in the
   post comments.

Do not guess past a material ambiguity. A focused, well-suggested round beats a wrong
spec. Chat mode asks the same round live (`references/chat-mode.md`).

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
- **No posts before approval.** A run that creates work or settles docs creates
  NOTHING on the tracker — it enqueues a **proposal** (`enqueue_spec_change_propose`),
  and the runtime runs your `apply.py` only after a human approves the `APR-NNNN`
  plan. Issues are then born `:status:todo`. You never create work posts or release
  claimable work yourself. (See "Approval before work".)

## Action gates (awareness only)

The implementation agent honors config-driven **action-class gates**
(`permissions.agents` in `configuration.json`: container, heavy_compute, network,
deps, data_destructive, external_publish, outside_repo, secrets — each `block` /
`surface` / `auto` / `require_human_approval`). They fire mid-implementation, not
here. You do NOT evaluate or enforce them. But when an issue you spec obviously
demands a gated action (a deploy, a destructive migration, a new dependency), say so
in the issue body so the human reading the plan is not surprised when the impl agent
parks for approval. The runtime renders the live policy into the impl prompt; the
authoritative list lives in `specseed_runtime/executing/permissions.py`.
