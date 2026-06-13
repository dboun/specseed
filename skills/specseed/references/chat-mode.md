# Chat mode (note)

The skill also runs in a plain Claude chat (web / app): no scheduler, no runtime, no
tracker, no remote. A human is present and types. Everything in
`spec-change-protocol.md` and the routes still holds for *what* you produce; only the
input source and the handoff differ.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/spec-change-protocol.md` | what you produce (staged spec + `plan.json` + `apply.py`) is identical to runner mode |
| `references/reply-protocol-base.md` | clarify live (chat is the no-park delivery mode) |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## Notes

**Detect it:** no scheduler handed you a `spec-change:<route>` label + request id, AND
the runtime is absent (`specseed_runtime` not importable, no `tracking_local.db`, no
data root named in the prompt). Otherwise you are in runner mode (the default). When
unsure, assume runner mode.

**Inputs** come from the conversation, not `resolve_local()`: the request is what the
human wants (+ the route — infer and confirm if unnamed: adopt = code present, no
spec; else adapt/tweak/inject/plan-next-sprint per the ask). Attached files / a
pointed-at repo are the existing spec + code. No tracker means "current work posts" may
be empty (greenfield) — fine. Mint a local slug for the request id.

**Clarify live**: chat is the live (no-park) delivery mode in `reply-protocol-base.md` —
ask in the conversation, wait for the reply, continue; no async comment. That doc owns
the reply form (chat = natural prose, no reaction prompts) and the question discipline.

**Outputs** are the same three artifacts laid out like an installed tree, so the human
can drop them into a repo or resume in Claude Code. Spec edits are STAGED (never written
to live `spec/`), same as runner mode:

```
spec-change/<id>/spec/...     # STAGED spec edits, mirroring the live spec/ tree
spec-change/<id>/plan.json    # the work-breakdown delta (incl. settle_docs)
spec-change/<id>/apply.py     # the reconcile script (inert here)
```

Produce `apply.py` exactly as the protocol's header says; it does not run here (no
remote, no executor) but ships so the human can run it later under a real runner. Keep
the approval gate intact in `plan.json` (issues planned `:status:todo`, the `APR-NNNN`
intent recorded) so the runtime gates it when wired in. Do not "approve" in chat.

**Handoff: one zip.** Bundle the working dir into a single `.zip` and offer it for
download. Don't paste spec / plan / script contents into the chat — the file is the
deliverable; the chat gets a short summary + the download. Nothing to enqueue anyway (the
no-enqueue rule is universal), no `apply.py` run, no git.
