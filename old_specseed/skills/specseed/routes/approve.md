# Approve mode (resolve human-approval gates)

The human-facing other half of HITL. The impl agent **parks** gated work — it writes
an approval request to `.specseed/project_management/issues/<id>/approval.md`, sets the
issue `awaiting_approval`, and moves on (see `templates/CLAUDE_template.md` "Operating
policy"). This mode lets a human walk those pending requests and resolve them — locally
(spin up an agent, say "next thing needing approval"), or driven from the mirror's
`approve`/`reject`/`hold` verbs (which invoke this same `approvals_resolve.py` logic
headless — no model). Remotely the verb can be commented **on the CONTROL issue** (any
gate, addressed by `APR-NNNN`) **or directly on the work issue** that carries the `🔔`
(its own gate; the `APR-NNNN` is optional when the issue has a single open gate). Either
way the decision lands in the same resolver — see `references/remote.md` "Command channel".

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
   (records: `issue`, `apr`, `n`, `summary`, `kind`, `why`, `options`). Each gate carries
   a global `APR-NNNN` id (`apr`) — the stable, human-typeable handle a person uses to
   address it (`approve APR-NNNN`); `A<N>` stays the per-issue in-file anchor.
3. None pending → tell the user "no open approvals" and stop. (Also mention any issues
   sitting in `awaiting_approval` with NO open `approval.md` entry — that's an
   inconsistency worth flagging, not resolving blindly.)

## Stage 2: Present each request faithfully

For each open request (walk in `issue` then `A<N>` order; address each by its `APR-NNNN`
id when a person names one), read the full entry from the issue's `approval.md` and show
the human everything they need to decide **without looking anything up**:

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

You do the judgment (Stages 1–2); the **write is a script** —
`approvals_resolve.py` is the single, deterministic mutation path (same code the
remote CONTROL `approve`/`reject` verbs run). It appends the `## Resolved A<N>`
marker (never edits the original question), flips `issues.json` status per the gate
kind, handles the claim, runs the re-renders, and honours the last-open-gate rule.
Address the gate by its `APR-NNNN` id:

```bash
python .specseed/scripts/core/approvals_resolve.py <APR-NNNN> approve|reject|hold \
    [--note "..."] [--option A] [--wont-do] [--run-verify]
```

What the script does (so you know what to expect — don't hand-edit `issues.json`):

| Decision | gate kind | `issues.json` status | Claim |
|---|---|---|---|
| **approve** | action (`gate:*` / `run-action` / `handoff` / `git-conflict`) | `todo` → next agent re-claims + resumes on the existing branch | cleared |
| **approve** | completion (`entity-approval`) | `done` (+ re-assembles so the ticket rolls up) | cleared |
| **reject** | action | `blocked` (default; pass `--wont-do` when the gated action WAS the issue's whole point → `wont_do`) | cleared |
| **reject** | completion | `in_progress` (changes requested) — NOT `todo`; the issue is finished work bouncing back | kept |
| **hold** | any | `blocked` | kept |

- A **handoff** the human says they completed: pass `--run-verify` so the script runs
  the gate's `Verify:` command FIRST and refuses to approve (non-zero exit, gate left
  open) if it fails — don't trust the toggle. On pass (or no `Verify:`), it approves →
  `todo`. The sidecar dir stays in place (record + reuse); do not delete it.
- A **run-action** the human executed: record the results they report into a new
  `step_reports/<X>_run-<desc>.md` BEFORE you resolve, then `approve` → the agent
  consumes the outputs. No source edits here.
- Multiple open `A<N>` on one issue → run the script once per gate; it only flips the
  issue status once the **last** open entry is resolved (it reports `flipped: false`
  with the open siblings until then).

## Stage 4: Report

The script already re-ran `approvals_render.py` (drops resolved entries from the
index) and, on a completion close, `tickets_assemble.py` + `roadmap_render.py` (ticket
rollup). You don't re-run them. If the mirror is on, the next runner reconcile pass
re-projects status onto the github issue and the CONTROL/PR thread (see
`references/remote.md`).

Tell the user, tersely: what was resolved, what each issue moved to, what's still
pending (if the user stopped a walk early).

## Ambiguity guard

Never guess a decision. If a reply is unclear ("maybe", "looks risky"), do NOT resolve
— leave the entry open, note the clarifying question (in chat, or as a threaded comment
remotely), and move to the next. A gate stays parked until the human is unambiguous.
