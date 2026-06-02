# Approve mode (resolve human-approval gates)

The human-facing other half of HITL. The impl agent **parks** gated work — it writes
an approval request to `.specseed/project_management/issues/<id>/approval.md`, sets the
issue `awaiting_approval`, and moves on (see `templates/CLAUDE_template.md` "Operating
policy"). This mode lets a human walk those pending requests and resolve them — locally
(spin up an agent, say "next thing needing approval"), or driven from the mirror's
CONTROL `approve`/`reject` verbs (which invoke this same logic headless).

Read-only on code. Touches only `approval.md` files + issue status in `issues.json`.

## When it fires

- `/specseed approve` (no arg) → **walk** all pending requests.
- "next thing needing approval" / "handle approvals" / "go through approvals" → walk.
- `approve <ID> [opt] [note]` / `reject <ID> <note>` / `hold <ID> <note>` → **direct
  resolve** one issue, no walk.

This is technical/ops, not spec work — no question-protocol rounds, no themes. Just
surface each request faithfully and apply the human's decision.

## Stage 1: Gather pending

1. Run `python .specseed/scripts/core/approvals_render.py` to refresh the index.
2. Read `.specseed/project_management/APPROVALS.md` (human view) / `approvals.json`
   (records: `issue`, `n`, `summary`, `kind`, `why`, `options`).
3. None pending → tell the user "no open approvals" and stop. (Also mention any issues
   sitting in `awaiting_approval` with NO open `approval.md` entry — that's an
   inconsistency worth flagging, not resolving blindly.)

## Stage 2: Present each request faithfully

For each open request (walk in `issue` then `A<N>` order), read the full entry from the
issue's `approval.md` and show the human everything they need to decide **without
looking anything up**:

- **What** the agent needs / is about to do, and **why it's gated** (which category).
- **Risks / blast radius** and **links / details** (paths, URLs, expected cost/runtime).
- **Options** + the agent's recommendation.
- For a **run-action**: the **self-run instructions** verbatim (the exact commands +
  expected runtime/output) — this is a thing the human (or only the human) runs.
- For a **handoff** (the human must do something out of band before the agent can
  proceed): point them at the sidecar **`Handoff:` dir** — read its `README.md` to them
  (or summarize) and name any helper script there. The gate body is just a summary; the
  dir holds the steps.

Then ask for the decision. Interactive local session → ask in chat (a one-shot
`AskUserQuestion` popup is fine here — the human is present by definition). Headless
(CONTROL verb) → the verb already carries the decision; skip the ask.

## Stage 3: Apply the decision

Three terminal outcomes. In every case, **append** to the issue's `approval.md` (never
edit the original question):

```markdown
## Resolved A<N> (<ISO date>): <approve|reject|hold> — <note / decided option>
```

Then set the issue `status` in `issues.json` per the decision (the agent that parked it
kept its claim; you are a *different* actor, so you may move it — this is the
no-self-bypass rule working as intended):

| Decision | `issues.json` status | Claim |
|---|---|---|
| **approve** (proceed) | `todo` | clear `claimed_by`/`claimed_at` → next agent re-claims and resumes on the existing branch |
| **reject** (don't do it) | `wont_do` if the issue's whole point was the gated action; else `blocked` with a note to rescope | clear claim |
| **hold** (not now) | `blocked` | keep or clear per the note |

- A **run-action** the human executed: record the results they report into a new
  `step_reports/<X>_run-<desc>.md`, mark the gate `approve`d, set the issue back to
  `todo`/`in_progress` so the agent can consume the outputs. No source edits here.
- A **handoff** the human says they completed: if the gate carries a **`Verify:`**
  command, **run it first** and only `approve` if it passes — a failing/empty verify
  means the action isn't actually done, so leave the gate open and tell them what's still
  missing (don't trust the toggle). On pass (or no `Verify:`), `approve` → `todo` so the
  next agent resumes. The sidecar dir stays in place (record + reuse); do not delete it.
- A **completion gate** (`approval_required` on a finished issue/ticket, OR a
  **code-review sign-off** — `Kind: entity-approval` written by `review_gate.py` when a
  review lands in `awaiting_approval`): approve → `done` (+ re-assemble so the ticket
  rolls up); reject / "request changes" → `in_progress` (changes requested). This is the
  completion path, NOT the generic `approve → todo` row above — a reviewed-and-approved
  issue is finished, don't send it back to `todo`.
- Multiple open `A<N>` on one issue → resolve each; only flip the issue status once the
  **last** open entry is resolved.

## Stage 4: Re-render + report

After each resolution (or once, at the end of a walk):
```bash
python .specseed/scripts/core/approvals_render.py        # drop resolved entries from the index
python .specseed/scripts/core/issues_assemble.py         # if a status changed
python .specseed/scripts/core/tickets_assemble.py        # if a completion gate closed an issue
python .specseed/scripts/core/roadmap_render.py
```
If the mirror is on, the next runner reconcile pass re-projects status onto the github
issue and the CONTROL/PR thread (see `references/remote.md`).

Tell the user, tersely: what was resolved, what each issue moved to, what's still
pending (if the user stopped a walk early).

## Ambiguity guard

Never guess a decision. If a reply is unclear ("maybe", "looks risky"), do NOT resolve
— leave the entry open, note the clarifying question (in chat, or as a threaded comment
remotely), and move to the next. A gate stays parked until the human is unambiguous.
