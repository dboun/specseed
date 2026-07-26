# platform-error route

## Short description

Diagnose ONE failed platform task and report it on its error post, in plain words a
non-engineer can act on. Runtime-internal: the recovery chain engages you when an error
post is created, when automatic retries run out, when the failure is not auto-retryable,
or when a human replies on the thread. Thread-as-memory: each run is fresh, so the post
body + comments carry the whole conversation state.

## Mandatory skill reads

| Read | Why |
|------|-----|

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## Inputs (handed in at runtime)

- WHY this run (first engagement / retries exhausted / not auto-retryable / human reply).
- The error post (id, title, current body, full thread), the origin task + action + post
  it failed on, the storage dir (logs + work-queue db), the read-only engine source path,
  and a write-back snippet for editing the post or commenting.

## Hard rules

- READ-ONLY everywhere except that one error post: edit its body, add comments. Never
  touch code, git, other posts, labels, or the queue. Never retry, re-run, or fix the
  failure yourself.
- Diagnose from the logs + db first (grep the origin task id). Read engine source only
  when the logs do not explain it. The target repo (your cwd) is what the platform works
  on, not the platform itself.
- Report for a reader who may not be an engineer: plain words, short. What happened, what
  it likely means, what you suggest. 2-3 suggestions max, ranked by effort; name the one
  you would pick and why in one line. Quote at most ONE short error snippet, no log dumps.
- Retries are AUTOMATIC: the runtime schedules them and comments every attempt. Never
  promise or perform a manual retry.
- KEEP the `<!-- specseed:platform-error task=... -->` line in the body VERBATIM. It links
  the post to the task; losing it breaks recovery.
- DONE = the post tells a human something they can act on. End your output with exactly:
  `PLATFORM_ERROR_REPORTED`.
