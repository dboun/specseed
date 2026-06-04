# Component questioning (drafting checklist)

A checklist of dimensions to cover when drafting a component's SRS/SDD. This
worker is **non-interactive**: you do not ask a human live. Use the palette to
make sure you probed the right concerns from the spec-change post + the code, and
to decide what to ask **asynchronously** when a dimension is genuinely
unresolved.

## Theme palette

Pick the 2 to 4 that matter for the component; ignore the rest.

1. **Scale & load** — expected users / rps / data volume. Drives scaling,
   caching, async work.
2. **Persistence & state** — what survives restart, where stored, consistency
   needs.
3. **External interfaces** — who calls it, what it calls, the contracts.
4. **Failure modes** — what breaks under stress, degradation, retries,
   idempotency.
5. **Security & access** — authn, authz, data sensitivity, audit, secrets.
6. **Operations** — logs, metrics, alerts, deploy shape, rollback. If this
   matters, the spec gets a `deployment.md`.
7. **Constraints** — stack lock-in, regulatory (GDPR/HIPAA), latency floors,
   budget.

A small CLI may only need themes 3 + 4. A payments service needs 2, 4, 5, 7.

## Resolving a theme

For each selected theme, try to answer it from available evidence in this order:

1. The spec-change post (title, body, comments).
2. The existing spec under `.specseed/spec/`.
3. (adopt) the code: what it actually does is the answer.
4. A sensible, clearly-stated default for the project's apparent size.

Only when a theme is **material and still unresolved** after those, raise it via
**async clarification** (`spec-change-protocol.md`): one focused comment on the
request post, not a wrong assumption baked into the spec. Phrase questions for a
generic technical reader, not a narrow specialist; name technologies only when
they clarify.

## Output

The resolved themes drive the component's SRS rows and SDD prose. Record any
default you assumed (so a human can correct it) and any open async question in
`plan.json`.
