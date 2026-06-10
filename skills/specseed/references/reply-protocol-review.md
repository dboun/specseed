# Reply protocol — review route

How the review route replies when reviewing a finished issue. Builds on the shared base
for the form; this file adds the review-specific rules.

## Mandatory skill reads

| Read | Why |
|------|-----|
| `references/reply-protocol-base.md` | the shared form (modes, structured/natural) |

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## The review reply is a verdict + findings

A review posts ONE reply: a `main_body` report on the issue post, as the `Code review`
comment the impl route reads on rework. It states:
- the **verdict** with a confidence (the runtime resolves the gate from it);
- concrete, actionable **findings** (what is wrong, where, why), or a clean pass.

Questions are rare here — review judges what is in front of it; it does not interview. If
something is genuinely undecidable, say so in the findings rather than opening a round.

## No approval asks here

The review route NEVER makes an approval ask. **Gated reviews are handled through code**:
confidence is the primary gate and `difficulty:` the modifier, and the runtime
(`executing/advance.py`) decides auto-approve vs `awaiting_approval` from your verdict +
the configured gates. You report; you never grant, request, or post an approval. So this
route has no `approval` section.
