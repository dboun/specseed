# CLAUDE.md

Invoke `./skills/specseed/references_ext/caveman.md` skill.

This repo is the **source + installer for the `specseed` skill** (a Claude Code / Codex skill). It is NOT an application — there is nothing to "run". You are a dev of this skill: you edit the skill's instructions (markdown) and its tooling (python scripts).

Do not confuse this repo's files with the artifacts the skill *produces*: at runtime, in a TARGET repo, specseed writes a `.specseed/` tree (spec docs + a `project_management/` work breakdown). That tree does not exist here.

The specseed skill doesn't rely on any external skill being present (e.g. in `~/.claude/skills` or `~/.agents/skills`).

## Layout

```
skills/specseed/
  SKILL.md            # entry/router: modes, output hierarchy, protocols. START HERE.
  routes/             # the flows the skill ROUTES INTO (one per mode-table entry)
    bootstrap.md      #   greenfield flow (stages 1–14) + depth dial (lite/standard/incremental) at stage 3.5
    plan-next.md      #   extend an incremental bootstrap forward: spec+break-down the next roadmap slice (append-only; not adapt)
    adopt.md          #   existing code, no .specseed/: reverse-bootstrap the spec FROM the codebase (+import docs). read-only on code
    adapt.md          #   non-trivial changes to an existing spec (incl. CHANGING settled docs)
    tweak.md          #   single-doc edits (+ escalation rules)
    configure.md      #   technical setup: backend (local/github/gitlab) + git workflow + HITL gate policy + runner knobs → config.json (portable); per-repo mirror state → remote.json
    approve.md        #   resolve parked HITL gates (walk/approve/reject); local twin of the remote approve/reject verbs
    migrate.md        #   bring an older-version .specseed/ tree up to the running skill's format (applies migrations/ files in range)
  version.txt         # the skill's own version (x.y.z, one line). Ships via install.sh. Bumped by the release procedure
  migrations/         # one file per breaking (y/x) boundary, named by target version (0.2.0.md…). Consumed by migrate.md; authored at release time
  references/         # shared building blocks LOADED BY routes (not routed-to directly)
    work-breakdown.md #   roadmap + epic/ticket/issue formation (3-tier) + risk-detection & gating pass (HITL)
    remote.md         #   OPTIONAL opt-in github/gitlab mirror + CONTROL channel + HITL gate lifecycle
    component-questions.md, question-protocol.md   # questioning subroutines
  templates/          # artifacts the skill EMITS verbatim into a TARGET repo
    CLAUDE_template.md          # the CLAUDE.md specseed writes into TARGET repos (impl-agent runtime; READ-FIRST operating-policy block)
    specseed-README_template.md # the .specseed/README.md operator manual (human-facing: run/kill/approve/configure/remote)
  references_ext/     # external-origin doc-style passes (integrated from other skills)
    caveman.md        #   doc-writing DENSITY style (terse, signal-dense)
    humanizer.md      #   doc-writing NATURALNESS pass (strip AI tells from human-read prose; em-dash ban). See SKILL.md Step 0
  scripts/            # stdlib-only python3 tooling (NO third-party deps)
    agents_runner.py  #   the ONLY human-run entry (start/kill the orchestrator loop); rest is agent-invoked
    core/             #   plumbing + analysis seams (assemble/validate/claim/render/analyze/policy/approvals/drift)
    remote/           #   OPTIONAL mirror cluster (github/gitlab/remote_config/sync/control); off unless user opts in
install.sh            # copies skills/specseed → ~/.claude/skills and ~/.agents/skills (recursive, preserves subdirs)
README.md
```

## Versioning & releases

The skill is versioned `x.y.z` in `skills/specseed/version.txt` (one line; ships via `install.sh`). A target repo records what built its tree in `<repo>/.specseed/version.txt`; the `migrate` route reconciles the two. Full model in `skills/specseed/migrations/README.md`.

- **z (patch)** — every change. NON-breaking to an existing `.specseed/` tree. No migration file.
- **y (minor)** — breaking to produced artifacts (spec/frontmatter format, script CLI/IO, folder layout, JSON schema): "old trees misbehave unless migrated". Needs a migration file. Agent may bump y.
- **x (major)** — **user-only** decision (major rethink). Agent may *suggest* x, never sets it autonomously.
- Stay `0.y.z` until the skill is usable + verified.

**Cutting a release (when the user says "let's cut a release" / "create a release").** Do NOT pre-author migrations; everything happens at cut time by comparing commits:

