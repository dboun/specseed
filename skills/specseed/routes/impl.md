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
- **Parking is not the end of it.** When what stops you is the MACHINE — a toolchain
  that is not installed, a service that is not running, one specific directory outside
  the repo you need — report `status: "needs_user_action"` with a `user_action` object
  (the RESULT FILE schema in your prompt has the shape). Give instructions a human can
  follow, a cheap read-only `check` command that proves it is done, and — whenever one
  command would do it — the `setup` command itself, so the human clicks **Run setup**
  instead of opening a terminal. Omit `setup` only when nothing could be scripted (plug
  in a device, obtain a licence). A `directory` request names the ONE path you want and a
  human grants exactly that. The human gets a card with buttons in the Need feedback tab,
  and your branch resumes the moment the check passes. Prose explaining what you are not permitted to do is the wrong answer:
  nothing can clear it, and every issue depending on yours waits behind it.
- Never review your own work. A `type:spike` issue captures a spike report before it can
  be done.
- **Gated/destructive work is NOT impl.** Dependency changes, data mutation, system
  installs, environment/playground setup, and running experiments route to the **operate**
  route, not here (`references/work-breakdown.md`). Implement code (incl. integration/e2e
  tests); leave the gated execution to operate.
