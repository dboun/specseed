# Ticket formation subroutine

Used in bootstrap stage 10 and adapt mode when new tickets are needed.

## Inputs

- `.specseed/spec/reqs.json` (settled reqs, generated from SRS)
- `.specseed/spec/sad.md` and `.specseed/spec/sdd.md` (or per-component variants)
- `.specseed/memory.md` (recent context — component summaries from stage 4)

## Output

`.specseed/spec/tickets.json` — **source of truth, written directly** (no source markdown). Schema:

```json
{
  "FEAT-0001": {
    "title": "User signup endpoint",
    "description": "1-3 sentence what + why",
    "type": "feature",
    "component": "api",
    "effort_hours": 4,
    "depends_on": [],
    "satisfies_reqs": ["SRS-API-001", "SRS-API-002"],
    "acceptance_criteria": [
      "POST /signup returns 201 with user id",
      "Duplicate email returns 409 with descriptive error"
    ],
    "artifacts": {
      "touches": ["src/api/auth/", "migrations/"],
      "tests": ["tests/api/test_signup.py"],
      "migrations": ["migrations/0007_create_users.sql"]
    },
    "status": "todo",
    "claimed_at": null,
    "claimed_by": null,
    "notes": "",
    "milestone": "M1"
  }
}
```

### Field semantics

- `type`: `feature` / `bug` / `chore` / `spike`
- `depends_on`: other ticket IDs blocking this one
- `satisfies_reqs`: 1–8 req IDs this ticket proves
- `status`: `todo` / `in_progress` / `blocked` / `done` / `deprecated`
- `claimed_at`: ISO timestamp set when status moves to `in_progress`; `null` otherwise. Used by other agents to detect stale claims
- `claimed_by`: agent identifier (free-form string — agent name, harness, host, whatever your setup distinguishes by) set when status moves to `in_progress`; `null` otherwise
- `artifacts.touches`: directories/files this ticket modifies
- `artifacts.tests`: test file paths (when known; can be added during implementation)
- `artifacts.migrations`: OPTIONAL — paths to forward migration scripts (DB schema, file-format changes, persistent-state changes). Present only when the ticket changes data shape. See "Migrations" section below
- `milestone`: OPTIONAL field. Only present if user opted into milestones (`.specseed/spec/milestones.md` exists). See "Milestones" section below

### ID format

`<TYPE>-NNNN` zero-padded. Examples: `FEAT-0001`, `BUG-0042`, `CHORE-0007`, `SPIKE-0003`. Pick global numbering or per-type at session start, stick.

## INVEST mnemonic

Each ticket should be:
- **I**ndependent — minimal coupling to other tickets
- **N**egotiable — scope can shift in conversation
- **V**aluable — ships something user-visible OR risk-reducing (e.g. a spike)
- **E**stimable — you can guess hours
- **S**mall — 1–3 days human-equivalent work, OR one focused agent session
- **T**estable — has acceptance criteria with clear pass/fail

## Acceptance criteria ≠ requirements

- **Requirements** (in `srs.md`, IDs `SRS-...`): say what the *system* does. Persistent. Verified across project lifetime
- **Acceptance criteria** (in tickets, no IDs): say what proves *this ticket* done. Local to ticket. Often phrased as observable behaviors

A ticket may satisfy 1–8 reqs. Acceptance criteria typically 2–6 items.

Don't force criterion-to-req 1:1 — criteria demonstrate *the work landed*; reqs describe *what the system does*. Different jobs.

## Sizing heuristics (concrete)

Two checks, apply both:

**Check 1 (semantic):** if you can name 2–6 acceptance criteria with confidence, the ticket is right-sized.
- `<2` criteria → too small or trivial; merge with a related ticket
- `>6` criteria → too big; split into vertical slices

**Check 2 (mechanical):** estimated effort 1–4 hours for one focused agent session.
- `>4h` → split, even if check 1 passes

Apply check 1 first; if it passes but check 2 fails, split anyway. Different failure modes — check 1 catches scope creep, check 2 catches optimism.

## Vertical slice heuristic

Cut slices such that each ticket produces **observable behavior change in one run/request**.

This is the test. If you can describe what the user (or upstream system) sees differently after the ticket ships in one sentence, it's a valid slice. If you have to describe internal state changes that aren't visible from outside, it's a horizontal layer, not a slice — merge with the slice that exposes it.

