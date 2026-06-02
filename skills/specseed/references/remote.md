# Remote mirror (optional, opinionated)

Mirror the local work layer onto **GitHub or GitLab** so a single dev can drive the
whole project from a phone — no computer present, but the laptop stays on. This is
an **opt-in, opinionated** workflow. OFF by default; offered once at onboarding.

**Local `.specseed/` is ALWAYS ground truth.** Remote is a mirror + a bug inbox + a
command channel — never authoritative for entities that already exist locally.

Vocabulary: a platform issue is a **"github issue"** (GitLab: still say "github
issue" in agent-facing text to avoid colliding with specseed's own `issue` tier;
internally GitLab calls it an issue/iid). Our three tiers stay **epic / ticket /
issue**.

---

## Invariants (non-negotiable)

1. **Local truth.** The remote is a projection. Reconciliation flows remote→local
   only for the two allowed remote actions below; everything else flows local→remote.
2. **Single writer, one laptop.** Always-on laptop runs the orchestrator. No second
   machine, no shared lock needed (local `flock` still guards `issues.json`).
3. **No API key for the model.** The runner invokes the **local Claude Code CLI**
   (`claude`), already authenticated on the laptop. Only the git host PAT is stored
   (`GITHUB_PAT` / `GITLAB_PAT`).
4. **The user is instructed NOT to hand-edit mirrored github issues.** Exactly two
   remote actions are honored as input:
   - **(a)** Create a NEW github issue (bug / feature request) → ingested as new local work.
   - **(b)** Comment a command on the **CONTROL** github issue → dispatched as a verb.
   Any other remote edit is treated as accidental (see Reconciliation).

---

## Topology — what `init` creates

Three **permanent, pinned** github issues (GitHub pins cap at 3 — this is why we
stop at three) + one rolling sprint dashboard:

| github issue | role | pinned | written by |
|--------------|------|:------:|------------|
| **ROADMAP**  | mirror of `ROADMAP.md` | ✓ | `roadmap_render` cascade |
| **TIMELINE** | mirror of `TIMELINE.md` | ✓ | `timeline_render` cascade |
| **CONTROL**      | command inbox; top post = ops cheatsheet | ✓ | `init` (body), runner (replies) |
| **SPRINT**   | current-sprint dashboard (rewritten each sprint) | — | sprint cascade |

GitLab has no issue-pinning → pins are skipped there; the four still exist and are
linked from each other's bodies + from `README`.

**Labels seeded at init** (state only — relationships are NEVER labels):
- `status:todo` `status:in_progress` `status:blocked` `status:in_review`
  `status:awaiting_approval` `status:done` `status:wont_do` `status:deprecated`
