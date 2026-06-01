# Configure mode (technical setup)

**Technical setup ONLY** — where work is tracked, the git workflow, the HITL gate
policy, credentials, runner options. NOT spec or project content. Fast,
template-driven, **≤2 rounds**. Goal: get the plumbing + autonomy decisions out of
the way early so the spec session isn't interrupted by them, and so the runtime
contract (`CLAUDE.md`) the impl agent later obeys is correct from day one.

Uses `references/question-protocol.md` format, but this is a fixed template with
conditional sub-questions — not open exploration. Heavy defaults; "defaults" or `OK`
ends it fast.

## When it fires

- **`/specseed configure`** — anytime, to set up or CHANGE technical settings.
- **Auto-preamble:** the first specseed run in a repo (no `.specseed/memory/remote.json`)
  that is about to enter **bootstrap** or **adopt**. Run this quickly FIRST, persist,
  then continue into the chosen mode. Skippable → local-only.

Not fired for plan-next / adapt / tweak (the repo's already configured; use the
explicit command to change).

## What it writes

Two files under `.specseed/memory/` (may be the first things created there). Both are
**config only** — NOT a spec; they do NOT change mode routing (bootstrap/adopt still
key off `.specseed/spec/`).

**1. `policy.json` — ALWAYS written** (even local-only). The HITL action-gate policy +
git-workflow contract the impl agent obeys at runtime via `CLAUDE.md`. Schema +
defaults in `.specseed/scripts/core/policy.py` (`default_policy()`); `policy.py validate`
checks it; `policy.py render-claude` turns it into the READ-FIRST block of `CLAUDE.md`.
Shape:
```json
{
  "configured": true,
  "hitl": {"categories": {"container":"block","heavy_compute":"block","network":"surface",
                          "deps":"block","data_destructive":"block","external_publish":"block",
                          "outside_repo":"block","secrets":"surface"}},
  "git": {"automation": true, "integration_branch": "dev", "base_branch": null,
          "branch_naming": "{issue_id}-{slug}", "push": "user", "pull_request": "never",
          "auto_merge": "clean_close", "refresh_on_merge": true}
}
```

**2. `remote.json` — only the mirror backend choice.**
- **local-only:** `{"enabled": false, "configured": true}`
- **mirror:** full config — `enabled: true`, `provider`, `repo`, `allowlist`,
  `retry_delay_minutes`, plus `initialized: false`. `initialized` flips `true` after
  the mirror's first `init`, which runs at the **end of bootstrap/adopt** (once work
  exists) — NOT here.

## Round 1 — backend

**1. Where should work be tracked?**
- **A)** local only (default) — `.specseed/` on disk, nothing external.
- **B)** GitHub mirror — also mirror the work onto github issues (drive from a phone).
- **C)** GitLab mirror — same, on GitLab.

→ **local only:** write `remote.json` `{enabled:false, configured:true}`, then continue to **Round 2** (git workflow + HITL gates still apply locally — they govern how the impl agent behaves whether or not there's a mirror).

**2. (mirror only) Which repo?** Detect `git remote get-url origin`; confirm or override (accepts `owner/name`, full URL, or self-hosted GitLab host).

**3. (mirror only) Credentials.** Confirm `GITHUB_PAT` / `GITLAB_PAT` is in the env or a `.env` (the wrappers read both). Verify once with `python .specseed/scripts/remote/remote_config.py ping` (after the file's written) or `github_functions.py get_authenticated_user`. **No Anthropic API key needed** — the runner drives your local Claude Code CLI.

## Mirror limitations — state plainly BEFORE Round 2 (don't bury)

- Opinionated **single-dev** setup: **one always-on laptop, single writer**.
- Local `.specseed/` is **always** the source of truth; the mirror is a mirror + inbox.
- **Don't hand-edit mirrored issues** — manual edits are reverted with a note. You MAY
  (a) create new issues (bugs / requests), (b) comment commands on the CONTROL issue.
- **GitHub pins max 3 issues** (ROADMAP / TIMELINE / CONTROL). **GitLab can't pin** at all.
- You start the runner yourself (`python <repo>_agents_runner.py &`); sync lag ~30–60s + API latency.

## Round 2 — git workflow + HITL gates (+ mirror options if mirror)

All heavy-default → a single `defaults` / `OK` accepts everything and writes both
files. Present the defaults inline; only the overrides cost the user anything.

### 2a. Git workflow → `policy.json` `git{}`

State the default shape in one breath, then ask only what the user wants to change:

> Default git workflow: agent works on a **`dev`** integration branch (forked off
> your default branch), one feature branch per issue (`{issue_id}-{slug}`),
> **auto-merges** a clean issue back into `dev`, and **does NOT push** (you push
> yourself). No PRs. Branches auto-refresh from `dev` so they don't go stale.

- **1. Integration branch name?** default `dev` (autodetect & fork off `main`/`master`). Or pick another name, or "current branch" = no feature branches.
- **2. Who pushes?** **A)** you push yourself (default) / **B)** agent auto-pushes its branch.
- **3. Auto-merge clean issues into the integration branch?** **A)** yes, on clean close (default) / **B)** no, you merge.
- **4. Open a PR/MR per issue?** default **no** (local). Yes → `pull_request: "on_merge_ready"` (only meaningful with a remote).
- **5. Let the agent touch git at all?** default **yes**. "No" → `automation:false` (agent only edits files; you do all git). This overrides 1–4.