1. **Find the previous release** — `git describe --tags --abbrev=0` (or `git tag`). Confirm the version + commit with the user. (None yet → previous is the untagged 0.1.0; see "Initial tag" below.)
2. **Diff** `<prev_tag>..HEAD`. Classify what changed in the SKILL surface that a `.specseed/` tree depends on: spec/frontmatter format, script CLI or I/O contract, folder layout, JSON schema, the entry-file templates.
3. **Decide the bump** with the user: any breaking change to the above → **y** (or **x** if the user calls it a major rethink); otherwise **z**.
4. **If y or x:** author `skills/specseed/migrations/<new_version>.md` (format in `migrations/README.md`) describing how to transform a tree from the previous format to the new one, derived from the diff. This is a **single hop** (`from:` = previous release, `to:` = new version): cover only THIS release's changes, not anything older. Files chain at migrate time, so never restate prior migrations. Mark uncertain steps `# REVIEW:`. (z bump → no migration file.)
5. **Bump** `skills/specseed/version.txt` to the new version. Commit (migration file + version bump together).
6. **Tag + push** — present the user the exact commands (tag the release commit `vX.Y.Z`, push the tag). Tagging/pushing is outward-facing: hand the commands over (or ask) rather than running git unprompted.

**Initial tag (0.1.0, one-time).** 0.1.0 is the released state on `main` (commit `bcd2234`), currently untagged. To tag + push it (run manually):

```bash
git tag -a v0.1.0 bcd2234 -m "specseed 0.1.0"
git push origin v0.1.0
```

The current `release-0.2/formalize` branch is unreleased work; `version.txt` stays `0.1.0` until a 0.2.0 release is formally cut (which is when its migration file gets authored).

## Mental model (the work layer specseed builds)

