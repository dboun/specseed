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
   only for the narrow allowed inputs below; everything else flows local→remote.
2. **Single writer, one laptop.** Always-on laptop runs the orchestrator. No second
   machine, no shared lock needed (local `flock` still guards `issues.json`).
3. **No API key for the model.** The runner invokes the **local Claude Code CLI**
   (`claude`), already authenticated on the laptop. Only the git host PAT is stored
   (`GITHUB_PAT` / `GITLAB_PAT`).
4. **The user is instructed NOT to hand-edit mirrored github issues.** Remote inputs are
   honored only through explicit channels:
   - **(a)** Create a NEW github issue (bug / feature request) → ingested as new local work.
   - **(b)** Comment a command on the **CONTROL** github issue → dispatched as a global /
     project verb.
   - **(c)** Comment on a mapped work issue → gate verbs resolve that issue's HITL gates;
     any other text lands in that issue's instruction inbox.
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
- Intake ignore labels: `draft`, `ignore`, `specseed:ignore`, `changes-requested`,
  `needs-more-info`, `needs-triage` by default. These are configurable in portable
  `config.json` under `backend.ignore_labels`.

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
  "allowlist": ["octocat"],              // per-repo: usernames whose command/inbox comments execute; [] = owner-only
  "permanent": {                          // numbers of the 4 dashboards
    "roadmap": 1, "timeline": 2, "control": 3, "sprint": 4
  },
  "map": { "PROJ-0042": 17, "FEAT-0101": 18 },   // specseed-id -> github issue number
  "cli_cursor": "2026-05-31T12:00:00Z",   // last processed repo-wide comment timestamp
  "cli_cursor_ids": [123456],              // same-timestamp comment ids already handled
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
   one of the four permanent ones, created after `pull_cursor`. If the issue has any
   configured `backend.ignore_labels`, skip it without ingesting (draft / review /
   non-actionable bucket). Otherwise each issue → a new local **BUG** (or feature)
   ticket+issue skeleton (title/body from the github issue), `status: todo`, flagged
   for the agent to flesh + slot into the DAG. Add to `map`, advance `pull_cursor`,
   comment back the assigned local ID.
2. **Process command comments (allowed actions b/c).** See "Command channel".
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

## Command channel — where verbs live

The runner polls repo comments once per pass (using `cli_cursor`, or one second before it
when `cli_cursor_ids` is non-empty so same-second retries stay visible). That call returns
new comments repo-wide; the runner keeps only comments by an **allowlisted** user (empty
allowlist → repo owner only), then dispatches **by where the comment landed**. Two
surfaces:

- **CONTROL issue → global runner ops + project work verbs.** Fixed verb set; unknown
  verb → reply with the cheatsheet.
- **A mapped work issue → that issue's HITL gates OR its instruction inbox.** A gate
  verb (`approve`/`reject`/`hold`) resolves a parked gate right where the `🔔` request
  appears; no trip to CONTROL. **Any other (free-form) comment** — an ask or a question —
  is appended to that issue's instruction **inbox** (`inbox.md`) and gets a one-line `📝`
  ack; the runner's `inbox_step` batch-processes it (see "Instruction inbox" below).

A comment on any other issue (ROADMAP / TIMELINE / a CR issue / unmapped) is ignored
by this channel (CR comments are relayed by `remote_sync.reconcile_crs`).

### CONTROL issue

| verb | action |
|------|--------|
| `status` | reply: runner state, active sprint, in-flight issue, ready count |
| `sync` | force one reconcile pass now |
| `pause` | finish the current issue, then idle (poll mirror + CONTROL only) |
| `resume` | leave idle, resume claiming work |
| `kill` | stop the current `claude` run immediately (SIGTERM the child) |
| `claim-next` | claim + run the next ready issue now |
| `adapt <text>` | pause claiming, then run `claude` headless in adapt mode (natural-language prompt, not a slash) |
| `plan-next` | run `claude` headless in plan-next mode |
| `approvals` | reply: list of pending HITL gates (from `approvals.json`), each with its `APR-NNNN` |
| `approve <APR-NNNN> [opt]` | resolve a parked HITL gate — **deterministic**, runs `approvals_resolve.py` (no model) |
| `reject <APR-NNNN> <note>` | reject a parked HITL gate — same deterministic script |
| `hold <APR-NNNN>` | park a gate as `blocked` (defer the decision) — same script |