Apply the auto-skip rule: if the user just says `defaults`, write `DEFAULT_GIT` and move on. Map answers onto the `git{}` fields (`integration_branch`, `push`, `auto_merge`, `pull_request`, `automation`).

### 2b. HITL action gates → `policy.json` `hitl.categories{}`

Show the 8-category default table (from `policy.py`), one line:

| category | covers | default |
|---|---|---|
| `container` | docker build/run/push/pull | block |
| `heavy_compute` | GPU / training / experiment scripts / long jobs | block |
| `network` | outbound non-localhost (downloads, external APIs) | surface |
| `deps` | add/remove dep or major version bump | block |
| `data_destructive` | delete data, drop/rewrite schema, destructive migration | block |
| `external_publish` | deploy, submission, upload — anything leaving the repo | block |
| `outside_repo` | writes outside the repo root | block |
| `secrets` | read/write credentials | surface |

> Levels: **block** = halt + ask you before acting · **surface** = do it but tell you ·
> **auto** = silent. `defaults` accepts the table; otherwise name the categories to change
> (e.g. "network → block, deps → surface").

These are project-wide defaults. **Per-issue gating is refined later** at work-breakdown
time (the risk-detection pass in `references/work-breakdown.md` proposes which specific
issues need an `approval_required` sign-off) — configure just sets the baseline.

### 2c. Mirror options (mirror only; skip if local-only or user says "defaults")

**1. Command allowlist.** github/gitlab usernames whose CONTROL-issue comments are allowed to run. Default: **PAT owner only**.

**2. Failure retry.** On a failed run (e.g. a usage/session limit), retry after N minutes. Default **30**.

## Persist

Write the config files:
1. `policy.json` — from Round 2a/2b answers (or `default_policy()` on `defaults`). Run
   `python .specseed/scripts/core/policy.py validate` to confirm it's well-formed.
2. `remote.json` — backend choice from Round 1 (+ 2c options if mirror).

**Do NOT run `remote_sync.py init` here** — there's no work to push yet. It runs
automatically at the end of bootstrap/adopt because the prefs are stored (see bootstrap
stage 13.5).

### Also write `.specseed/README.md` (the human operator manual) — FIRST SETUP ONLY

If `.specseed/README.md` does NOT already exist, write it from
`templates/specseed-README_template.md`. This is the human-facing "how to run things"
doc (start/kill the runner, approvals, change the plan, github/gitlab). Specialize it:

- Fill `{{PROJECT}}`, `{{RUNNER}}` (= `<repo>_agents_runner.py`), `{{BACKEND}}`
  (`local only` / `GitHub mirror` / `GitLab mirror`), `{{INTEGRATION_BRANCH}}` (from
  `policy.json` `git.integration_branch`).
- **Prune** the marked blocks per backend: keep `LOCAL-ONLY` blocks and delete
  `MIRROR-ONLY` blocks when local-only; do the reverse when a mirror is configured.
- Delete the leading authoring HTML comment.

On a `/specseed configure` **re-run** that changes the backend, rewrite the README the
same way (re-prune for the new backend). Don't clobber an existing README on a no-op
re-run.

**Re-run via `/specseed configure`:** rewrite whichever file changed. If `policy.json`
changed AND `CLAUDE.md` already exists in the repo, **re-render its operating-policy
block** (`policy.py render-claude` → replace the block at the top of `CLAUDE.md`). If
mirror structural fields (provider/repo) changed, re-run `remote_sync.py init`
(idempotent / self-healing).

## End message (template — phrase naturally)

> Technical config saved (**<local-only | github mirror | gitlab mirror>**;
> git: **<integration branch, push/merge posture>**; gates: **<all-default | the overrides>**).
> Change it anytime with `/specseed configure`.
>
> Now tell me what you want to do — or pick a route:
> - `/specseed bootstrap` — brand-new project, spec from scratch
> - `/specseed adopt` — existing code, recover the spec from it
> - `/specseed plan-next` — break down the next roadmap slice
> - `/specseed adapt` · `/specseed tweak` — change an existing spec
>
> Or just describe the goal and I'll route it.

When fired as the **auto-preamble**, skip the route menu — just confirm config saved
in one line and continue straight into the bootstrap/adopt first-message.
