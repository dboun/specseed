# Reply protocol — ask route

How the ask route replies when answering a question. Builds on the shared base for the
form + question discipline; this file adds the ask-specific rules.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/reply-protocol-base.md` | the shared form (modes, structured/natural) + question discipline |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## The ask reply is the answer

A normal ask reply is a single `main_body`: the answer, posted as a comment on the request
post, concise, grounded in the source you read (spec / code / tracker). If the answer
implies a change, say which route would make it (`spec`, `inject`, `operate`, …) — do not
make it here (ask is read-only).

If the QUESTION ITSELF is ambiguous, post ONE `questions` round to disambiguate (shared
question discipline), park for the reply, and stop — then answer on the next invocation.

## No approval asks here

The ask route is read-only and NEVER makes an approval ask: it neither changes state nor
runs anything that needs sign-off. So this route has no `approval` section.