- `sprint:<id>` created lazily as sprints appear.
- `tier:epic` `tier:ticket` `tier:issue` — the ONE structural label allowed, only so
  the phone can filter a flat list by tier (it's an attribute, not a relationship).

Optional repo issue templates:
- Canonical templates live under `.specseed/entity_templates/` no matter which backend
  is used.
- If the user opts in (`backend.entity_templates.enabled:true`), only user-facing
  templates (`bug`, `feature`, `change-request`) are projected to the selected host's
  native directory: GitHub `.github/ISSUE_TEMPLATE/`, GitLab `.gitlab/issue_templates/`.
- Specseed supports one mirror provider at a time (`github` OR `gitlab` OR local-only),
  not simultaneous GitHub+GitLab projection.
- Projection is plain file output on the current branch. The host UI may only show the
  templates once those files reach the repo's default branch.

---

## Mapping — local → github issue

Every epic, ticket, and issue becomes **one flat github issue**. **No nesting API,
no parent/child labels.** Relationships are expressed as **markdown links** in the
body (works identically on GitHub and GitLab).

```
Title:  [PROJ-0042] Add password reset flow
Labels: status:in_progress, sprint:SPRINT_2026_W01_A, tier:ticket
Body:
  <prose: story / description / acceptance criteria, copied from the folder md>

  ---
  - **Epic:** [EPIC-0001 Account security](#<n>)
  - **Issues:** [FEAT-0101](#<n>) · [FEAT-0102](#<n>)
  - **Depends on:** [PROJ-0040](#<n>)
  - specseed-id: PROJ-0042   ← machine anchor, do not edit
```

- **State → label + open/closed.** `status:<state>` label always set. Terminal
  states (`done` / `wont_do` / `deprecated`) ALSO close the github issue
  (`done` = closed-completed; `wont_do` / `deprecated` = closed-not-planned).
  Non-terminal = open.
- **Claim → assignee.** `claimed_by` → github assignee (best-effort; skipped if the
  name isn't a repo member).
- **Sprint → `sprint:<id>` label.** Also surfaced in the SPRINT dashboard body.
- **`specseed-id: <ID>` trailer** is the join key. The registry maps ID↔github-issue
  number; the trailer is the human-visible backup. Never edited by the user.

The reverse join (github issue → local ID) is the **registry** (`remote.json`), with
the body trailer as fallback if the registry is lost.

---

## Registry — `.specseed/memory/remote.json` (per-repo STATE)

Single stdlib-JSON file. Holds ONLY project-specific mirror state — the target repo,
the command allowlist, the ID↔number map, the poll cursors, the dashboard issue
numbers. It is **NOT portable**: never copy it to another repo. The PORTABLE backend
choice (`enabled` + `provider`) and runner knobs live in `config.json` (see
`scripts/core/config.py`); copy THAT between repos.

```json
{
  "repo": "owner/name",                  // or full URL; normalized by the wrappers
  "allowlist": ["octocat"],              // per-repo: usernames whose CONTROL comments execute; [] = owner-only
  "permanent": {                          // numbers of the 4 dashboards
    "roadmap": 1, "timeline": 2, "control": 3, "sprint": 4
  },
  "map": { "PROJ-0042": 17, "FEAT-0101": 18 },   // specseed-id -> github issue number
  "cli_cursor": "2026-05-31T12:00:00Z",   // last processed CONTROL comment timestamp
  "pull_cursor": "2026-05-31T12:00:00Z",  // last processed new-issue scan
  "labels_seeded": true,
  "initialized": false                    // flips true after the first remote_sync init
}
```

The mirror engine works from a single runtime `cfg` = this state ∪ the `provider` from
config.json (`remote_config.load_runtime()`); `save_state()` persists only the keys
above, so the injected `provider` / `retry_delay_minutes` never leak back in. The PAT
does NOT live here — it stays in the environment / `.env` the wrappers already read.

---

## Reconciliation policy

Assume the user does NOT hand-edit mirrored github issues. On each runner wake:

1. **Pull new work (allowed action a).** Scan for github issues NOT in `map` and not
   one of the four permanent ones, created after `pull_cursor`. Each → a new local
   **BUG** (or feature) ticket+issue skeleton (title/body from the github issue),
   `status: todo`, flagged for the agent to flesh + slot into the DAG. Add to `map`,
   advance `pull_cursor`, comment back the assigned local ID.
2. **Process CONTROL comments (allowed action b).** See "CONTROL verbs".
3. **Detect drift on mapped issues.** For each mapped github issue, compare against
   local:
   - **No conflict** (remote matches local, or remote changed a field local doesn't
     own) → take remote silently. (In practice rare, since edits are discouraged.)
   - **Conflict** (remote edited a field local owns: title/body/state/labels) →
     **local overwrites**, and the runner posts a comment:
     > ⚠️ Reverted a manual edit. This issue mirrors local `<ID>`; edit it via the
     > CONTROL issue (`adapt …`) or locally, not here. See pinned CONTROL issue.
4. **Push.** Re-render the four dashboards + upsert every mapped issue from local.

"Field local owns" = title, body, status/label, open-closed, assignee, sprint. The
only remote-origin field is *the existence of a brand-new issue* (step 1).

---

## Heal (self-repair permanent + mapped issues)

- **Permanent issue deleted** → recreate it on next wake, re-pin, rewrite body,
  update `permanent.*` number. (You can't truly delete on GitHub via API — but if a
  number 404s, treat as gone.)
- **Permanent issue closed by hand** → reopen + comment "this is a permanent
  dashboard; don't close it."
- **Mapped work issue closed by hand** (not via a terminal local status) → reopen +
  comment with the explicit proper path:
  > To mark this done, comment `claim-next` is not it — set the local status: either
  > finish it through the agent, or comment `adapt mark <ID> done` on the CONTROL issue.
  > Closing here is reverted because local is the source of truth.
- **Mapped work issue deleted** → mark the local entity `deprecated` (it's gone from
  the mirror; don't silently recreate churn), note in its `notes`, comment is moot.

---

## CONTROL verbs (the command channel)

The user comments a verb on the **CONTROL** github issue. The runner polls
`list_repo_issue_comments(since=cli_cursor)`, keeps only comments **on the CONTROL issue
number** authored by an **allowlisted** user (empty allowlist → repo owner only),
dispatches, and replies with the result as a comment. Fixed verb set — unknown verb
→ reply with the cheatsheet.

| verb | action |
|------|--------|
| `status` | reply: runner state, active sprint, in-flight issue, ready count |
| `sync` | force one reconcile pass now |
| `pause` | finish the current issue, then idle (poll mirror + CONTROL only) |
| `resume` | leave idle, resume claiming work |
| `kill` | stop the current `claude` run immediately (SIGTERM the child) |
| `claim-next` | claim + run the next ready issue now |
| `adapt <text>` | run `claude` headless in adapt mode (natural-language prompt, not a slash) |
| `plan-next` | run `claude` headless in plan-next mode |
| `approvals` | reply: list of pending HITL gates (from `approvals.json`) |
| `approve <ID> [opt]` | resolve a parked HITL gate — run `claude` headless in approve mode |
| `reject <ID> <note>` | reject a parked HITL gate — run `claude` headless in approve mode |

(Headless `claude -p` does NOT expose user-invoked slash commands, so the runner phrases
these as a natural-language task that auto-triggers the specseed skill — see
`agents_runner.control_prompt`. CRs use the same approach via `relay_prompt`.)

The CONTROL issue **top post** (written at init) is a short cheatsheet of exactly these
verbs + the pause/stop story. Authorization: comment author ∈ allowlist. Sudo /
arbitrary shell is intentionally NOT a verb — the channel is for control, not RCE.

---

## The runner — `<repo>_agents_runner.py`

A thin shim the skill writes at the repo root; logic lives in the shared
`agents_runner.py` under `.specseed/scripts/`. It loads `config.json` (the portable
config) on startup and **fails fast if it's missing or invalid**. **The same runner
serves local-only** (`config.backend.enabled:false`): it runs the work loop + file-based
control below, and skips steps 2–3 (reconcile + CONTROL). The mirror loop described here
is the `backend.enabled:true` superset.

```bash
python <repo>_agents_runner.py &        # start (background)
```

Loop, every ~30–60s:
1. Read control file `.specseed/memory/runner.ctl` (`run` | `pause` | `stop`).
   `stop` → graceful exit. `pause` → only steps 2–3 run (no claiming).
2. `remote_sync` reconcile pass (pull new work, drift, heal, push dashboards).
3. `remote_control` process new CONTROL comments.
4. If `run`: claim + execute the next ready issue via the local `claude` CLI; on
   done/blocked, post the progress comment (see below) and re-render dashboards.
5. Sleep.

The `claude` invocation is **built from `config.runner`** (the task prompt is piped on
stdin, so `-p` carries no positional prompt; output is appended to `runner.log`).
With the defaults (`model: opus`, `effort: high`, `allowed_tools: [Read,Edit,Bash]`,
`max_turns: 400`):

```
claude -p --model opus --effort high --permission-mode auto \
       --allowedTools "Read,Edit,Bash" --max-turns 400
```

The loop interval also comes from `config.runner.interval` (default 45s; `--interval`
overrides).

**Retry on failure.** If a `claude` run exits non-zero (e.g. a usage/session limit),
the runner arms a **cooldown** (`config.runner.retry_delay_minutes`, default **30**) and
skips *only claude attempts* during it — reconcile + CONTROL polling keep running, so you
stay in control and the `status` verb shows the time left. The next attempt fires
automatically once the cooldown clears; a success clears it early. `retry_delay_minutes`
is portable config (`config.json`, applies even local-only), editable there or by asking
the agent.

**Control without launchd** (user asked for something elegant, no daemons):
- **Pause after current:** `pause` verb on the CONTROL issue, OR locally
  `echo pause > .specseed/memory/runner.ctl`. The loop checks the file at the top of
  every iteration and after finishing the current issue.
- **Resume:** `resume` verb, or `echo run > .specseed/memory/runner.ctl`.
- **Stop:** `echo stop > .specseed/memory/runner.ctl` (graceful — finishes current,
  then exits), or `Ctrl-C` / `kill <pid>` (the SIGINT/SIGTERM handler flips control to
  `stop` and exits after the current step). `kill` verb stops only the in-flight
  `claude` child, not the loop.

The runner is **shipped plumbing** (not an analysis seam). It logs to
`.specseed/memory/runner.log`.

---

## Progress comments (back to the phone)

Per the locked decision: comment on the mapped github issue **only on the
moments that matter** (low noise):
- **done** → `✓ Done. <1-line summary>. PR: <url if any>.`
- **blocked** → `⛔ Blocked: <reason>. <next step / what's needed>.`
- **awaiting_approval** → `🔔 Needs your approval — <ID> A<N>: <summary>` + why +
  options + the `approve <ID>` / `reject <ID> <note>` reply hint. This is the HITL
  surface step — see "HITL gate lifecycle" below.

Claim and in-review transitions update the **label** only (no comment).

---

## HITL gate lifecycle (surface · resolve)

Lets a human clear approval gates from a phone, mirroring the local `approve` route.
Local `.specseed/` stays ground truth — the mirror is just the channel.

- **Surface (announce).** When the impl agent parks a gated action it sets the issue
  `awaiting_approval` and writes the request to `approval.md` + runs
  `approvals_render.py`. The runner's `work_step` sees the status transition and posts
  the `🔔 Needs your approval` comment on the mapped github issue (transition-based, so
  it posts once — no dedup logic needed). The `awaiting_approval` label also flips. Use
  the `approvals` CONTROL verb anytime for the full pending list.
- **Resolve (consume reply).** The human comments `approve <ID> <opt>` or
  `reject <ID> <note>` on the **CONTROL** issue. `remote_control` dispatches it as a
  work verb; the runner runs the `approve` route headless (natural-language prompt, the
  same `approve` route a local human uses — `routes/approve.md`). The route appends a
  `## Resolved` marker to `approval.md`, flips the issue (`todo` to resume / `wont_do` /
  `blocked`), and re-renders. The runner then re-pushes so the label/state update
  projects back.
- **Global-gate safety.** A reply authorizes answering *that gated question* only. When
  the resumed issue is later worked and hits another `block`-level action, it parks
  again per the operating-policy contract. The reply is not blanket autonomy.

Idempotency: surfacing is transition-based (one comment per entry into
`awaiting_approval`); resolving keys off the CONTROL comment cursor (`cli_cursor`), so a
handled reply isn't reprocessed.

---

## Scripts involved

- `remote_config.py` — load/save `remote.json` STATE; `load_runtime()` merges the
  portable `backend.provider` from `config.json` onto the state; `save_state()` writes
  back only the canonical state keys; provider dispatch (imports `github_functions` or
  `gitlab_functions`); label/body rendering helpers.
- `config.py` (core, not remote-only) — the portable `config.json` (hitl + git +
  `backend{enabled,provider}` + `runner{}`); the runner loads + validates it on startup.
- `remote_sync.py` — `init` / `reconcile` (pull → drift → heal → push) / dashboard
  rendering. `--dry-run` prints intended calls without mutating.
- `remote_control.py` — poll + authorize + dispatch CONTROL verbs (incl. `approvals`
  inline, `approve`/`reject` as work verbs).
- `agents_runner.py` — the loop; `<repo>_agents_runner.py` shim calls its `main()`.
  Posts the `awaiting_approval` surface comment and runs the `approve`/`reject` route.
- `approvals_render.py` (core, not remote-only) — keeps `approvals.json` current; the
  surface step + `approvals` verb read it.

All stdlib-only, reusing `github_functions.py` / `gitlab_functions.py` for transport.

---

## Onboarding (configure → init, two phases)

The opt-in is split so the technical decisions happen EARLY and the heavy mirror
creation happens LATE (once work exists):

**Phase 1 — configure (early).** `routes/configure.md` captures the technical prefs:
the portable bits into `config.json` (`backend{enabled,provider}`, `runner.retry_delay_minutes`)
and the per-repo bits into `remote.json` (`repo`, `allowlist`, `initialized:false`) —
verifying the PAT, explaining the limitations — but does NOT touch the remote. Runs as a
first-run preamble before bootstrap/adopt, or via `/specseed configure`.

**Phase 2 — init (late).** At the end of bootstrap (stage 13.5) / adopt (9.5), if
`config.backend.enabled` is true and `remote.json` is not yet `initialized`, the mirror
is created with NO further questions:
1. `python .specseed/scripts/remote/remote_sync.py init` — creates the 4 dashboards, pins
   the 3, seeds labels, pushes the current work.
2. Write the `<repo>_agents_runner.py` shim at repo root.
3. Add the "Remote mirror" block to `CLAUDE.md` (runtime contract — see
   `CLAUDE_template.md`).
4. Flip `initialized:true`; tell the user the start command + the pause/stop story.

If configure chose **local-only** (`backend.enabled:false`) → init is skipped; the
feature is invisible.

This is **prefer-programmatic**: the agent calls the scripts, it does not hand-create
github issues one by one.
