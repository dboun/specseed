# Configure mode (technical setup)

**Technical setup ONLY** — where work is tracked, credentials, runner options. NOT
spec or project content. Fast, template-driven, **≤2 rounds**. Goal: get the plumbing
decisions out of the way early so the spec session isn't interrupted by them.

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

`.specseed/memory/remote.json` (this may be the first thing created under `.specseed/`).
A bare `remote.json` is **config only** — it is NOT a spec and does NOT change mode
routing (bootstrap/adopt still key off `.specseed/spec/`).

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

→ **local only:** write `{enabled:false, configured:true}`, go straight to **End**. No more questions.

**2. (mirror only) Which repo?** Detect `git remote get-url origin`; confirm or override (accepts `owner/name`, full URL, or self-hosted GitLab host).

**3. (mirror only) Credentials.** Confirm `GITHUB_PAT` / `GITLAB_PAT` is in the env or a `.env` (the wrappers read both). Verify once with `python .specseed/scripts/remote_config.py ping` (after the file's written) or `github_functions.py get_authenticated_user`. **No Anthropic API key needed** — the runner drives your local Claude Code CLI.

## Mirror limitations — state plainly BEFORE Round 2 (don't bury)

- Opinionated **single-dev** setup: **one always-on laptop, single writer**.
- Local `.specseed/` is **always** the source of truth; the mirror is a mirror + inbox.
- **Don't hand-edit mirrored issues** — manual edits are reverted with a note. You MAY
  (a) create new issues (bugs / requests), (b) comment commands on the CONTROL issue.
- **GitHub pins max 3 issues** (ROADMAP / TIMELINE / CONTROL). **GitLab can't pin** at all.
- You start the runner yourself (`python <repo>_agents_runner.py &`); sync lag ~30–60s + API latency.

## Round 2 — mirror options (mirror only; skip if user says "defaults")

**1. Command allowlist.** github/gitlab usernames whose CONTROL-issue comments are allowed to run. Default: **PAT owner only**.

**2. Failure retry.** On a failed run (e.g. a usage/session limit), retry after N minutes. Default **30**.

Both have safe defaults → "defaults" / `OK` writes immediately.

## Persist

Write `remote.json`. **Do NOT run `remote_sync.py init` here** — there's no work to
push yet. It runs automatically at the end of bootstrap/adopt because the prefs are
stored (see bootstrap stage 13.5). Changing config later via `/specseed configure`
when a mirror already exists: re-run `init` (idempotent / self-healing) if structural
fields (provider/repo) changed; otherwise just rewrite `remote.json`.

## End message (template — phrase naturally)

> Technical config saved (**<local-only | github mirror | gitlab mirror>**). Change it
> anytime with `/specseed configure`.
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