Slices may legitimately skip layers (some don't touch UI; some are backend-only). The rule is observability, not "touches every layer".

## Integration tickets

Insert an integration ticket **before any merge point where ≥2 parallel branches converge in the DAG**.

Why mechanical: `tickets_analyze.py` makes merge points visible — any ticket whose `depends_on` lists 2+ tickets from different parallel branches. Those are integration risk points. An integration ticket between them surfaces the seam before downstream tickets pile on.

Not all merge points need one — small merges (both branches are 1–2 tickets each, same component) typically don't. Use judgment, but err on the side of inserting if branches span ≥2 components or ≥4 tickets each.

## Formation technique

1. **Topo-sort reqs** by `depends_on`. Identifies which reqs must be implemented before which others
2. **Group cohesive reqs** — reqs that change together belong in one ticket
3. **Prefer vertical slices** — per the heuristic above
4. **Apply sizing checks** — both check 1 and check 2
5. **Respect topo order** — can't schedule a ticket whose deps aren't done
6. **1–8 reqs per ticket.** More → split. Fewer than 1 → why does this ticket exist?
7. **Insert integration tickets** at parallel merge points per the heuristic above

## Critical path

After `tickets.json` written, run `.specseed/scripts/tickets_analyze.py` (user-provided). Returns critical path = longest dep chain. Determines minimum project duration.

- Schedule critical-path tickets first. Best agent, highest priority
- Parallel branches run alongside
- **"Important" ≠ "on critical path".** Parallel branches matter — they just don't extend total time
- If a parallel branch slips enough, may become critical path. Re-run after status changes

Surface critical path to user. User may rebalance if path is unreasonably long (often signals overly narrow tickets or artificial dependencies).

## Migrations

Tickets that change **data shape** (DB schema, persisted file format, on-disk state, config schema) need migration handling. Otherwise the change ships, deployment breaks, nobody knows why.

**Required for any data-shape-changing ticket:**

1. **`artifacts.migrations` field** — list paths of forward migration scripts. Validator can check the path exists once written
2. **Acceptance criterion**: include a criterion phrased "migration script committed at `<path>` and runs cleanly against current schema" (paraphrase as fits). Forces the human (or agent) verification step

Optional but recommended:
- Document rollback (idempotent down-migration if your tooling supports it, or a manual rollback note in `notes`)
- Note deployment ordering in `notes` if the migration must run before/after specific other tickets' deploys

For retired features (see `adapt.md` retirement sub-flow), cleanup migrations get their own `CHORE-NNNN` tickets with the same `artifacts.migrations` + acceptance-criterion treatment.

## Spike post-completion

Spike tickets (`type: "spike"`) are time-boxed research/exploration — answers unknown at start. Examples: "Postgres or DynamoDB for billing?", "Is library X fast enough?", "How does OAuth flow Y work?". End of spike = a learned answer.

**Before marking a spike `done`, the implementation agent MUST capture findings.** Otherwise the learning vanishes — future agents/humans won't read closed ticket internals. Use this template (write into the ticket's `notes` field, plain prose):

```
Spike report:
- Question: <what was being investigated>
- Investigation: <what was tried — sources, prototypes, benchmarks (1-2 lines)>
- Findings: <what was learned (1-3 lines)>
- Decision: <chosen path, or "no decision yet — see Followups">
- Followups: <ADR row? new req? SDD update? any of these → tell user to use /specseed>
```

Five fields, 3–10 lines total. Keep tight.

Then:

1. **If a decision was made** → use `/specseed` (adapt or tweak mode) to add an ADR row capturing the decision + justification
2. **If new or changed requirements emerged** → use `/specseed adapt` to draft them properly (with IDs, dependencies)
3. **If a design pattern surfaced that should be in the SDD** → use `/specseed adapt` to add the relevant SDD section
4. Only THEN mark the spike `done`

Do not auto-invoke adapt mode from a spike completion — the agent surfaces findings to the user with a recommendation, user invokes the skill explicitly. Auto-invoke would be too rigid; many spikes simply confirm an existing plan and need no further docs.

If user agrees the spike's findings need no doc changes (rare but possible), mark `done` and leave the spike-report `notes` as the only artifact. The note IS the spike's deliverable in that case.

## Milestones (OPTIONAL)

Only relevant if the user asked for milestones during the context pre-stage or later.

If opted in:
- Create `.specseed/spec/milestones.md` listing named milestones (M1, M2, ...) with:
  - Goal (one line per milestone)
  - Target date (if any)
  - List of ticket IDs included
- Add `"milestone": "M1"` field to each ticket in `tickets.json`
- After milestones defined, when surfacing critical path, also show per-milestone critical paths

If NOT opted in: omit the `milestone` field from tickets entirely. Don't create `milestones.md`.

## The art part (judgment calls)

Concrete rules above cover most cases. The remaining judgment calls:

- **Where to cut vertical slices when they span 2 components** — pick the slice that has the smaller cross-component contract. If contracts are equal, prefer the component the user's team is more familiar with
- **When to merge tightly-coupled tickets** — if two tickets have identical `satisfies_reqs` and one depends only on the other, consider merging. Tightly coupled = always shipped together; splitting just adds ceremony
- **When to defer a spike** — if a spike's question doesn't block anything on the critical path, defer it; if it does, schedule it immediately

When unsure, surface to user with a 1-line summary of the trade-off, don't ask open-ended.

## Validation

After writing or editing `tickets.json`, ALWAYS run `.specseed/scripts/tickets_validate.py`:
- Schema check
- ID uniqueness
- Dangling `depends_on` refs (ticket IDs not in tickets.json)
- Dangling `satisfies_reqs` refs (req IDs not in reqs.json)
- Enum value validation (type, status, priority where applicable)
- `artifacts.migrations` path existence check (if present)
- Claim-field invariants (status × claimed_at × claimed_by — see `tickets_validate.py` docstring)

Fix any issues before proceeding to critical-path analysis.
