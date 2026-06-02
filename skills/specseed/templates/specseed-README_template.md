<!--
specseed README template. Written to `<repo>/.specseed/README.md` at first setup
(end of the configure preamble; see routes/configure.md "Persist").

FILL the {{PLACEHOLDERS}}:
  {{PROJECT}}              project name
  {{RUNNER}}               the runner shim filename = "<repo>_agents_runner.py"
  {{BACKEND}}              "local only" | "GitHub mirror" | "GitLab mirror"
  {{INTEGRATION_BRANCH}}   config.json git.integration_branch (default: dev)

PRUNE the marked blocks:
  <!-- MIRROR-ONLY --> ... <!-- /MIRROR-ONLY -->   keep ONLY if a github/gitlab mirror is configured.
                                                   For local-only, DELETE every MIRROR-ONLY block
                                                   (what remains is the correct local-only manual).
  <!-- CR-ONLY --> ... <!-- /CR-ONLY -->           keep ONLY if spec-change requests are on
                                                   (config.json cr.enabled = true). Else DELETE the block.
Delete THIS comment block after filling.

Doc style: plain English, brief, no em-dashes (humanizer). This is a human-read manual, not a spec.
-->

# Running {{PROJECT}}

This repo is driven by **specseed**. The spec lives under `.specseed/spec/`, the work
breakdown under `.specseed/project_management/`, and implementation agents pick up work
from there one issue at a time. You supervise: you start and stop the work, approve the
risky steps, and change the plan when it needs changing.

You do **not** write the code by hand here (you can, but the point is to let agents do
it against the spec). This README is the operator's manual: how to run the agents, how
to stop them, what you control. Backend: **{{BACKEND}}**.

## The map

```
.specseed/
  README.md            you are here
  spec/                WHAT/WHY/HOW: vision, srs, sad, sdd, reqs.json. The contract.
  project_management/  the work: ROADMAP.md, TIMELINE.md, epics/ tickets/ issues/ sprints/
                       APPROVALS.md  -> things waiting on your sign-off
  memory/              config + runtime state (config.json [portable], remote.json [per-repo, mirror], runner.ctl, runner.log)
  scripts/             tooling. agents_runner.py (run the loop) + add_work.py (add a manual item) are yours to run; core/ + remote/ are agent-run.
```

Source of truth is the **folders** on disk. The `*.json` files (`issues.json`,
`tickets.json`, `sprints.json`) are generated from them, never hand-edited.

Read `ROADMAP.md` for the plan (phases, epics, ticket titles with `(X/Y complete)`
counts). Read `TIMELINE.md` for the sprint order. That is the whole status picture.

## How work happens

An implementation agent reads the root `CLAUDE.md` (its operating contract), claims the
next ready issue, implements it on its own branch off `{{INTEGRATION_BRANCH}}`, and
moves to the next. Dependencies are respected automatically: an issue is only claimable
once the issues it depends on are done.

### Run it

One always-on process, the **runner**, drives the work loop: it claims the next ready
issue, runs it via your local Claude Code CLI (no API key needed), and repeats. It logs
to `.specseed/memory/runner.log`. One machine, single writer: keep exactly one running.

```bash
python {{RUNNER}} &        # start in the background
```

<!-- MIRROR-ONLY -->
Each pass it also reconciles the github/gitlab mirror and reads commands from the CONTROL
issue (see the mirror section below).
<!-- /MIRROR-ONLY -->

Prefer to step through by hand? Skip the runner and run `claude` in the repo root, then
tell it to claim and implement the next ready issue per `CLAUDE.md`. It builds one issue
and stops; run it again for the next. `/loop` in Claude Code repeats that for you.

## Core commands

Control the runner with no daemon, through a control file the loop checks every pass.

| want | how |
|------|-----|
| **start** | `python {{RUNNER}} &` |
| **pause** (finish current issue, then idle) | `echo pause > .specseed/memory/runner.ctl` |
| **resume** | `echo run > .specseed/memory/runner.ctl` |
| **stop** (graceful: finish current, then exit) | `echo stop > .specseed/memory/runner.ctl` |
| **stop now** | `Ctrl-C`, or `kill <pid>` |

**Pause vs stop:** pause keeps the process alive (it just stops claiming new work); stop
ends it. If a run hits a usage or session limit, the runner backs off for a cooldown
(default 30 min) and retries on its own.

### Add an out-of-band item (bug / urgent fix / chore)

