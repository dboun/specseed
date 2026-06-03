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
    change-request.md #   headless conductor the RUNNER invokes per relay turn to drive ONE filed CR-NNNN (async conversation → approval → regenerate via adapt's stages, on a cr/ branch). NOT human-typed
    configure.md      #   technical setup ROUTE: hands off to the interactive scripts/configure.py (no agent Q&A) + does post-return provisioning (scripts tree, version.txt, entity templates, README, CLAUDE.md block). The Q&A → config.json (portable) + per-repo mirror state → remote.json all live in the script
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
- issues = technical, the unit an agent claims and executes. Typed `feature/bug/chore/spike/qa` (a `qa` issue is a ticket's terminal QA pass). Optional `difficulty` (easy/hard, drives the code-review gate), `priority` override, and `created_at` (manual items).
- **sprints** = time-boxed batches of tickets (~168h soft budget), ORTHOGONAL to epics (epic = by outcome, sprint = by time; a ticket has one of each via `epic:` + `sprint:`). Drive `TIMELINE.md` + claim ordering; never appear in `ROADMAP.md`.
- **Folders are source of truth**; `tickets.json` / `issues.json` / `sprints.json` are GENERATED by the assemble scripts and hold live runtime state (status/claim). Frontmatter is flat `key: value` with inline JSON for lists/objects (so the parsers stay stdlib-only).
- Critical path is at the **ticket** tier and is **PROJECT-level** (not per-sprint — it informs sprint assignment); issues are claimed at the **issue** tier; claiming is **sprint-scoped** (active sprint first, spill to next), then ordered by **priority** (issue override → parent ticket → medium) then `created_at` (FIFO for manual items) then topo. Manual items added via the human-run `add_work.py` (high → active sprint, jumps the queue; normal → backlog; no replan).
- **Memory** lives under `.specseed/memory/` (dir): `session_state.md` (session scratch) + `sprint_planning.md` (durable sprint prefs). Co-located CONFIG (not memory): `config.json` — the PORTABLE "how-you-work" file (HITL gates + git workflow + `backend{enabled,provider}` + `runner{}` incl. the `agents{}` matrix [FUNCTION implement/review/qa, plus the OPTIONAL `respec` for CR conductor → DIFFICULTY easy/hard → ordered fallback chain of {provider claude|codex, config_dir, model, effort} specs] + `review{}` code-review gate + `qa{}` policy + `cr{}` spec-change-request block [enabled/label/branch_prefix, off by default]); copyable between repos — and `remote.json` — per-repo mirror STATE (repo/allowlist/issue-map/cursors), NOT portable, mirror only. Split rule: anything project-specific → remote.json; the rest → config.json. `save_state` persists only the state keys so transient runtime fields never leak.
- **HITL (two axes).** (1) Per-entity *completion* gates — `approval_required`/`review_required` → `awaiting_approval`/`in_review`; the implementing agent can't self-bypass. Code review fills the long-anticipated seam: a separate reviewer writes `issues/<id>/review.json` (confidence+verdict), `review_gate.py` decides auto-close vs `awaiting_approval` (confidence primary, `difficulty` modifier — `hard` always to a human) vs `in_progress` (changes); a needs-human review writes an `entity-approval` into `approval.md` so it surfaces in APPROVALS. Optional end-of-ticket **QA** = a terminal `type:qa` issue whose findings become `bug` issues on the same ticket. (2) *Action* gates — config-driven classes (container/heavy_compute/network/deps/data_destructive/external_publish/outside_repo/secrets), each `block`/`surface`/`auto` in `config.json`, fire mid-work regardless of issue. A `block` → **park-and-continue**: write `issues/<id>/approval.md`, set `awaiting_approval`, move to next issue. Humans resolve via the **approve** route (or remote `approve`/`reject` CONTROL verbs). Policy renders into a READ-FIRST block at the top of the target repo's `CLAUDE.md` (`config.py render-claude`). Per-issue gating is decided at work-breakdown time by the **risk-detection & gating pass** (consolidated table, explicit user approval; also suggests the *isolate-gated-execution* split — prep/run/consume, soft convention not enforced). **Git workflow** (branch-per-issue off a configurable `dev`, push auto-or-user, PR/auto-merge, refresh-on-merge) is also in `config.json`.
- **Depth dial (bootstrap)** = how much the user answers/reviews at once; NEVER drops artifacts or changes scripts. `lite` (small: same docs, fewer Qs, ~1 sprint), `standard` (plan all now, today's flow), `incremental` (big: spec shared contract whole-but-lean, deep-spec + break down ONLY the first increment → 1 sprint, defer the rest). The target of the optimization is **user-interaction overhead**, not the machinery. Incremental's deferred roadmap tail is picked up later by **plan-next** mode (append-only forward; recomputes project-level CP each slice). **adopt** mode (existing code, no `.specseed/`) reverse-bootstraps the spec FROM the codebase: read-only recon → import any existing docs → draft spec describing reality → built work shown in ROADMAP (NO fabricated done-tickets by default; opt-in for verification coverage) → forward gaps broken down per the same depth dial (incremental → `plan-next` tail). `.specseed/` becomes source of truth; originals never edited in place (opt-in one-time propagate-back). Roadmap is always whole (titles); `roadmap_render.py` tolerates titles with no ticket folder yet, so incremental needs no script changes.

## Scripts (in skills/specseed/scripts/)

`agents_runner.py` sits at `scripts/` top-level (the only human-run entry). All names below live in `scripts/core/`; the remote-mirror cluster lives in `scripts/remote/`. (Paths in the target repo mirror this: `.specseed/scripts/core/...`, `.specseed/scripts/remote/...`.)

`requirements_generate_json.py` (SRS tables→reqs.json), `requirements_analyze.py` / `tickets_analyze.py` / `sprint_plan.py` (SHIPPED stdlib defaults that are also the editable analysis/scheduling seam — orgs may swap them; the rest of the scripts are non-swappable plumbing), `issues_assemble.py` + `tickets_assemble.py` + `sprints_assemble.py` (folders→json; assemble bottom-up: issues → tickets[effort/counts from issues] → sprints[effort/counts from tickets]), `issues_validate.py` + `tickets_validate.py` + `sprints_validate.py` (kept separate by design; sprints_validate also enforces NO backward sprint deps + budget + back-consistency), `claim_issue.py` (atomic flock claim; no-arg auto-picks next ready issue; sprint-scoped via `--sprint-scope`; ordered sprint→priority→created_at→topo), `review_gate.py` (code-review decision seam: reads `issues/<id>/review.json` + `config.review` + `difficulty` → auto-approve / awaiting_approval / changes; `--apply` mutates status + writes an `entity-approval` to surface needs-human reviews), `issue_info.py`, `roadmap_render.py` (bump ROADMAP "(X/Y)" counts) + `timeline_render.py` (regenerate TIMELINE.md sprint schedule), `verification_map.py` (req→ticket→issues→tests), `drift_check.py`, `config.py` (load/validate the PORTABLE `config.json` — hitl + git + `backend` + `runner` [incl. the optional `respec` agent function] + `review` + `qa` + `cr`; `render-claude` → the READ-FIRST operating-policy block in CLAUDE.md incl. the review/QA completion-gate contract + a conditional CR line; the runner validates it on startup), `approvals_render.py` (scan `issues/*/approval.md` → `APPROVALS.md` + `approvals.json` for pending HITL gates), `change_requests.py` (pure CR entity I/O — `.specseed/change_requests/<CR-NNNN>/cr.md` ground truth; create/load/save/list + status/turn/session/branch/cursor setters + pending-comment sidecar; read-only `list`/`show` CLI), `inbox.py` (pure I/O for a work issue's free-form instruction inbox — `issues/<id>/inbox.md` append-only `IN-<seq>` entries + the `inbox.state` processed cursor; parse/append/cursor only — the runner's `inbox_step` does the agent-mediated thinking + boundaries + reply).

Three more human-run entries sit at `scripts/` top-level alongside `agents_runner.py`: **`configure.py`** — the interactive technical-setup configurator (pure python, stdlib, zero tokens, NO agent calls); walks simple yes/no questions → writes the portable `config.json` (+ `remote.json` for a mirror), with `--defaults` / `--set k=v` / `--show` for non-interactive use. The agent's `configure.md` route hands off to it instead of interviewing the user, then does the post-return provisioning. **`add_work.py`** — scaffold a manual ticket+issue out of band (high priority → current sprint + queue jump, normal → backlog; re-assembles + renders, never replans). **`add_change_request.py`** — file a CR locally (sibling of `add_work.py`); a remote `change-request`-labeled issue does the same from afar.

**`agents_runner.py`** (top-level, `scripts/agents_runner.py`) — the always-on orchestrator loop + `--write-shim` (writes the `<repo>_agents_runner.py` shim). Loads + validates `config.json` on startup (fails fast if missing/invalid) and reads the loop interval + agent matrix from `config.runner`. Each work pass **peeks** the next ready issue (`claim_issue.py --peek`, read-only), picks the agent chain for its (FUNCTION, DIFFICULTY) — `qa` for a type:qa issue, else `implement` — and builds the `claude` / `codex` command for the chosen spec (config_dir → `CLAUDE_CONFIG_DIR`/`CODEX_HOME`); a failed spec falls through to the next in the chain before the retry cooldown arms. **Backend-agnostic:** keyed off `config.backend.enabled`, it runs **local-only** (just the claim+run work loop + file-based `runner.ctl` control) OR **mirror** (additionally reconcile + the CONTROL channel). `remote is None` inside the loop = the local branch. Each pass also runs — BEFORE the work step — an **inbox step** (`inbox_step`): one issue's free-form `inbox.md` instructions/questions, batch-processed FRESH-CONTEXT (never a resumed session), with hard boundaries (spec/scope change → "file a CR"; new work → "use `add_work`"; never edits settled docs), acting on issues in ANY state incl. blocked/done, skipping the issue the work step will claim; and AFTER work a **review step** (reviews one in-scope `in_review` issue with the `review`-function chain for that issue's difficulty, then `review_gate.py`); merges still happen in the impl agent's finish flow. It imports the remote cluster but no-ops the mirror steps when local. **CR/respec mode** (gated on `config.cr.enabled`, off by default): when a CR is active the runner FREEZES claiming, isolates the respec on a `cr/<CR-NNNN>` branch off the integration branch, relays the conversation through a RESUMABLE coding-agent session (one `session_id` per CR; capture-from-JSON then `--resume`), and on a terminal CR state merges (approved) or drops (rejected) the branch before resuming claiming. Strictly serial FIFO. The relay prompt is NATURAL LANGUAGE (`claude -p` headless drops user-invoked slash commands) — the CONTROL work-verbs (adapt/plan-next/approve/reject) likewise use a natural-language `control_prompt`, not a `/specseed` slash.

**Optional remote mirror cluster** (`scripts/remote/`, only used if user opts in — see `references/remote.md`): `github_functions.py` / `gitlab_functions.py` (stdlib REST wrappers), `remote_config.py` (remote.json STATE I/O + `load_runtime` merging `backend.provider` from config.json + provider-agnostic adapter), `remote_sync.py` (local→remote engine: init/reconcile/push/dashboards; also CR intake — a `change-request`-labeled issue → `CR-NNNN`, per-CR comment relay in/out, state reflection), `remote_control.py` (CONTROL-issue command channel; `crs` verb + CR roll-up in `status`). Local stays ground truth; the mirror is shipped plumbing, off by default.

## Conventions

- Scripts: python3, **stdlib only**. Keep it that way (no PyYAML etc).
- **Scripts MUST have pytest tests under `tests/unit/python/` (run: `python3 -m pytest tests/unit/python/`).** This is a hard rule and **applies to you (the skill dev) too**: when you add or change a script, add/extend its tests in the same change. Unit tests cover the **plumbing + analysis-seam scripts** (config, assemble, validate, claim_issue, review_gate, add_work, the renders) against tiny on-disk `.specseed/` fixtures (pytest `tmp_path`). **Tests MUST NOT invoke an agent and MUST consume zero tokens** — so `agents_runner.py`'s `claude` shell-out is deliberately *not* tested (test only its pure helpers if any). Quick throwaway `/tmp` chain checks (issues_assemble → tickets_assemble → sprints_assemble → validators → sprint_plan → claim_issue → timeline_render → verification_map) are fine while iterating, but the durable coverage lives in `tests/unit/python/`. Useful Python integration tests belong under `tests/integration/python/`; mark them `integration`, keep them fast, isolated under `tmp_path` or `/tmp`, clean up any persistent scratch, mock network/agent outputs, and add them when new script flows would otherwise only be checked by hand. **Integration tests are opt-in only:** bare `python3 -m pytest` is configured to run units only; run integration tests only when explicitly asked, when editing an integration test, or during pre-release/CI checks (`python3 -m pytest tests/integration/python/`).
- Skill doc-writing style: caveman-spirit — lean, fragments OK, no filler (see `references_ext/caveman.md`). User-facing comms start normal then go terse.
- Produced spec PROSE (vision/README/SAD-SDD prose/ticket prose/ADR justifications) also gets a **humanizer** anti-AI-tell pass (`references_ext/humanizer.md`, scope + em-dash ban in `SKILL.md` Step 0). Two axes: caveman = density, humanizer = naturalness. Machine artifacts (frontmatter/JSON/SRS tables) + the runtime `CLAUDE.md` are exempt. This applies to skill OUTPUT, not the skill's own internal docs.
