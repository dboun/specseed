# Configure mode (technical setup)

**Technical setup ONLY** — where work is tracked, the git workflow, the HITL gate
policy, credentials, runner options. NOT spec or project content.

**The questions live in an interactive script now**, not in this conversation. You do
NOT interview the user. Your job is a short hand-off + the mechanical provisioning
once the config exists. The script: `scripts/configure.py` (pure python, stdlib,
zero tokens), run by the human, writes `.specseed/memory/config.json` (always) and
`remote.json` (mirror only).

## When it fires

- **`/specseed configure`** — anytime, to set up or CHANGE technical settings.
- **Auto-preamble:** the first specseed run in a repo (no `.specseed/memory/config.json`)
  about to enter **bootstrap** or **adopt**. Point the user at the script FIRST, wait,
  then continue into the chosen mode.

Not fired for plan-next / adapt / tweak (already configured; the user re-runs the
script to change settings).

## Unconfigured (no `config.json`) — hand off, don't interview

Send ONE short message; do not ask the setup questions yourself:

> Technical setup runs through a quick script. From the repo root:
> ```
> python3 <SKILL_DIR>/scripts/configure.py
> ```
> Blast Enter to take all-local defaults; it's yes/no questions. Come back here when
> it says "done" and I'll finish wiring things up + continue.

- `<SKILL_DIR>` = the directory holding this skill's `SKILL.md` (resolve it to the real
  absolute path, e.g. `~/.claude/skills/specseed` or `~/.agents/skills/specseed`).
- Mention the non-interactive form for the scripted crowd: `configure.py --defaults`
  (all defaults, no prompts) or `configure.py --set git.push=auto` (one-off overrides),
  `--show` to inspect.
- **STOP and wait.** When the user returns and `.specseed/memory/config.json` now
  exists → go to **Persist** below. (If they say they skipped it, run
  `configure.py --defaults` yourself, or write `default_config()` — local-only.)

When fired as the auto-preamble, after Persist just confirm in one line and continue
straight into the bootstrap/adopt first-message (skip the route menu).

## Configured (`config.json` exists) + `/specseed configure`

Don't re-interview. Ask what they want to change AND give them the command:

> What do you want to change? You can re-run the whole thing — it loads your current
> values as the defaults:
> ```
> python3 <SKILL_DIR>/scripts/configure.py
> ```
> Or a one-off without prompts, e.g. `configure.py --set git.push=auto`
> (`--show` prints the current config). Tell me when you've run it and I'll re-wire.

After they re-run it (config changed) → run the affected **Persist** steps, especially
re-rendering the CLAUDE.md block. If they instead just describe the change in chat,
translate it to the right `--set` invocation and hand THAT to them rather than editing
`config.json` by hand.

## What the script writes (reference)

The split is **PORTABLE config** vs **per-repo state**. `config.json` is the one file
a user copies between repos ("how I work"); `remote.json` is project-specific and must
NOT be copied. Both live under `.specseed/memory/`, both **config only** — NOT a spec;
they do NOT change mode routing (bootstrap/adopt still key off `.specseed/spec/`).

**1. `config.json` — ALWAYS (PORTABLE).** The whole "how-you-work" contract: HITL
action-gates + git workflow (obeyed by the impl agent via `CLAUDE.md`), the **backend
choice** (local vs github/gitlab), and the **runner knobs**. Schema + defaults in
`.specseed/scripts/core/config.py` (`default_config()`); `config.py validate` checks it;
`config.py render-claude` turns the hitl+git half into the READ-FIRST block of
`CLAUDE.md`. Shape:
```json
{
  "configured": true,
  "hitl": {"categories": {"container":"block","heavy_compute":"block","network":"surface",
                          "deps":"block","data_destructive":"block","external_publish":"block",
                          "outside_repo":"block","secrets":"surface"}},
  "git": {"automation": true, "integration_branch": "dev", "base_branch": null,
          "branch_naming": "{issue_id}-{slug}", "push": "user", "pull_request": "never",
          "auto_merge": "clean_close", "refresh_on_merge": true},
  "backend": {"enabled": false, "provider": null,
              "ignore_labels": ["draft","ignore","specseed:ignore","changes-requested",
                                "needs-more-info","needs-triage"],
              "entity_templates": {"enabled": false}},
  "runner": {"interval": 45, "max_turns": 400, "allowed_tools": ["Read","Edit","Bash"],
             "retry_delay_minutes": 30,
             "agents": {
               "implement": {"easy": [{"provider":"claude","config_dir":null,"model":"sonnet","effort":"medium"}],
                             "hard": [{"provider":"claude","config_dir":null,"model":"opus","effort":"high"}]},
               "review":    {"easy": [...], "hard": [...]},
               "qa":        {"easy": [...], "hard": [...]},
               "respec":    {"easy": [...], "hard": [...]}}},
  "review": {"enabled": true, "scope": "hard",
             "auto_approve": {"min_confidence": 90, "difficulty": ["easy"]}},
  "qa": {"enabled": true, "mode": "suggest", "effort_threshold_hours": 4.0},
  "cr": {"enabled": false, "label": "change-request", "branch_prefix": "cr/"}
}
```