To inject work that isn't in the plan, use `add_work.py` — it scaffolds a ticket + one
issue and slots it in without re-solving the schedule:

```bash
python .specseed/scripts/add_work.py        # prompts for title/type/priority/...
# or non-interactively:
python .specseed/scripts/add_work.py --title "Login 500 on empty password" \
    --type bug --priority high --component api --effort 0.5
```

A **high**-priority item lands in the current sprint and jumps to the top of the queue;
**medium/low** go to the backlog for the next sprint. Either way the runner picks it up
on its next pass — no replan needed.

<!-- MIRROR-ONLY -->
From your phone, comment a verb on the pinned **CONTROL** issue instead (the runner
checks it every pass): `pause`, `resume`, `status` (state, sprint, in-flight, ready
count), `sync` (force a reconcile), `claim-next` (run the next issue now), `kill` (stop
just the current build, keep the loop). Full verb list is in the mirror section.
<!-- /MIRROR-ONLY -->

### Quick status check

```bash
python .specseed/scripts/core/issue_info.py <ISSUE-ID>   # one issue + its parent ticket + reqs
```

`ROADMAP.md` and `TIMELINE.md` are the human dashboards. Open them first.

## Approvals (you sign off the risky steps)

Some actions are gated: the agent will **not** do them without you. When it hits one, it
parks that work (writes the request, sets the issue to `awaiting_approval`) and moves on
to other ready work. Nothing silently waits on you while other work could proceed.

Gated categories and their level (block / surface / auto) live in
`.specseed/memory/config.json` and are rendered into `CLAUDE.md`. Typical blocks:
installing dependencies, destructive data ops, anything that leaves the repo (deploy,
publish), writing outside the repo, running containers or heavy compute.

To see and resolve what is waiting:

```bash
python .specseed/scripts/core/approvals_render.py   # refresh the pending index
```

Then read `.specseed/project_management/APPROVALS.md`. To resolve, spin up an agent and
say `/specseed approve` (walk every pending one), or resolve one directly:

- `/specseed approve <ID> [option] [note]`  proceed
- `/specseed reject <ID> <note>`            do not do it
- `/specseed hold <ID> <note>`              not now

<!-- MIRROR-ONLY -->
From your phone, comment on the CONTROL issue: `approvals` (list pending),
`approve <APR-NNNN> [opt]`, `reject <APR-NNNN> <note>`, `hold <APR-NNNN>`. Or resolve a
gate right on the work issue carrying the `🔔` — comment `approve` / `reject <note>` /
`hold` there (the `APR-NNNN` is optional when that issue has only one open gate).
<!-- /MIRROR-ONLY -->

You cannot approve your own agent's work as that same agent. The approve step is a
separate actor on purpose, so a gate is a real gate.

<!-- MIRROR-ONLY -->
### Talk to an issue

