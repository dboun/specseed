# inject

## Short description

Add manual work to the remote work breakdown without changing settled spec
content. This route is for urgent or out-of-band work that a human wants in the
queue now: a manual epic, ticket, or issue. If the tier is not explicit, infer
the smallest tier that fits the request. Bodies come from
`templates/entity_templates/`.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/spec-change-protocol.md` | the spec spine: outputs, gate, async clarification, tracking contract |
| `references/remote-posts.md` | the post/label model |
| `references/reply-protocol-spec.md` | clarification-round format |
| `references/chat-mode.md` | when run in chat (no runtime) |
| `references/work-breakdown.md` | tier shapes, body links, `type:`/`difficulty:` labels, the dep DAG for manual posts |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|
| `dependencies_validate.py` | validate the manual `plan.json.creates` (parent tree + deps) before emitting |

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

- Plan the requested epic/ticket AND any **issue** at **`:status:todo`** in
  `plan.json.creates` — but create nothing yet (plan-first, per the protocol's approval
  gate). Even injected/urgent items are proposed first: a human approves the plan before
  anything is created, then `apply.py` creates them claimable.
- If the injected item is a ticket and needs execution work, create one or more
  child issues. A simple manual ticket may get one issue with matching scope.
- If the injected item is an issue and no parent ticket is clear, create a
  minimal manual ticket parent and link the issue under it.
- If the injected item is an epic, create at least one ticket if the request
  already contains actionable work. Otherwise create the epic only and ask for
  the first ticket or acceptance condition.
- Use the templates in `templates/entity_templates/` for bodies. Mark bodies
  with "Manual: yes" and preserve the request-post link. Label each issue with its
  `type:` (`feature`/`bug`/`chore`/`spike`) and an optional `difficulty:`.
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
- Refresh the SCHEDULE body. ROADMAP and CURRENT SPRINT re-render from the runtime
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

If clarification is needed instead, `plan.json` should contain only the clarifying
comment(s) + the `spec-change:status:awaiting_input` label (touching only the request
post). The runtime sees a request-post-only run and applies it straight away (no
approval).

## Finish

**Before stopping, validate the dependency graph:** run `dependencies_validate.py` and
clear every error + warning per **Issue dependencies** in `work-breakdown.md`.

Per the protocol: write `plan.json` (with `plan_summary` + `apr`) + `apply.py`, then
stop. The runtime gates it: a run that plans any work is a proposal, so it posts the
`plan_summary` + `APR-NNNN` and parks the request `spec-change:status:awaiting_approval`;
`apply.py` creates the posts only on approval. (Inject stages no spec; the gate trips on
the `creates` in `plan.json`.)

## Boundary

| Situation | Route |
|-----------|-------|
| Add urgent/manual work to the current sprint | **inject** |
| Change settled requirements or design | **adapt** |
| Add the next planned roadmap slice | **plan-next-sprint** |
| One tiny spec or status edit | **tweak** |
