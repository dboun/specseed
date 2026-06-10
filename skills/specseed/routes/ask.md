# ask route

## Short description

Answer a question on the request post. **Read-only:** no spec edit, no code edit, no work
posts. Route the question to the right source (spec / code / tracker), read it, and answer
concisely as a comment on the post.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/reply-protocol-ask.md` | the answer + clarification format |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|
| `requirements_analyze.py` | only when the question is about requirement consistency |
| `dependencies_validate.py` | only when the question is about the dependency graph |

## Route the question to its source

| Question is about | Read |
|---|---|
| product, requirements, scope, design intent | `<specseed_dir>/spec/` (vision / SRS / SAD / SDD / `adr.csv`) |
| how the code behaves, where something lives, why it works a certain way | the target codebase |
| work status, what's planned, dependencies, sprint | the tracker posts (local cache) |

A question often spans two sources — read both, give one answer.

## Hard rules

- Read-only. Never edit spec, code, labels, or work posts. If the answer implies a
  change, say so and point at the route that would make it (`spec`, `inject`, …) — do not
  make it here.
- If the question itself is ambiguous, raise one clarification round
  (`references/reply-protocol-ask.md`) and stop.
