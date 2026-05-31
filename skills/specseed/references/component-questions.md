# Component questioning subroutine

Used by bootstrap stage 4 (per-component pre-SRS probing) and adapt mode for changed components only.

Load `references/question-protocol.md` first — it owns the question format and round mechanics.

## When fires

Per component, AFTER component-split decision is known, BEFORE drafting that component's SRS. Don't draft anything for a component until its questioning rounds are done and user OKs ending the stage.

## Theme palette

Pick 2–4 relevant themes per component. Announce upfront via the themes-upfront rule. Typical palette:

1. **Scale & load** — does it handle X users / Y rps / Z data? Affects horizontal scaling, caching, sharding decisions
2. **Persistence & state** — what survives restart? Where stored? Consistency requirements?
3. **External interfaces** — who calls it (clients, other components, external services)? Who does it call? What contracts?
4. **Failure modes** — what breaks under stress? How does it degrade? Retries? Circuit-breakers? Idempotency?
5. **Security & access** — authn, authz, data sensitivity, audit, secrets handling
6. **Operations** — logs, metrics, alerts, deployment shape, rollback
7. **Constraints** — tech stack lock-in, regulatory (GDPR/HIPAA/etc), team familiarity, budget, latency floors

Don't always use all. For a small CLI tool maybe just theme 3 + 4. For a payments service: 2, 4, 5, 7.

**Operations theme triggers deployment.md.** If theme 6 is selected AND the user's answers indicate operational concerns matter (custom deploy steps, runbook procedures, ops handoff), note in `session_state.md`: `deployment.md needed`. Bootstrap stage 14 will create it.

## Question phrasing rules

Questions should be intuitive to a generic technical reader, not a domain-narrowed specialist. Technologies in parentheses when they help.

**Good:**
> **1. Does this need to handle millions of users, or are hundreds enough?**
>
> (affects whether we need horizontal scaling, caching layers, async job queues)
> - **A)** Hundreds of concurrent users max
> - **B)** Tens of thousands
> - **C)** Millions+
>
> Confidence: A 60% / B 30% / C 10%
> Suggestion: **A**. Project is internal tool per context.

**Bad (technical word salad, presumes answer):**
> **1. Should we implement a Redis-backed distributed cache with consistent hashing for horizontal scalability under load?**

## Pacing

Default: **2 rounds × 4 Qs per component**.
Min: 1 round × 3–4 Qs (trivial components).
Max: 4 rounds × 6 Qs (genuine megacomponent — rare).

Apply ranking gate (from `question-protocol.md`) before each round ≥2.

## Don't move forward without explicit approval

After each round, wait for user OK / overrides / free text. Do not draft next round automatically. Do not begin SRS drafting until user OKs ending the stage for that component.

## Output

After all rounds done for component C, write component summary to `session_state.md`:
```
## Component: <C>
- Scale answer: <locked>
- Persistence answer: <locked>
- ...
- Other notable free-text from user: <...>
- (optional) deployment.md flag: yes/no
```

This summary drives the SRS draft for that component.

## Compression hook

Per-component done = good semantic checkpoint to propose context compression. Note in `session_state.md`:
```
## Compression note <timestamp>
- Just finished component <C>. Component summaries up to <C> are above. SRS not yet drafted.
```

After compression, reread `SKILL.md` then `session_state.md` to resume.