(`approve`/`reject`/`hold` address a gate by its global `APR-NNNN` id and resolve via
`approvals_resolve.py` — a script flips the status directly, no model, no ambiguity
about which gate. `adapt`/`plan-next` DO need a model: headless `claude -p` does not
expose user-invoked slash commands, so the runner phrases them as a natural-language
task that auto-triggers the specseed skill — see `agents_runner.control_prompt`. CRs
use the same approach via `relay_prompt`.)

### Work issue (in-place gate resolution)

Comment on the work issue carrying the `🔔`: `approve [APR-NNNN] [opt]` /
`reject [APR-NNNN] <note>` / `hold [APR-NNNN]`. The `APR-NNNN` is optional **only** when
the issue has exactly one open gate (the resolver targets it); with two or more open
gates and no id the runner replies listing the open ids and resolves nothing — name one.
The decision routes into the same `approvals_resolve.py` as the CONTROL path; the result
is posted back on the work issue.

The CONTROL issue **top post** (written at init) is a short cheatsheet of exactly these
verbs + the pause/stop story. Authorization: comment author ∈ allowlist. Sudo /
arbitrary shell is intentionally NOT a verb — the channel is for control, not RCE.

### Instruction inbox (free-form issue-local asks)

A non-gate comment on a work issue ("add more comments", "don't do it that way", "why
did you handle X like that?") is **not** a decision — it's an instruction or a question.
It is appended to that issue's `inbox.md` as an `### IN-<seq>` entry (monotonic per-issue
counter, not a timestamp) and acked once with `📝`. Each loop the runner's `inbox_step`:

- picks ONE issue with unprocessed inbox entries (skipping the issue the work step will
  claim this pass, so one pass never has two writers on the same issue), and acts on it
  in **any** state — including `blocked` and `done` (a human comment is often what
  unblocks or redirects),
- feeds the whole unprocessed batch to a **fresh-context** agent (it reads the issue,
  plan.md, step reports, and the real code/diff — never a resumed session, which would
  reason against a phantom tree),
- the agent classifies each message: a **question** → answer; an **in-scope
  instruction** → rework within the issue's existing scope (re-open + re-claim a `done`
  issue, redo, re-close, re-running the review gate if it applies); a **spec / scope
  change** → reject with "file a CR" (`add_change_request` or the `change-request`
  label); **new work** → reject with "use `add_work`". It NEVER edits settled docs and
  NEVER auto-files a CR,
