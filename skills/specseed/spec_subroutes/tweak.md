# tweak

## Short description

The smallest change. One req added, one priority changed, a typo, one status
flip. A single-doc edit and at most one matching work post. Anything bigger is
`adapt`.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/spec-change-protocol.md` | the spec spine: outputs, gate, async clarification, tracking contract |
| `references/remote-posts.md` | the post/label model |
| `references/reply-protocol-spec.md` | clarification-round format |
| `references/chat-mode.md` | when run in chat (no runtime) |
| `references/work-breakdown.md` | the one-ticket carve-out + when to escalate to a real breakdown (adapt) |
| `templates/spec_doc_templates/` | the doc format for the one edited spec file (SRS row, vision, etc.) |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|
| `requirements_generate_json.py` | regenerate `reqs.json` when the tweak adds/changes an SRS row |
| `dependencies_validate.py` | validate `plan.json.creates` when the tweak plans one issue |

## Fires when

`spec-change:tweak` on a request post AND the change is genuinely small. If, once
you localize it, the change fans out across multiple spec files or multiple work
posts, or it forces reopening settled design, **treat it as adapt** instead:
follow `spec_subroutes/adapt.md` for this request. Note the bump in `plan.json`.

## Flow

1. **Locate.** From the request post, find the one spec file (or the one work
   post) to touch. If ambiguous which, use async clarification.
2. **Edit (staged).** Read the live doc for context, write the changed copy into the
   staging tree `<specseed_dir>/storage/spec-change/<id>/spec/<same relative path>`
   (`spec_change_spec_dir(id)`); never write to live `spec/`:
   - SRS req add/change -> edit the table row; regenerate `reqs.json` (staged too).
   - priority / wording change -> edit the staged copy, same id.
   - typo in `vision.md` / prose -> fix in the staged copy; re-check the em-dash ban.
3. **One work post, maybe.** The common pairing is "add this req AND a ticket for
   it". That stays a tweak: one `creates` (ticket at `:status:todo`,
   `satisfies_reqs` the new req) or one label/comment change in `plan.json`. A
   pure status flip on an existing post is a label swap. If the one planned post
   is an **issue** (claimable), it is planned `:status:todo` (with its `type:`
   label) and the run **proposes** with an `APR-NNNN` like every work-creating route
   (plan-first: nothing is created until approval).
4. **Finish.** A tweak that plans an issue OR edits the spec (even a doc-only fix, which
   now STAGES the change) is a **proposal**: write the staged spec + `plan.json` (with
   `plan_summary` + `apr`) + `plan.json`, then stop. The runtime gates it: posts the
   summary + `APR-NNNN`, parks `awaiting_approval`, and on approval promotes the spec and
   applies `plan.json`. The ONLY direct (ungated) path is a **clarification round** that
   touches only the request post (a comment + `awaiting_input`); the runtime runs that
   straight away. There is no "label swap + close" direct path any more.

## Escalate to adapt when

- the edit reopens a settled v1 design doc,
- it touches more than one spec file or more than one work post (beyond the
  one-req + one-ticket carve-out), or
- the request post asks a broader "what about..." that needs real reconciliation.

Escalating means: stop following this file, follow `spec_subroutes/adapt.md` for the
same request id. Carry over what you already located.
