# operate route

## Short description

**NOT YET WIRED: if you are actually invoked on this route, STOP and say "OPERATE ROUTE
NOT IMPLEMENTED YET".** The contract below is written ahead of the runtime wiring.

Run an operational task USING the target codebase, where the point is doing something and
handling its result — not authoring a feature. operate owns the gated, real-world,
often-destructive work that impl deliberately does NOT: environment / playground setup and
verification; dependency changes and system installs; data mutation and migrations against
real stores; running an experiment or procedure and processing its outputs; and the
exploratory checking that is not a deterministic test — monkey testing and structured
use-case execution. Because this work is gated or irreversible, operate is the one route
that asks a human for approval before acting (`references/reply-protocol-operate.md`).

operate does NOT author features and does NOT write the deterministic automated tests
(unit, integration, e2e) — those are impl. operate EXECUTES and REPORTS.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/reply-protocol-operate.md` | how operate replies, incl. the approval ask before gated/destructive actions |
| `references/work-breakdown.md` | how operate work items are formed (the gated "run" item; isolate-gated-execution) |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## Inputs (read at runtime, before acting)

- The operate task post (body + comments): what to set up / run, the procedure, the
  expected outputs, and where results go. Treat a handoff pointer in the body (setup
  steps, "download this model", credentials to provision) as the spec for the run.
- `<specseed_dir>/spec/sad.md` → `## Project layout` + the relevant SDD: how the codebase
  is laid out and how to invoke it.
- Any `prep` issue's outputs this run consumes (code, scripts, fixtures already on
  primary).

## What operate does

- **Environment / playground setup:** stand up what a task needs (containers, services,
  test data), then VERIFY it works (smoke the setup) before declaring it ready.
- **Dependency / system changes:** install or change dependencies, system packages, or
  tooling.
- **Data mutation:** run migrations or data fixes against real stores.
- **Experiments / procedures:** run the procedure, capture the outputs, and process them
  (analysis, metrics) — transcribe results back to the post; if outputs feed a downstream
  issue, say where they live.
- **Exploratory checking:** monkey testing (odd, adversarial, random, boundary inputs;
  bad ordering; repeated/concurrent actions) and structured use-case execution (drive real
  user flows end to end). This is checking, not authoring.

## Approval + gates

- BEFORE any gated or destructive action, post an approval ask and PARK
  (`references/reply-protocol-operate.md`). Proceed only on approval; on decline, stop and
  report.
- Honor the action-class gates the runtime renders into your prompt (container,
  heavy_compute, network, deps, data_destructive, external_publish, outside_repo,
  secrets). The approval ask is how you surface one to the human.

## Hard rules

- **Execute + report, don't author.** No feature code, no deterministic test code (route
  that to impl); no spec edits (route that to spec). A run issue makes no source edits.
- **Findings become work, not silent fixes.** A monkey / use-case failure is reported and
  filed as a `type:bug` issue on the relevant ticket — operate never silently fixes and
  never expands scope.
- Never change workflow labels or grant approval yourself; the scheduler advances state.
  You request approval; the runtime resolves it.