- the runner records the agent's reply as an `agent (re: IN-…)` entry, posts it back on
  the work issue (`📝`-prefixed so it doesn't re-ingest), and advances the cursor
  (`inbox.state`: `processed_through: IN-<seq>`) to the **snapshot's max** — a comment
  that arrives mid-turn is picked up next pass, not skipped.

`inbox_step` runs **before** the work step (answering humans takes precedence over
grinding new implementation work) and only while the runner is in `run` (a paused runner
idles the inbox too). The inbox is the agent-mediated, free-form counterpart to the
deterministic gate verbs — keep the two kinds separate: a decision never rides the inbox,
an instruction never masquerades as an approval.

### Cursor advance (at-least-once, retry-safe)

`cli_cursor` advances only across comments that were **fully handled this pass**, and
never past a still-pending work/gate action: the action is RETURNED to the runner and
executed after polling, so the cursor stops just before the earliest pending action and
the runner extends it per successful action (oldest first, stopping at the first failure).
For multiple comments sharing the same second, `cli_cursor_ids` records which comment ids
at that timestamp are already handled; the next pass re-reads that second and skips only
those ids. A failed/cooling command therefore stays behind the cursor and is re-read +
retried next pass — it is never silently lost.

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
3. `remote_control` process new comments repo-wide (CONTROL ops + per-issue gate verbs + free-form → instruction inbox — see "Command channel"). An `adapt` work verb pauses claiming before it runs, so adapt cannot race the work loop.
4. If `run`: process one issue's instruction **inbox** (answering humans first), THEN
   claim + execute the next ready issue via the local `claude` CLI; on done/blocked, post
   the progress comment (see below) and re-render dashboards.
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
- **awaiting_approval** → `🔔 Needs your approval — <ID> APR-NNNN: <summary>` + why +
  options + the `approve APR-NNNN` / `reject APR-NNNN <note>` / `hold APR-NNNN` reply hint. This is the HITL
  surface step — see "HITL gate lifecycle" below.

Claim and in-review transitions update the **label** only (no comment).

---

## HITL gate lifecycle (surface · resolve)

Lets a human clear approval gates from a phone, mirroring the local `approve` route.
Local `.specseed/` stays ground truth — the mirror is just the channel.

- **Surface (announce).** When the impl agent parks a gated action it sets the issue
  `awaiting_approval` and writes the request to `approval.md`. The runner calls
  `approvals_render.py`: it stamps a stable `APR-NNNN` id, writes `approvals.json`, and
  posts one `🔔 Needs your approval` comment for every open gate without a `Surfaced:`
  stamp. The stamp is per gate, not per status transition, so a second gate on an already
  parked issue still gets announced once. Use the `approvals` CONTROL verb anytime for
  the full pending list.
- **Resolve (consume reply).** The human comments `approve APR-NNNN [opt]`,
  `reject APR-NNNN <note>`, or `hold APR-NNNN` either on CONTROL (global alias) or on the
  work issue carrying the `🔔` (preferred in-place path; id optional only when that issue
  has exactly one open gate). `remote_control` returns a resolver action; the runner calls
  `approvals_resolve.py` directly — no model. The script appends a `## Resolved` marker to
  `approval.md`, flips the issue (`todo` to resume / `wont_do` / `blocked`), re-renders,
  and reports any render warning. The runner then re-pushes so the label/state update
  projects back.
- **Global-gate safety.** A reply authorizes answering *that gated question* only. When
  the resumed issue is later worked and hits another `block`-level action, it parks
  again per the operating-policy contract. The reply is not blanket autonomy.

Idempotency: surfacing is gate-stamp-based (`Surfaced:` means already announced);
resolving keys off the repo-wide comment cursor (`cli_cursor` + `cli_cursor_ids`), so a
handled reply isn't reprocessed, including same-second comments already marked by id.

---

## Scripts involved

- `remote_config.py` — load/save `remote.json` STATE; `load_runtime()` merges the
  portable `backend.provider` from `config.json` onto the state; `save_state()` writes
  back only the canonical state keys; provider dispatch (imports `github_functions` or
  `gitlab_functions`); label/body rendering helpers.
- `config.py` (core, not remote-only) — the portable `config.json` (hitl + git +
  `backend{enabled,provider,ignore_labels}` + `runner{}`); the runner loads + validates
  it on startup.
- `remote_sync.py` — `init` / `reconcile` (pull → drift → heal → push) / dashboard
  rendering. `--dry-run` prints intended calls without mutating.
- `remote_control.py` — poll + authorize + dispatch CONTROL verbs (incl. `approvals`
  inline, `approve`/`reject`/`hold` as work/gate verbs); appends a work issue's free-form
  comment to its instruction inbox.
- `agents_runner.py` — the loop; `<repo>_agents_runner.py` shim calls its `main()`.
  Posts the gate surface comment, runs the deterministic gate resolver, and runs
  `inbox_step` (fresh-context processing of a work issue's instruction inbox).
- `approvals_render.py` (core, not remote-only) — keeps `approvals.json` current; the
  surface step + `approvals` verb read it.
- `inbox.py` (core, not remote-only) — pure I/O for a work issue's `inbox.md` + processed
  cursor; the intake path and `inbox_step` use it.

All stdlib-only, reusing `github_functions.py` / `gitlab_functions.py` for transport.

---

## Onboarding (configure → init, two phases)

The opt-in is split so the technical decisions happen EARLY and the heavy mirror
creation happens LATE (once work exists):

**Phase 1 — configure (early).** `routes/configure.md` captures the technical prefs:
the portable bits into `config.json` (`backend{enabled,provider,ignore_labels}`,
`runner.retry_delay_minutes`)
and the per-repo bits into `remote.json` (`repo`, `allowlist`, `initialized:false`) —
verifying the PAT, explaining the limitations — but does NOT touch the remote. Runs as a
first-run preamble before bootstrap/adopt, or via `/specseed configure`.

**Phase 2 — init (late).** At the end of bootstrap (stage 13.5) / adopt (9.5), if
`config.backend.enabled` is true and `remote.json` is not yet `initialized`, the mirror
is created with NO further questions:
1. Write the `<repo>_agents_runner.py` shim at repo root.
2. `python .specseed/scripts/remote/remote_sync.py init` — creates the 4 dashboards, pins
   the 3, seeds labels, pushes the current work.
3. Add the "Remote mirror" block to `CLAUDE.md` (runtime contract — see
   `CLAUDE_template.md`).
4. Flip `initialized:true`; tell the user the start command + the pause/stop story.

If configure chose **local-only** (`backend.enabled:false`) → init is skipped; the
feature is invisible.

This is **prefer-programmatic**: the agent calls the scripts, it does not hand-create
github issues one by one.