Any comment on a work issue that ISN'T a verb is treated as a free-form note — an
instruction ("add more comments", "don't do it that way") or a question ("why did you
handle X like that?"). The agent reads the issue and the real code, then replies on that
issue. It works within the issue's existing scope; if your note actually wants a
spec/scope change it points you to a `change-request`, and brand-new work to `add_work`.
Decisions still go through the `approve`/`reject`/`hold` verbs — the free-form note is for
asks and questions, not sign-off.
<!-- /MIRROR-ONLY -->

## Changing the plan

Spec and plan changes go through specseed, not by hand-editing settled docs (editing
them by hand desyncs the traceability the agents rely on).

| command | use it for |
|---------|-----------|
| `/specseed tweak` | a one-line change (add a requirement, change a priority) |
| `/specseed adapt` | a real change to the spec, including reopening settled docs |
| `/specseed plan-next` | break down the next slice of the roadmap into work |
| `/specseed configure` | change backend, git workflow, the approval gates, or the runner knobs |
| `/specseed migrate` | update this `.specseed/` tree after the specseed skill itself was upgraded (usually offered automatically at session start) |

If an agent thinks a settled spec doc is wrong mid-build, it stops, writes a
`spec_concern.md` next to its issue, and tells you to run `/specseed adapt`. It does not
edit the spec itself.

<!-- CR-ONLY -->
## Spec-change requests (CRs)

Sometimes the thing you want changed is the **spec**, not the work. A spec-change request
(CR) is how you ask for that. It is not a normal ticket: a CR does not satisfy a
requirement, it rewrites them and then regenerates the work below.

**File one:**
<!-- MIRROR-ONLY -->
- Remote: open an issue labeled `change-request`. Title it with what you want changed; put
  the request in the body. The runner picks it up.
<!-- /MIRROR-ONLY -->
- Local: `python .specseed/scripts/add_change_request.py` (prompts for a title and the
  request text).

**What happens next.** An urgent CR **pauses sprint work** until it is resolved. The runner
stops claiming new issues (it finishes whatever is mid-flight first) and switches to handling
the CR on its own isolated branch.

<!-- MIRROR-ONLY -->
The conversation happens **on that CR's own issue**. The system asks clarifying questions and
posts a drafted plan there, as comments. **Nothing changes until you reply approving it.**
Just comment back like you would to a person.

- **Approve:** comment your approval on the CR issue (for example "approved"). Only then does
  the system regenerate the spec and the work, merge the change, and resume sprint work. The
  new urgent work jumps to the top of the queue.
- **Reject:** comment a rejection. The CR's branch is discarded and nothing in the spec
  changes.

**Where to see status:** the CR's own issue (you get notified on new comments), plus the
CONTROL issue: `status` rolls up open CRs ("CRs: 1 open, CR-0001 awaiting you") and `crs`
lists them all with their state.
<!-- /MIRROR-ONLY -->

The system never proceeds through a spec change on its own. If anything is unclear, or the
change would invalidate work already done, it stops and asks rather than guessing.
<!-- /CR-ONLY -->

**Reusing your setup across repos.** `.specseed/memory/config.json` holds only "how you
work" (gates, git workflow, backend choice, runner knobs) — no project-specific data — so
you can copy it into another repo's `.specseed/memory/` to start from your usual setup
(then `/specseed configure` to fill in repo-specific bits). Do **not** copy `remote.json`:
it is per-repo state (the target repo, issue map, cursors) and is recreated per project.

<!-- MIRROR-ONLY -->
## The github/gitlab mirror

The mirror is a **mirror**, plus a command channel. Your local `.specseed/` is always
the source of truth. The runner projects local state onto the remote each pass.

It maintains a few permanent dashboards as issues:

| issue | what it shows |
|-------|----------------|
| **ROADMAP** | the roadmap, mirrored |
| **TIMELINE** | the sprint schedule |
| **CONTROL** | your command inbox. Its top post is a cheatsheet of the verbs. |
| **SPRINT** | the current sprint, rewritten each sprint |

On GitHub these three (ROADMAP / TIMELINE / CONTROL) are pinned. GitLab cannot pin, so
they exist but are not pinned.

What you can do from the remote:
- **Comment a verb on the CONTROL issue** (see the command table above): `status`,
  `pause`, `resume`, `kill`, `sync`, `claim-next`, `approvals`, `approve`, `reject`,
  `plan-next`, `adapt <text>`.
  `adapt <text>` pauses claiming before it runs; comment `resume` after reviewing the result.
- **Open new issues** for bugs or requests. The runner picks them up.
- **Draft without intake:** add an ignore label (`draft`, `ignore`, `specseed:ignore`,
  `changes-requested`, `needs-more-info`, `needs-triage` by default). The runner skips
  that issue until the label is removed.

What you should not do:
- **Do not hand-edit the mirrored issues.** Manual edits get reverted to match local,
  with a note. Drive changes through the CONTROL verbs or locally with `/specseed`.

Only allowlisted users can run CONTROL commands (default: the token owner). The channel
is for control, not arbitrary shell.

Sync lag is roughly 30 to 60 seconds plus API latency, so a phone command takes a moment
to land.
<!-- /MIRROR-ONLY -->

## Where things are when something breaks

- `.specseed/memory/runner.log`  the runner's log (start here when a build misbehaves)
- `.specseed/memory/runner.ctl`  the control file (`run` / `pause` / `stop`)
- `.specseed/memory/config.json`  your portable config: what is gated, the git workflow, backend + runner knobs
- `.specseed/project_management/APPROVALS.md`  work parked on your sign-off
- A stuck issue: `python .specseed/scripts/core/issue_info.py <ID>` shows its state.
  Claims older than a few hours are auto-recovered on the next claim.

To re-run the work-layer tooling by hand after editing folders (the agent normally does
this for you), assemble bottom-up then validate:

```bash
python .specseed/scripts/core/issues_assemble.py
python .specseed/scripts/core/tickets_assemble.py
python .specseed/scripts/core/sprints_assemble.py
python .specseed/scripts/core/issues_validate.py    # + tickets_validate.py, sprints_validate.py
```
