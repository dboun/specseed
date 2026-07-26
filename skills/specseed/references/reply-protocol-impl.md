# Reply protocol — impl route

How the impl route replies while implementing an issue. Builds on the shared base for the
form + question discipline; this file adds the impl-specific rules.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/reply-protocol-base.md` | the shared form (modes, structured/natural) + question discipline |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## Replying is allowed BEFORE you are done

Unlike a spec run, impl is a long job, so it may post replies mid-work — as comments on
the issue post — not only a final one:

- **Progress note** (`main_body`): a short status when a long phase starts/finishes, so a
  watching human sees movement. Keep it sparse — not every step.
- **Blocking question** (`main_body` + `questions`): when you genuinely cannot proceed
  without a human decision (ambiguous acceptance criterion, a choice the issue/spec does
  not settle). Post the round, set the issue's status so the runtime parks it for input,
  and STOP. The next invocation resumes with the answer. Use the question discipline —
  default to a suggestion, don't ask the obvious.
- **Completion note** (`main_body`): a brief summary of what landed when you finish.

Keep all of these terse. The bulk of the work is code, not commentary.

## No approval asks here

The impl route NEVER makes an approval ask. Code review and merge gates are
**code-owned**: the runtime runs the review route and resolves the gate from its verdict
(`executing/advance.py`); you never request or grant approval. Action-class gates fire
mid-implementation and are also runtime-driven — you PARK on them, you do not post an
approval ask. **Work that needs an approval ask (dependency changes, data mutation, system
install, environment/playground setup, running an experiment) is not impl work — it is an
`operate` work item** (see `references/work-breakdown.md`). So this route has no
`approval` section.
