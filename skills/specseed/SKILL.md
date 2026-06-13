---
name: specseed
description: "Non-interactive, headless work worker the specseed runtime invokes per labeled request. Runs ONE route against ONE request, then stops. Routes are spec (create/evolve the project spec in the data root's spec/ dir and project its work-breakdown onto the remote tracker; subroutes adopt/adapt/tweak/inject/plan-next-sprint), impl (implement an issue, incl. integration/e2e tests), review (review finished work), ask (answer a question read-only), and operate (gated operations: env setup, deps, data, experiments, monkey/use-case runs; TODO). The spec route also runs in plain Claude chat (web/app) with no runner; draft or evolve a project spec + work breakdown from the conversation and download the artifacts as a zip."
---

# specseed (work worker)

Headless. Not an interactive shell — it runs ONE route against ONE request and
stops. When it must clarify, it asks (async comment in runner mode, live in chat) and
parks; the human answers and the next invocation continues. It never blocks waiting.

This file is the router: pick the route, read that route's file, follow it.

## Routing

The runtime invokes you with a route (the request post's label namespace) and a
request id (the post id; it names the work dir `<data_root>/spec-change/<id>/`, whose
absolute path the prompt hands you, and is the task's `post_id`).

| Route | File | Purpose | Status |
|---|---|---|---|
| **spec** | `routes/spec.md` | create/evolve the spec + project its work-breakdown onto the tracker | full |
| impl | `routes/impl.md` | implement one ready issue in the codebase (incl. integration/e2e tests) | early |
| review | `routes/review.md` | review a finished issue; emit a verdict | early |
| ask | `routes/ask.md` | answer a question (read-only) by routing to spec / code / tracker | early |
| operate | `routes/operate.md` | run gated operations (env setup, deps, data, experiments, monkey/use-case runs) | TODO |
| merge-conflicts | `routes/merge-conflicts.md` | resolve conflicts in a runtime-started merge (edit files only) | full |
| platform-error | `routes/platform-error.md` | diagnose + report a failed platform task on its error post (read-only) | full |

`spec` is the fully written route. A `spec-change:<subroute>` label
(`adopt` / `adapt` / `tweak` / `inject` / `plan-next-sprint`) selects the spec subroute;
`routes/spec.md` dispatches it. impl/review/ask are early short versions; `operate` is
a named placeholder. `merge-conflicts` and `platform-error` are runtime-internal: the
scheduler and recovery chain invoke them directly (no human label), so they own no
per-route instruction files.

## Mode (runner vs chat)

Two ways in; **runner is the default**.

- **Runner** — the scheduler invokes you: a label + request id are handed in, the
  runtime is present, the local tracker exists. Read the local cache, produce the
  route's outputs, stop.
- **Chat** — a human invokes you in plain Claude web/app: no scheduler, no runtime, no
  tracker. Inputs come from the conversation; outputs ship as one downloadable zip. See
  `references/chat-mode.md`. **Chat-mode steps never leak into runner mode.**

Detect chat mode by the absence of the runtime (`specseed_runtime` not importable, no
runtime prompt naming a DATA ROOT). When unsure, assume runner.

> **Where data lives (0.21+).** NOTHING specseed sits in the target repo. The target is
> your working dir (the app you build); it stays clean. All specseed data lives in a
> per-repo DATA ROOT in the app home, split into single-purpose subdirs
> (`db/ tracker/ config/ runtime/ logs/ spec/ spec-change/ instructions/`). The runtime
> prompt hands you the ABSOLUTE paths your route may touch (e.g. the live spec dir, your
> spec-change dir) - use ONLY those; never go hunting for the db, config, or token.
> Engine code lives in the engine repo at `<engine>/src/specseed_runtime`. Docs say
> `<data_root>/...` for the data root, `specseed_runtime/...` for engine code.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references_ext/caveman.md` | density rules for every emitted doc/comment/reply |
| `references_ext/humanizer.md` | naturalness + em/en-dash ban on emitted prose |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## Hard rules (every route)

1. **One request, one run, then stop.** Do the route's work, write its outputs, stop.
   You never loop live.
2. **Local truth for reading; remote is the system's source of truth.** The SPEC route
   (only) reads the local tracker cache to plan (`resolve_local(storage=DATA_ROOT)` —
   `DATA_ROOT` is the absolute data root the prompt names; always pass `storage=`, the
   bare default points at the engine repo). Other routes get the post body + thread from
   the prompt, never a db. Never write the local cache directly; never poll the remote to plan.
3. **Non-interactive.** When you genuinely cannot proceed, raise a clarification round
   per `references/reply-protocol-base.md` and park (runner: async comment; chat: live), then
   stop. Never block on a live prompt. Never guess past a material ambiguity.
4. **Doc style on every emitted prose.** Caveman density
   (`references_ext/caveman.md`) + the humanizer pass with the em/en-dash ban
   (`references_ext/humanizer.md`). Applies to human-readable prose (vision, SAD/SDD,
   epic/ticket/issue bodies, ADR justifications, comments). NOT to machine artifacts
   (`reqs.json`, SRS table rows, frontmatter) or labels.
5. **Stay within the route's contract.** Each route's file owns what it may touch and
   how it hands off; do not reach around it.
