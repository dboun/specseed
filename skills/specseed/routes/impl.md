# impl route

## Short description

Implement ONE ready work issue in the target codebase. The runtime invokes this once the
issue is claimable: `:status:todo`, claimed into `in_progress`, with every `Depends on:`
it declares already `done` (merged to primary). You write code on the issue's branch; the
scheduler owns every state change and the merge. Works in the codebase with its own
build/test commands; the skill's spec scripts do not apply.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/reply-protocol-impl.md` | when/how impl replies (progress, blocking questions; no approvals) |
| `references/work-breakdown.md` | issue sizing, the `difficulty:` meaning, spike-report format, integration/e2e test issues |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## Inputs (read at runtime, before coding)

- The issue post (body + comments): the task, technical acceptance criteria, artifacts, plan notes.
- The latest `Code review` comment, if any: findings that bounced it back; address every point.
- `<specseed_dir>/spec/vision.md`: intent.
- `<specseed_dir>/spec/sad.md` → `## Project layout`: the authoritative layout; match it, never invent a different structure.
- The SDD/SRS for the area you touch: the how + the requirements being satisfied.

## Hard rules

- Keep edits scoped to what the issue asks. Leave the tree building and test-passing.
- One branch per issue, cut fresh from primary (the git policy is rendered into your
  run). Never merge, never change workflow labels, never approve — the scheduler advances
  state from your result.
- **Honor the action-class gates** the runtime renders into your prompt (container,
  heavy_compute, network, deps, data_destructive, external_publish, outside_repo,
  secrets). When an action hits a gate, park; do not force it.
- Never review your own work. A `type:spike` issue captures a spike report before it can
  be done.
- **Gated/destructive work is NOT impl.** Dependency changes, data mutation, system
  installs, environment/playground setup, and running experiments route to the **operate**
  route, not here (`references/work-breakdown.md`). Implement code (incl. integration/e2e
  tests); leave the gated execution to operate.
