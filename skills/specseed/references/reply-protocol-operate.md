# Reply protocol — operate route

How the operate route replies while running an operational task. Builds on the shared base
for the form + question discipline; this file adds the operate-specific rules, including
the ONE thing only operate does: **make approval asks** before gated or destructive
operations.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/reply-protocol-base.md` | the shared form (modes, structured/natural) + question discipline |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## What operate replies

operate runs real operations (environment/playground setup, dependency changes, data
mutation, system installs, experiments, monkey/use-case execution), so it posts several
kinds of reply on the task post:

- **Progress note** (`main_body`): short status at the start/end of a real step, since
  runs are slow and a watching human wants to see movement.
- **Question** (`main_body` + `questions`): a blocking decision the task does not settle
  (shared question discipline; default to a suggestion).
- **Approval ask** (below): BEFORE any gated or destructive action.
- **Result** (`main_body`): what was set up / run, what came out, where the outputs are.

## Approval asks (operate-only)

Before an action that mutates data, installs/changes dependencies, provisions or tears
down a system/playground, or otherwise hits an action-class gate, operate STOPS and posts
an approval ask, then parks until a human approves. **This is the one route that authors
approval asks** — the others (spec, impl, review, ask) never do.

An `approval` section is its OWN reply, ALONE: no `main_body`, no `questions`, no second
`approval`. **One approval = one comment**, because in external mode it is consumed by a
REACTION on that one comment.

Structured form (specseed-UI) — the UI drives approval, NO reaction prompt:

```json
{
  "schema_version": 1,
  "user_facing_thread_entry": {
    "allow_regular_user_reply": true,
    "sections": [
      {
        "section_type": "approval",
        "content": {
          "approval_id": "APR-0001",
          "markdown": "What will run and what it touches. 1-3 sentences.",
          "justification_markdown": "Why it needs sign-off (it is gated/destructive). 1-2 sentences.",
          "post_approval_message": {"markdown": "Approved — running it now."}
        }
      }
    ]
  }
}
```

Natural form — **external** carries a reaction prompt:
```
**Approval needed — APR-0001**

Run the schema migration against the dev database (drops + recreates the `events` table).
Why: this mutates real data and cannot be undone without a restore.

React 👍 on this comment (or reply `approve APR-0001`) to proceed, 👎 to decline.
```
Natural form — **chat** surfaces the ask only, NO reaction prompt (chat can't approve a
live gate):
```
**Approval needed — APR-0001**

Run the schema migration against the dev database (drops + recreates the `events` table).
Why: this mutates real data and cannot be undone without a restore.
```

Rules:
- `approval_id` is a real `APR-NNNN` token from the runtime action-gate mechanism — NEVER
  invent one. If the run has no real token to cite, do not author an `approval` section;
  park on the action gate and let the runtime drive it.
- The skill never READS or judges the reaction; the runtime resolves it. On approval,
  proceed; on decline, stop and report.
