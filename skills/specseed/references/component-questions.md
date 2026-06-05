# Component questioning subroutine

Used when drafting a component's SRS/SDD (adapt cold-start per component; adapt for
changed components only; adopt for behavior the code can't explain). It owns *which
concerns to probe per component* and *how to phrase a question*. Load
`references/question-protocol.md` first — that owns the round format and mechanics.

This worker is **non-interactive**: you resolve each theme from evidence first and
only raise a question **round** (async comment in runner mode, live in chat) for the
themes that stay material and unresolved. The discipline below is the same one the
old interactive skill used; the difference is you answer most of it yourself before
asking anything.

## Theme palette

Pick the **2–4 themes that matter** for the component; ignore the rest. Announce the
chosen themes upfront (themes-upfront rule).

1. **Scale & load** — does it handle X users / Y rps / Z data? Drives horizontal
   scaling, caching, async work.
2. **Persistence & state** — what survives restart? Where stored? Consistency needs?
3. **External interfaces** — who calls it (clients, other components, external
   services)? Who does it call? What contracts?
4. **Failure modes** — what breaks under stress? How does it degrade? Retries,
   idempotency, circuit-breakers?
5. **Security & access** — authn, authz, data sensitivity, audit, secrets.
6. **Operations** — logs, metrics, alerts, deploy shape, rollback.
7. **Constraints** — stack lock-in, regulatory (GDPR/HIPAA), latency floors, budget,
   team familiarity.

A small CLI may need only themes 3 + 4. A payments service needs 2, 4, 5, 7.

**Operations theme triggers `deployment.md`.** If theme 6 is selected AND the
evidence shows operational concerns matter (custom deploy steps, runbook procedures,
ops handoff), the spec gets a `deployment.md`. Note the intent in `plan.json`.

## Resolve each theme from evidence first

For each selected theme, answer it in this order; stop at the first that resolves it:

1. The spec-change post (title, body, comments).
2. The existing spec under `<specseed_dir>/spec/`.
3. (adopt) the code: what it actually does is the answer.
4. A sensible, clearly-stated default for the project's apparent size.

A theme resolved from evidence becomes SRS rows + SDD prose directly. **Record any
default you assumed** in `plan.json` so a human can correct it. Only a theme that is
**material AND still unresolved** after those four becomes a question.

## Question phrasing rules

Questions should be intuitive to a generic technical reader, not a domain-narrowed
specialist. Technologies in parentheses when they help.

**Good:**
> **1. Does this need to handle millions of users, or are hundreds enough?**
>
> (affects whether we need horizontal scaling, caching layers, async job queues)
> - **A)** Hundreds of concurrent users max
> - **B)** Tens of thousands
> - **C)** Millions+
>
> Confidence: A 60% / B 30% / C 10%
> Suggestion: **A**. Project reads as an internal tool from context.

**Bad (technical word salad, presumes the answer):**
> **1. Should we implement a Redis-backed distributed cache with consistent hashing
> for horizontal scalability under load?**

## Pacing

Default: **2 rounds × 4 Qs per component**, but only after the evidence pass — most
components resolve to **0–1 rounds**.
- Min: 1 round × 3–4 Qs (any component that still has open material themes).
- Max: 3 rounds × 6 Qs (a genuine megacomponent — rare; hard ceiling).
- Apply the **ranking gate** (`question-protocol.md`) before each round ≥2.
- Apply the **auto-skip rule** HARD: never ask what the post, the spec, the code, or
  an obvious default already answers.

**Depth tier override** (adapt cold-start, see `routes/adapt.md` "Depth dial"):
- `lite` — at most 1 round × ≤4 Qs, 2 themes.
- `incremental` — deep-question only the components the **first increment touches**;
  out-of-scope components get a 1-line SRS placeholder, deferred to `plan-next-sprint`.
- `standard` — as above (default).

## Output

The resolved themes (whether answered from evidence or from the human's round reply)
drive the component's SRS rows and SDD prose. In `plan.json` record, per component:
the chosen themes, each theme's resolution (evidence / default-assumed / asked), any
default assumed, and any still-open async question. A re-triggered run reads this back
so it never re-probes a settled component.
