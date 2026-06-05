# inject

Add manual work to the remote work breakdown without changing settled spec
content. This route is for urgent or out-of-band work that a human wants in the
queue now: a manual epic, ticket, or issue. If the tier is not explicit, infer
the smallest tier that fits the request.

Read `references/spec-change-protocol.md`, `references/remote-posts.md`, and
`references/work-breakdown.md` first.

## Fires when

`spec-change:inject` on a request post. The request may say "manual ticket",
"manual issue", "manual epic", "add this to the queue", "hotfix", "do this now",
or similar. This route does not reopen requirements or architecture. If the
request needs a spec change before work can be defined, ask for clarification or
route the user toward `adapt`.

## 1. Reconstruct state (local read)

- Read the request post title, body, and comments from the local tracker.
- Read current work posts and the dashboard posts from the local tracker.
- Identify the current sprint from `current_sprint` dashboard content,
  `current_sprint` labels, or `sprint:*` labels already present on work posts.
- Identify parent candidates if the request names an existing epic or ticket.

## 2. Decide the manual tier

Pick the tier from the request when stated. If not stated, infer it:

- **Issue:** concrete technical task, bug fix, spike, QA pass, or one agent-sized
  change. Prefer issue when an existing ticket is named or obvious.
- **Ticket:** user-visible slice with product acceptance criteria, or a request
  that needs one or more claimable issues beneath it.
- **Epic:** outcome area or grouping that will need multiple tickets.

Ask an async clarification when the tier or parent would materially change the
result. Common questions: "Should this be a standalone ticket or an issue under
ticket #N?", "Which existing epic owns this?", or "What acceptance condition
proves this manual item done?" Keep questions narrow.

## 3. Build the manual work posts

All manual work created by this route becomes the **current sprint**.

- Create the requested epic or ticket at `:status:todo`; create any **issue** at
  **`:status:awaiting_approval`** (gated, per the protocol's approval gate). Even
  injected/urgent issues do not auto-implement: a human approves first.
- If the injected item is a ticket and needs execution work, create one or more
  child issues. A simple manual ticket may get one issue with matching scope.
- If the injected item is an issue and no parent ticket is clear, create a
  minimal manual ticket parent and link the issue under it.
- If the injected item is an epic, create at least one ticket if the request
  already contains actionable work. Otherwise create the epic only and ask for
  the first ticket or acceptance condition.
- Use the templates in `templates/entity_templates/` for bodies. Mark bodies
  with "Manual: yes" and preserve the request-post link.
- Body-link relationships both ways as much as possible (`Epic: #12`,
  `Ticket: #41`, `Issues: ...`, `Depends on: ...`). For posts created in the
  same plan, use stable temporary refs in `plan.json` and have `apply.py`
  resolve them to created ids before writing final bodies.

Do not invent new SRS ids. If the item maps to existing requirements, include
those req ids in the ticket body. If it does not, say "Manual work, no SRS req"
in the body.

## 4. Sprint handling

Manual work preempts the existing active sprint:

- If a current sprint exists, push all of its current ticket membership to the
  **next sprint**. Remove active/current markers from the old board membership
  and assign those tickets to the next sprint label or next sprint section.
- Create or refresh the new current sprint so it contains only the manual
  tickets created or selected by this inject request.
- If the request injects an issue under an existing ticket, move that parent
  ticket into the new current sprint with the manual issue.
- Refresh the SCHEDULE body. ROADMAP and Current sprint re-render from the runtime
  (from the work posts + `sprint:*`/`current_sprint` labels) — do not hand-edit them.

There must still be only one active current sprint after the plan applies.

## 5. plan.json

Record the full decision:

- `manual: true`
- `inferred_tier` and `tier_reason`
- `creates` for all new posts
- `edits` for parent bodies and the SCHEDULE dashboard
- `labels` for status and sprint/current-sprint changes
- `comments` for the request post and any affected parent work posts
- `sprint_shift` describing what moved from current to next sprint

If clarification is needed, `plan.json` should contain only the clarifying
comment and the request status swap to `spec-change:status:awaiting_approval`.

## Finish

Per the protocol: `plan.json` -> `apply.py` -> `enqueue_spec_change_run(...)` ->
stop. If the run created any issue, post one `APR-NNNN` request comment and park
the request `spec-change:status:awaiting_approval` (the approval gate), not `done`.

## Boundary

| Situation | Route |
|-----------|-------|
| Add urgent/manual work to the current sprint | **inject** |
| Change settled requirements or design | **adapt** |
| Add the next planned roadmap slice | **plan-next-sprint** |
| One tiny spec or status edit | **tweak** |
