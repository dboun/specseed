# tweak

The smallest change. One req added, one priority changed, a typo, one status
flip. A single-doc edit and at most one matching work post. Anything bigger is
`adapt`.

Read `references/spec-change-protocol.md` and `references/remote-posts.md`. You
will rarely need `work-breakdown.md`.

## Fires when

`spec-change:tweak` on a request post AND the change is genuinely small. If, once
you localize it, the change fans out across multiple spec files or multiple work
posts, or it forces reopening settled design, **treat it as adapt** instead:
follow `routes/adapt.md` for this request. Note the bump in `plan.json`.

## Flow

1. **Locate.** From the request post, find the one spec file (or the one work
   post) to touch. If ambiguous which, use async clarification.
2. **Edit.** Apply the change in place under `.specseed/spec/`:
   - SRS req add/change -> edit the table row; regenerate `reqs.json`.
   - priority / wording change -> edit in place, same id.
   - typo in `vision.md` / prose -> fix; re-check the em-dash ban.
3. **One work post, maybe.** The common pairing is "add this req AND a ticket for
   it". That stays a tweak: one `creates` (ticket at `:status:todo`,
   `satisfies_reqs` the new req) or one label/comment change in `plan.json`. A
   pure status flip on an existing post is a label swap.
4. **Finish.** `plan.json` -> `apply.py` -> `enqueue_spec_change_run(...)` ->
   stop. If the tweak ended up touching nothing on the remote (e.g. a doc-only
   typo fix), still write a `plan.json` that just moves the request post to
   `done` (a label swap) and enqueue, so the request closes out.

## Escalate to adapt when

- the edit reopens a settled v1 design doc,
- it touches more than one spec file or more than one work post (beyond the
  one-req + one-ticket carve-out), or
- the request post asks a broader "what about..." that needs real reconciliation.

Escalating means: stop following this file, follow `routes/adapt.md` for the same
request id. Carry over what you already located.
