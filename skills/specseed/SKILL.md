---
name: specseed
description: "Non-interactive, headless work worker the specseed runtime invokes per labeled request. Runs ONE route against ONE request, then stops. Routes are spec (create/evolve the project spec under <specseed_dir>/spec/ and project its work-breakdown onto the remote tracker; subroutes adopt/adapt/tweak/inject/plan-next-sprint), impl (implement an issue, incl. integration/e2e tests), review (review finished work), ask (answer a question read-only), and operate (gated operations: env setup, deps, data, experiments, monkey/use-case runs; TODO). The spec route also runs in plain Claude chat (web/app) with no runner; draft or evolve a project spec + work breakdown from the conversation and download the artifacts as a zip."
---

# specseed (work worker)

Headless. Not an interactive shell — it runs ONE route against ONE request and
stops. When it must clarify, it asks (async comment in runner mode, live in chat) and
parks; the human answers and the next invocation continues. It never blocks waiting.

This file is the router: pick the route, read that route's file, follow it.

## Routing

The runtime invokes you with a route (the request post's label namespace) and a
request id (the post id; it names the work dir
`<specseed_dir>/storage/spec-change/<id>/` and is the task's `post_id`).

| Route | File | Purpose | Status |
|---|---|---|---|
| **spec** | `routes/spec.md` | create/evolve the spec + project its work-breakdown onto the tracker | full |
| impl | `routes/impl.md` | implement one ready issue in the codebase (incl. integration/e2e tests) | early |
| review | `routes/review.md` | review a finished issue; emit a verdict | early |
| ask | `routes/ask.md` | answer a question (read-only) by routing to spec / code / tracker | early |
| operate | `routes/operate.md` | run gated operations (env setup, deps, data, experiments, monkey/use-case runs) | TODO |

`spec` is the fully written route. A `spec-change:<subroute>` label
(`adopt` / `adapt` / `tweak` / `inject` / `plan-next-sprint`) selects the spec subroute;
`routes/spec.md` dispatches it. impl/review/ask are early short versions; `operate` is
a named placeholder.

## Mode (runner vs chat)

Two ways in; **runner is the default**.

- **Runner** — the scheduler invokes you: a label + request id are handed in, the
  runtime is present, the local tracker exists. Read the local cache, produce the
  route's outputs, stop.
- **Chat** — a human invokes you in plain Claude web/app: no scheduler, no runtime, no
  tracker. Inputs come from the conversation; outputs ship as one downloadable zip. See
  `references/chat-mode.md`. **Chat-mode steps never leak into runner mode.**

Detect chat mode by the absence of the runtime (`specseed_runtime` not importable, no
`<specseed_dir>/storage/`). When unsure, assume runner.

> **Path mapping.** The engine is never copied into the target. A target holds only
> `<specseed_dir>/spec/`, `<specseed_dir>/storage/`, and a version marker (default
> `<specseed_dir>` = `<target>/.specseed`). Engine code lives in the engine repo at
> `<engine>/src/specseed_runtime` and `<engine>/skills`. In this dev repo the target IS
> this repo, so `<specseed_dir>/{spec,storage}` map to repo-root `{spec,storage}/`.
> Docs use the `<specseed_dir>/...` form for target data, `specseed_runtime/...` for
> engine code.

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
2. **Local truth for reading; remote is the system's source of truth.** Read the local
   tracker cache to plan (`resolve_local(storage=<specseed_dir>/storage)` — always pass
   `storage=`; the bare default points at the engine repo, not the target). Never write
   the local cache directly; never poll the remote to plan.
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
