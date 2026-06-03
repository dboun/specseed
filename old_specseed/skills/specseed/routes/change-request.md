# Change-request mode (CR conductor)

Drives ONE filed spec-change request (`CR-NNNN`) to a terminal state. **Headless** — the
runner invokes it per relay turn; no interactive human in the loop. Input arrives as
comments on the CR's remote issue, relayed one turn at a time. Output is this turn's text;
the runner posts it back as a comment.

**This is the async, approval-gated wrapper that DRIVES a spec operation.** It does NOT
reimplement that operation and does NOT modify it. A human at the laptop still uses
`/specseed adapt` directly (unchanged) — see "Boundary" at the end.

**Two kinds, same rails.** A spec request (`CR-NNNN`) carries a `kind`:
- **`kind:change`** (default — what this route has always done): change/extend a SETTLED
  spec. Branch-isolated on `cr/<CR-ID>`; on approval runs **adapt's** stages by reference;
  terminal = `respec_complete` → the runner MERGES the branch.
- **`kind:bootstrap`**: COLD-START — no spec exists yet (a `bootstrap`-labeled remote issue
  on an unspecced repo). **No branch** (nothing settled to isolate); on approval runs
  **bootstrap's** stages (see `routes/bootstrap.md` "Remote-driven variant") to write the
  first `.specseed/` tree straight onto the working branch; terminal = `respec_complete` →
  the runner FINALIZES (flips done + the mirror's `initialized`; no merge).

The conversation mechanics below (resume the session, clarify, plan, GATE on explicit
approval, `turn: human` between turns) are identical for both kinds. Where a step says
"adapt", read "adapt for change, bootstrap for cold-start". Read `cr.kind` on entry.

Load `references/question-protocol.md` before any user-facing turn. CR scope is adapt-grade,
so apply **anti-max-bias hard** (same posture as adapt).

## 1. Entry / invocation

`/specseed change-request <CR-ID>` (conceptual mode name). The new human comment is the
current user turn.

**Headless reality (Phase 3 relay):** under `claude -p`, user-invoked slash commands are
NOT available. The runner triggers this route with natural language that auto-matches the
specseed skill description, e.g. *"Use the specseed skill to handle spec-change request
CR-0001. New message from the user: <comment>."* — not a literal `/specseed change-request`.
Treat the slash form as the name of the flow, not the wire format.

On entry:
- Read the CR: `python .specseed/scripts/core/change_requests.py show <CR-ID>` → its
  `status`, `turn`, `request`, `log`, `branch`, `session_id`.
- **`kind:change`:** the runner already created + checked out `cr/<CR-ID>` off `dev` and
  switched to respec mode BEFORE invoking (Phase 3). Work on the already-checked-out branch.
  Do NOT create branches, switch branches, merge, or reset — the runner owns all git
  mode/branch transitions.
- **`kind:bootstrap`:** NO branch — you write onto the current working branch. Still do NOT
  create/switch branches or merge; just produce the `.specseed/` tree in place.
- Only ever touch THIS CR and the spec/work-layer files its change implies. Never claim
  issues, never run the normal work loop.

## 2. State is the session, not a machine

Each turn resumes the SAME `claude` session (`--resume <session_id>`), so the model already
remembers the whole conversation. Do NOT re-derive a state machine from disk. Continue the
conversation naturally from where it left off.

- **First turn** (no session yet): read the `Request` fresh, open the conversation.
- **Later turns**: the new comment is a reply in the ongoing thread; respond in context.

The CR `status`/`turn` fields exist for the RUNNER to branch on, not for you to reconstruct
intent — the session is the source of conversational truth.

## 3. Conversation

Use `references/question-protocol.md` (same rounds/format as adapt's reduced rounds).

- **Clarify.** Ask clarifying questions about the requested spec change: what changes, why
  now, what must NOT break. Post them as this turn's output (runner comments them). One round
  at a time, anti-max-bias hard, auto-skip obvious Qs.
- **Draft a plan** once enough is known. The plan = adapt's stage-3 delta-map style:
  - impact map (which `spec/` docs + req IDs added/changed/deprecated; whether any
    `settled: true` doc is touched),
  - which tickets/issues get created / revised / deprecated,
  - the **urgent issues that will jump the queue** (high-priority into the `in_progress`
    sprint after merge).
- **Ask for approval explicitly.** End the plan turn with: *"Reply `I approve` to proceed, or
  tell me what to change."* Recognize a clear approval in the human's NEXT comment.

## 4. Never proceed unapproved (THE GATE)

Do NOT mutate ANY `.specseed/spec/` or work-layer file until the human's turn is a clear
approval. This is the existing skill self-gate (decision #5) — there is **no separate
`approval.md` ceremony**; the question-protocol + this rule IS the gate.

Every non-terminal turn ends the same way:
1. append a one-line note to the CR log (`change_requests.py` `append_log`),
2. set CR `turn: human` (waiting on the user),
3. STOP. (Runner stays frozen on this CR until the next comment arrives.)

On ANY blocker, uncertainty, or in-flight conflict (see stage 5) — STOP and ask, never guess
through a spec change. `turn: human` is the universal "waiting" signal.

## 5. On approval → regenerate (run the operation's machinery)

**`kind:bootstrap`:** run **bootstrap's** stages instead — see `routes/bootstrap.md`
"Remote-driven (headless relay) variant". Produce the full `.specseed/` tree (spec + first
sprint) on the working branch, append a completion note, set CR `status: respec_complete`,
`turn: null`, STOP. The runner finalizes (flips `done` + the mirror's `initialized`); no
merge. The rest of this section is the `kind:change` path.

Only after a clear approval. Run adapt's existing stages BY REFERENCE — `routes/adapt.md`
stages 3-8. Do not duplicate their text; follow them:

- **Localize impact** (adapt stage 3) on the already-checked-out `cr/<CR-ID>` branch.
- **Patch docs in place** (adapt stage 5). Reopening a `settled` doc logs to `adr.csv` per
  adapt's rule. (Headless: the approval already covered the change; no second settled-doc
  prompt — but if the impact is materially WIDER than the approved plan, stop and re-confirm.)
- **Regenerate tickets/issues** (adapt stage 7) + run the **risk-detection & gating pass**
  (`references/work-breakdown.md`) over the new issues.
- **Sprint-assign the urgent issues high-priority into the `in_progress` sprint** — same
  placement `add_work.py` uses for high-priority items, so they sort to the top of the claim
  order once the branch merges.
- **Re-assemble + re-render** (adapt stage 6): issues → tickets → sprints, then validators,
  `tickets_analyze.py`, `roadmap_render.py`, `timeline_render.py`.
- **ADR append** (adapt stage 8) for any decision made.

**In-flight conflict.** If adapt's "Conflict handling" surfaces that a `done`/`in_progress`
ticket or issue is invalidated, do NOT resolve it autonomously. Carry the warning into THIS
conversation: set `turn: human`, post the conflict + options, STOP. Resume regeneration only
after the human decides. (No new conflict logic — adapt already surfaces it; the relay just
ferries it.)

All commits land on `cr/<CR-ID>` (the runner created it; you commit onto it). Do NOT merge —
that is the runner's approval gate. When regeneration is complete:
1. append a completion note to the CR log,
2. set CR `status: respec_complete`, `turn: null`,
3. STOP.

The runner sees `respec_complete` → merges `cr/<CR-ID>` → `dev` → flips CR `done` → exits
respec mode → resumes claiming (Phase 3).

## 6. On rejection

If the human's turn is a clear rejection ("reject", "cancel this", "never mind"):
1. write a one-line reason to the CR log,
2. set CR `status: rejected`, `turn: null`,
3. STOP.

Do NOT delete the branch or touch git — the runner deletes `cr/<CR-ID>` and `dev` never saw
the half-baked spec (Phase 3).

## 7. Session-id capture

The route does NOT manage the session id — the runner captures it from the `claude`
invocation and stores it in `CR.session_id`, then resumes with it (Phase 3, INDEX section B).
The route's only obligation: be safe to resume (no reliance on transient process state; all
durable facts live in the CR folder + the conversation).

## 8. Mutating CR state (how)

The conductor writes only CR `status` / `turn` / log — never `session_id` / `branch` /
`comment_cursor` (runner-owned). `change_requests.py`'s CLI is read-only (`show`/`list`), so
write via the helpers from `.specseed/scripts/`:

```bash
python3 -c "import sys; sys.path.insert(0,'.specseed/scripts'); \
from core import change_requests as cr; r=cr.find_root(); \
cr.append_log(r,'CR-0001','asked clarifying Qs'); cr.set_turn(r,'CR-0001','human')"
```

`set_status(root, id, status, turn=...)` sets status (and turn only if passed),
`set_turn(root, id, turn)` flips turn alone, `append_log(root, id, note)` appends the log
line. Valid `status`: `open` / `respec_complete` / `done` / `rejected`. Valid `turn`:
`agent` / `human` / `null` (Python `None`).

## Boundary: change-request vs adapt vs plan-next

| Situation | Mode |
|-----------|------|
| A filed `CR-NNNN`, processed headless by the runner (async comment conversation → approval → regenerate, branch-isolated) | **change-request** (this route) |
| A human at the laptop changing/extending a settled spec, interactively | **adapt** (unchanged) |
| Spec the next un-detailed roadmap slice forward (append-only) | **plan-next** |

A CR is the wrapper; adapt is the engine it runs on approval. Keep them distinct: do not fold
CR lifecycle into adapt, and do not let adapt grow CR's branch/relay machinery.
