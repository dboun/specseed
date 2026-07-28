# spec route

## Short description

Create or evolve the project spec under `<specseed_dir>/spec/` and project its
work-breakdown onto the remote tracker. Normal outputs: STAGED spec edits under
`<specseed_dir>/storage/spec-change/<id>/spec/` (promoted to live `spec/` only on
approval) and `plan.json` in the request dir (the inspectable work-breakdown delta).
Plan-first, code-enforced: you write the outputs and STOP — never enqueue, never run
generated code, never pick the gate, never touch git or application code (adopt *reads*
code; it never writes it). The runtime reads `plan.json` + the staging dir and decides
in code whether the run is a proposal (needs human `APR-NNNN` approval) or the one
ungated clarification round. A `spec-change:<subroute>` label selects the subroute below.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/spec-change-protocol.md` | the spine: staged spec, `plan.json`, the approval gate, async clarification, idempotency, the tracking contract |
| `references/remote-posts.md` | the post/label model the work-breakdown lives in (tiers, status, type/difficulty, dashboards, body links) |
| `references/reply-protocol-spec.md` | reply form + the clarification-round format/discipline |
| `references/chat-mode.md` | when no runtime is present (chat): inputs, staged outputs, zip handoff |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|
| `requirements_generate_json.py` | SRS requirement tables → `reqs.json` |
| `requirements_analyze.py` | cycle / orphan / dangling-ref detection over `reqs.json` |
| `critical_path.py` | longest dependency chain over the ticket delta in `plan.json` |
| `sprint_pack.py` | cohesion-aware, dependency-respecting sprint packing of that delta |
| `dependencies_validate.py` | work-breakdown link check over `plan.json.creates` (parent tree + `Depends on:` DAG) |

## Subroutes

A `spec-change:<subroute>` label selects one. Read that file and follow it.

| Subroute | File | Fires when |
|----------|------|-----------|
| adopt | `spec_subroutes/adopt.md` | code present, `spec/` empty — recover the spec from the codebase (one-time onboarding) |
| adapt | `spec_subroutes/adapt.md` | create the first spec (cold start) OR a non-trivial spec change; the ONLY route that may reopen a settled doc |
| tweak | `spec_subroutes/tweak.md` | the smallest change: one req/priority/typo/status flip, single-doc + at most one work post |
| inject | `spec_subroutes/inject.md` | add manual/urgent work to the breakdown without changing settled spec |
| plan-next-sprint | `spec_subroutes/plan-next-sprint.md` | extend the spec forward into the next un-specced slice (append-only) |

If a subroute's localized work outgrows its boundary, it says so and escalates (tweak →
adapt; a plan-next-sprint slice that hits settled design → adapt).

## Hard rules (spec)

In addition to the SKILL-level rules:

- **Spec edits are STAGED, never written to live `spec/`.** Read live `spec/` for
  context; write each created/edited doc into
  `<specseed_dir>/storage/spec-change/<id>/spec/` at its live relative path
  (`scheduling/spec_change.spec_change_spec_dir(id)`).
- **No posts before approval.** A run that creates work or touches the spec creates
  NOTHING on the tracker. The runtime promotes staged spec and applies `plan.json` only
  after a human approves the `APR-NNNN` plan. Issues are then born `:status:todo`.
  The one ungated run is a request-scoped clarification round.
- **Remote mutations live in `plan.json`.** Use creates/edits/labels/comments/closes/
  deletes. The runtime applies them through the tracking contract. Generated `apply.py`
  is only an escape hatch with `"executor": "script"`.
- **Settled docs are soft-frozen.** Only adapt may reopen one (and re-settles on
  approval). The skill never writes `settled` itself; the runtime stamps it on approval.