`runner.agents` is the agent matrix: each FUNCTION (`implement`/`review`/`qa`, plus
optional `respec` for the CR conductor) → each DIFFICULTY (`easy`/`hard`) → an ordered
fallback chain of specs `{provider, config_dir, model, effort}`. `respec` has no real
easy/hard split (both buckets hold the same chain; the runner reads `hard`).
`review`/`qa`/`cr` are OPTIONAL blocks (absent = the defaults shown).

**2. `remote.json` — per-repo mirror STATE, only for a mirror.** Project-specific,
never copied. Holds `repo`, `allowlist`, the specseed-id↔issue-number `map`, cursors,
`permanent` dashboard issue numbers, `labels_seeded`, `initialized: false`.
`initialized` flips `true` after the mirror's first `init`, which runs at the END of
bootstrap/adopt — NOT here. Local-only writes no `remote.json`.

## Mirror limitations — state these BEFORE the user opts in (if they ask about a mirror)

- Opinionated **single-dev** setup: one always-on laptop, single writer.
- Local `.specseed/` is **always** the source of truth; the mirror is a mirror + inbox.
- **Don't hand-edit mirrored issues** — manual edits are reverted with a note. You MAY
  (a) create new issues (bugs / requests), (b) comment commands on the CONTROL issue.
- **GitHub pins max 3 issues** (ROADMAP / TIMELINE / CONTROL). **GitLab can't pin** at all.
- Keep a remote issue as a note/draft by adding any configured ignore label.
- You start the runner yourself (`python .specseed/scripts/agents_runner.py &`); sync lag ~30–60s.
- Credentials: `GITHUB_PAT` / `GITLAB_PAT` in the env or a `.env`. **No Anthropic key
  needed** — the runner drives your local Claude Code CLI. Verify once (after the script
  has written `remote.json`) with `python .specseed/scripts/remote/remote_config.py ping`.

## Persist (AGENT-side, after the script has written `config.json`)

The script wrote + validated `config.json` (+ `remote.json` if a mirror). Now finish
the mechanical setup the script does NOT do:

1. **Provision the scripts tree** — copy the skill's `scripts/` into
   `.specseed/scripts/` per `SKILL.md` "Provisioning the scripts tree". No repo-root
   shim is written; the user runs `python .specseed/scripts/agents_runner.py &` directly.
   The script ran from the skill dir, so the target's `.specseed/scripts/` may not exist
   yet; do this before any `.specseed/scripts/...` call.
2. **Re-validate** from the target tree: `python .specseed/scripts/core/config.py validate`.
3. **Stamp `.specseed/version.txt`** — FIRST SETUP ONLY. If it does not exist, write the
   running skill's version (one line x.y.z, copied from the skill's own `version.txt`,
   sibling of `SKILL.md`). Don't overwrite on a re-run (migrate owns it after creation).
4. **Entity templates:** `python .specseed/scripts/core/entity_templates.py sync`
   (writes `epic`/`ticket`/`issue`/`bug`/`feature`/`change-request`; also projects the
   user-facing ones if `backend.entity_templates.enabled`).
5. **`.specseed/README.md`** — FIRST SETUP ONLY (or re-prune on a backend change). Write
   from `templates/specseed-README_template.md`: fill `{{PROJECT}}`, `{{BACKEND}}`,
   `{{INTEGRATION_BRANCH}}`; keep the `LOCAL-ONLY` blocks + delete `MIRROR-ONLY` (or
   vice-versa); delete the leading authoring HTML comment. Don't clobber an existing
   README on a no-op re-run.
6. **CLAUDE.md operating-policy block** — render with `config.py render-claude` and put
   it at the top of the repo's `CLAUDE.md`. On a re-run that changed hitl/git/review/qa/cr
   and `CLAUDE.md` already exists, **replace that block in place**. If `CLAUDE.md` is being
   created here, the merge protocol (SKILL.md) applies.

**Do NOT run `remote_sync.py init` here** — there's no work to push yet. It runs
automatically at the end of bootstrap/adopt (see bootstrap stage 13.5). If a re-run
changed mirror structural fields (`backend.provider` / `remote.json` `repo`), re-running
`remote_sync.py init` later is idempotent / self-healing.

## End message (template — phrase naturally)

> Technical config saved (**<local-only | github mirror | gitlab mirror>**;
> git: **<integration branch, push/merge posture>**; gates: **<all-default | the overrides>**).
> Re-run `configure.py` anytime to change it.
>
> Now tell me what you want to do — or pick a route:
> - `/specseed bootstrap` — brand-new project, spec from scratch
> - `/specseed adopt` — existing code, recover the spec from it
> - `/specseed plan-next` — break down the next roadmap slice
> - `/specseed adapt` · `/specseed tweak` — change an existing spec
>
> Or just describe the goal and I'll route it.

When fired as the **auto-preamble**, skip the route menu — confirm config saved in one
line and continue straight into the bootstrap/adopt first-message.
