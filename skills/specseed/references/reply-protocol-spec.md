# Reply protocol — spec route

How the spec route (and its subroutes) replies. Builds on the shared base for the form +
question discipline; this file adds the spec-specific rules.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/reply-protocol-base.md` | the shared form (modes, structured/natural) + question discipline |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## When the spec route replies

The ONLY user-facing reply a spec run posts is a **clarification round** — `main_body` +
a `questions` round — when it cannot proceed safely without an answer. Compose it per the
shared base.

- **Runner:** post the round as comment(s) on the spec-change request post, set the
  request `spec-change:status:awaiting_input`, and STOP. The next poll re-triggers the
  route with the human's reply. Record the round under `plan.json.questions` so a
  re-trigger never re-asks (memory cadence below).
- **Chat:** ask live, continue.

## No approval asks here

The spec route NEVER makes an approval ask. The plan-first **APR gate is runtime-owned**:
the runtime reads `plan.json` + the staging dir, posts the `plan_summary` + `APR-NNNN`
itself, and resolves the human's approval in code. The skill writes `plan.json` (incl.
`plan_summary`, `apr`) and stops; it does not author, post, or read an approval reply. So
this route has no `approval` section.

## Memory cadence (`plan.json`)

A spec run is fresh each invocation, so durable question outcomes live in the request dir.
Under a `questions` key:
- Round with overrides/free-text → write locked outcomes only (not the transcript):
  `"questions": {"<stage>/round-<N>": {"1": "B", "2": "free: keep auth+session together"}}`
- Pure-OK first try → skip the entry.
- Auto-declared choices → record so a later pass sees the assumption:
  `"auto_declared": ["SQLite for persistence (90%+, not contradicted)"]`
- At each stage boundary → a one-line-per-decision rollup; it's the resumable state.

Because runner mode parks between rounds, read `plan.json` back before posting a new round
so you never re-ask an answered question.
