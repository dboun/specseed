# review route

## Short description

Review ONE issue that finished and is `in_review`, before it can reach `done`. You
produce a verdict; the scheduler resolves the outcome from it plus the configured gates
(`executing/advance.py`). You never merge or approve. Reads the diff + spec; the skill's
spec scripts do not apply.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/reply-protocol-review.md` | the verdict/findings reply form (no approvals) |
| `references/work-breakdown.md` | the code-review gate + the `difficulty:` modifier |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## Inputs (read at runtime, before judging)

- The issue post (body + comments): the acceptance criteria + the scope it claimed.
- `git diff` against the primary branch: what this work actually touched.
- `<specseed_dir>/spec/vision.md` + `sad.md`: intent + the expected layout.

## Hard rules

- Assess correctness, scope, and whether the issue's acceptance criteria are met. Report
  a verdict with a confidence; list concrete, actionable findings.
- Confidence is the primary gate; the issue's `difficulty:` label is the modifier — a
  `hard` issue never auto-approves, always landing in `awaiting_approval` for a human even
  at high confidence (`references/work-breakdown.md`).
- Do not merge, approve, or change workflow labels. **Never review work you
  implemented.**
- Findings go back as the `Code review` comment the impl agent must address on rework.
