# Chat mode

The skill also runs in a plain Claude chat (web / app), with no runner, no
scheduler, no local tracker, no remote. The human is present and types. Output is
a **downloadable zip of the produced artifacts**, not a queued `apply.py`. This
file owns the differences. Everything in `spec-change-protocol.md` and the routes
still holds for *what* you produce; this changes *where input comes from* and
*how you hand off*.

## Detect the mode

You are in **chat mode** when BOTH:

- no scheduler handed you a `spec-change:<route>` label + request id (a human
  invoked the skill directly), AND
- the runtime is absent: `specseed_runtime` is not importable, or there is no
  `tracking_local.db` / `<specseed_dir>/storage/`.

Otherwise you are in **runner mode** (the default the rest of the docs assume):
read the local cache, emit `apply.py`, enqueue, stop. **Runner mode is unchanged
— do not apply chat-mode steps there.** When unsure, you are in runner mode.

## Inputs come from the conversation

No `resolve_local()`, no remote read. Plan from what the human gives you:

- The request = the conversation (what they want, the route).
- Existing work / spec = files they attach or a repo they point at. No tracker
  means "current work posts" may be empty (greenfield) — fine.
- **Route:** if the human did not name one, infer it (adopt = code present, no
  spec; adapt/tweak/inject/plan-next-sprint per the request) and confirm via the
  questions protocol.
- **Request id:** mint a local slug (`chat-<short-desc>` or a timestamp). It only
  names the request dir.

## Still non-interactive (form-wise)

Never pop a form. When you must clarify, ask through the **questions protocol** —
short, batched, decision-shaped questions — then continue. There is no remote to
comment on, so the async-comment clarification path does NOT apply here. Don't
guess past a material ambiguity; ask, then proceed.

## Outputs: artifacts mirroring `<specseed_dir>`

Write into a working dir laid out like an installed tree, so the human can drop it
into a repo or resume in Claude Code:

```
spec/...                                  # the spec edits (vision/SRS/SAD/SDD/adr.csv/reqs.json)
storage/spec-change/<id>/plan.json        # the work-breakdown delta
storage/spec-change/<id>/apply.py         # the reconcile script (inert here)
```

Produce `apply.py` exactly as the protocol's canonical header says. In chat there
is no remote and no executor, so it does **not** run here — it ships so the human
can run it later under a real runner. Same for the approval gate: still create
issues `:status:awaiting_approval` in `plan.json` and record the `APR-NNNN`
intent, so the gate holds when wired into a runner. Do not "approve" in chat.

## Handoff: a zip, never the output

- Bundle the whole working dir into **one `.zip`** and offer it as a downloadable
  file. **Never paste spec / plan / script contents into the chat** — the file is
  the deliverable; the chat gets a short summary + the download.
- Produce the zip **early and keep refreshing it**: the moment the first spec file
  exists, zip and offer; re-zip at each milestone (spec drafted, plan computed,
  apply.py written). The human should always have a current download.
- No enqueue, no `apply.py` run, no git. The run ends when the artifacts are
  bundled and offered.