Three tiers: **epic → ticket → issue**, plus **sprints** as an orthogonal grouping.
- epics + tickets = PM / non-technical. Tickets carry `satisfies_reqs` + the critical-path `depends_on` DAG.
- issues = technical, the unit an agent claims and executes.
- **sprints** = time-boxed batches of tickets (~168h soft budget), ORTHOGONAL to epics (epic = by outcome, sprint = by time; a ticket has one of each via `epic:` + `sprint:`). Drive `TIMELINE.md` + claim ordering; never appear in `ROADMAP.md`.
- **Folders are source of truth**; `tickets.json` / `issues.json` / `sprints.json` are GENERATED by the assemble scripts and hold live runtime state (status/claim). Frontmatter is flat `key: value` with inline JSON for lists/objects (so the parsers stay stdlib-only).
- Critical path is at the **ticket** tier and is **PROJECT-level** (not per-sprint — it informs sprint assignment); issues are claimed at the **issue** tier; claiming is **sprint-scoped** (active sprint first, spill to next).
- **Memory** lives under `.specseed/memory/` (dir): `session_state.md` (session scratch) + `sprint_planning.md` (durable sprint prefs). Co-located CONFIG (not memory): `config.json` — the PORTABLE "how-you-work" file (HITL gates + git workflow + `backend{enabled,provider}` + `runner{}`); copyable between repos — and `remote.json` — per-repo mirror STATE (repo/allowlist/issue-map/cursors), NOT portable, mirror only. Split rule: anything project-specific → remote.json; the rest → config.json. `save_state` persists only the state keys so transient runtime fields never leak.
- **HITL (two axes).** (1) Per-entity *completion* gates — `approval_required`/`review_required` → `awaiting_approval`/`in_review`; the implementing agent can't self-bypass. (2) *Action* gates — config-driven classes (container/heavy_compute/network/deps/data_destructive/external_publish/outside_repo/secrets), each `block`/`surface`/`auto` in `config.json`, fire mid-work regardless of issue. A `block` → **park-and-continue**: write `issues/<id>/approval.md`, set `awaiting_approval`, move to next issue. Humans resolve via the **approve** route (or remote `approve`/`reject` CONTROL verbs). Policy renders into a READ-FIRST block at the top of the target repo's `CLAUDE.md` (`config.py render-claude`). Per-issue gating is decided at work-breakdown time by the **risk-detection & gating pass** (consolidated table, explicit user approval; also suggests the *isolate-gated-execution* split — prep/run/consume, soft convention not enforced). **Git workflow** (branch-per-issue off a configurable `dev`, push auto-or-user, PR/auto-merge, refresh-on-merge) is also in `config.json`.
- **Depth dial (bootstrap)** = how much the user answers/reviews at once; NEVER drops artifacts or changes scripts. `lite` (small: same docs, fewer Qs, ~1 sprint), `standard` (plan all now, today's flow), `incremental` (big: spec shared contract whole-but-lean, deep-spec + break down ONLY the first increment → 1 sprint, defer the rest). The target of the optimization is **user-interaction overhead**, not the machinery. Incremental's deferred roadmap tail is picked up later by **plan-next** mode (append-only forward; recomputes project-level CP each slice). **adopt** mode (existing code, no `.specseed/`) reverse-bootstraps the spec FROM the codebase: read-only recon → import any existing docs → draft spec describing reality → built work shown in ROADMAP (NO fabricated done-tickets by default; opt-in for verification coverage) → forward gaps broken down per the same depth dial (incremental → `plan-next` tail). `.specseed/` becomes source of truth; originals never edited in place (opt-in one-time propagate-back). Roadmap is always whole (titles); `roadmap_render.py` tolerates titles with no ticket folder yet, so incremental needs no script changes.

## Scripts (in skills/specseed/scripts/)

`agents_runner.py` sits at `scripts/` top-level (the only human-run entry). All names below live in `scripts/core/`; the remote-mirror cluster lives in `scripts/remote/`. (Paths in the target repo mirror this: `.specseed/scripts/core/...`, `.specseed/scripts/remote/...`.)

`requirements_generate_json.py` (SRS tables→reqs.json), `requirements_analyze.py` / `tickets_analyze.py` / `sprint_plan.py` (SHIPPED stdlib defaults that are also the editable analysis/scheduling seam — orgs may swap them; the rest of the scripts are non-swappable plumbing), `issues_assemble.py` + `tickets_assemble.py` + `sprints_assemble.py` (folders→json; assemble bottom-up: issues → tickets[effort/counts from issues] → sprints[effort/counts from tickets]), `issues_validate.py` + `tickets_validate.py` + `sprints_validate.py` (kept separate by design; sprints_validate also enforces NO backward sprint deps + budget + back-consistency), `claim_issue.py` (atomic flock claim; no-arg auto-picks next ready issue; sprint-scoped via `--sprint-scope`), `issue_info.py`, `roadmap_render.py` (bump ROADMAP "(X/Y)" counts) + `timeline_render.py` (regenerate TIMELINE.md sprint schedule), `verification_map.py` (req→ticket→issues→tests), `drift_check.py`, `config.py` (load/validate the PORTABLE `config.json` — hitl + git + `backend` + `runner`; `render-claude` → the READ-FIRST operating-policy block in CLAUDE.md; the runner validates it on startup), `approvals_render.py` (scan `issues/*/approval.md` → `APPROVALS.md` + `approvals.json` for pending HITL gates).

**`agents_runner.py`** (top-level, `scripts/agents_runner.py`) — the always-on orchestrator loop + `--write-shim` (writes the `<repo>_agents_runner.py` shim). Loads + validates `config.json` on startup (fails fast if missing/invalid) and builds the `claude` command + loop interval from `config.runner`. **Backend-agnostic:** keyed off `config.backend.enabled`, it runs **local-only** (just the claim+run work loop + file-based `runner.ctl` control) OR **mirror** (additionally reconcile + the CONTROL channel). `remote is None` inside the loop = the local branch. It imports the remote cluster but no-ops the mirror steps when local.

**Optional remote mirror cluster** (`scripts/remote/`, only used if user opts in — see `references/remote.md`): `github_functions.py` / `gitlab_functions.py` (stdlib REST wrappers), `remote_config.py` (remote.json STATE I/O + `load_runtime` merging `backend.provider` from config.json + provider-agnostic adapter), `remote_sync.py` (local→remote engine: init/reconcile/push/dashboards), `remote_control.py` (CONTROL-issue command channel). Local stays ground truth; the mirror is shipped plumbing, off by default.

## Conventions

- Scripts: python3, **stdlib only**. Keep it that way (no PyYAML etc).
- Test a script by building a tiny `.specseed/` fixture under `/tmp` and running the chain (issues_assemble → tickets_assemble → sprints_assemble → validators → sprint_plan → claim_issue → timeline_render → verification_map). Clean up after.
- Skill doc-writing style: caveman-spirit — lean, fragments OK, no filler (see `references_ext/caveman.md`). User-facing comms start normal then go terse.
- Produced spec PROSE (vision/README/SAD-SDD prose/ticket prose/ADR justifications) also gets a **humanizer** anti-AI-tell pass (`references_ext/humanizer.md`, scope + em-dash ban in `SKILL.md` Step 0). Two axes: caveman = density, humanizer = naturalness. Machine artifacts (frontmatter/JSON/SRS tables) + the runtime `CLAUDE.md` are exempt. This applies to skill OUTPUT, not the skill's own internal docs.
