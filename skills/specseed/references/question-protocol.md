# Question protocol (shared)

Format for every clarification round this skill raises. Used by
`component-questions.md` and any route that must ask the human something before it
can spec safely.

## Two channels, one format

The *format* below is identical in both; only delivery + when-you-stop differ.

- **Runner mode (async).** You cannot interview a human live. A round is posted as
  a **comment (or comments) on the spec-change request post**, then you swap the
  request to `spec-change:status:awaiting_input` and **stop**. The next poll
  re-triggers the route with the human's reply in the post comments, where you read
  the answers and continue. A round may carry **several questions** — the headless
  constraint is "one round, then park and wait," NOT "one question." Do not loop
  live; post the round, park, stop.
- **Chat mode (live).** A human is present and types. Ask the round, wait for the
  reply in the conversation, continue. No park, no comment.

## Round opener

1–3 lines max. Format:

```
**Round X — <theme name>**

<optional 1-2 lines context for the round overall — only if needed>
```

**Context for each question goes INSIDE the question.** No big top-context block
that maps to individual questions.

## Per-question format

Strict template. Use exactly:

```
**N. <short question stem>**

<1-2 lines inline context if needed>
- **A)** <option text>
- **B)** <option text>
- **C)** <option text>   // 2-3 options typical

Confidence: A NN% / B NN% / C NN%
Suggestion: **<letter>**. <one-line rationale>
```

Rules:
- Numbering is **within-round** (1., 2., 3., …). Not `Q1.2`. Round number is in the opener
- Always full word `Confidence:` and `Suggestion:`. Never `Conf:`, never `Sug:`
- Confidences sum to 100
- One option suggested, with a short rationale
- Options mutually exclusive, phrased short
- Counterargument for non-suggested option allowed (1 line) if non-trivial
- Questions phrased at a level a generic technical reader can grasp. Technologies in parentheses if needed. No technical word salad

The confidence/suggestion shape is the point: it forces the skill to commit to a
defensible default and expose its uncertainty instead of asking open-ended. Even in
async runner mode, the human can reply `OK` to take all suggestions, so a
well-suggested round often costs the human one word.

## Round closer

### First round of a request (verbose, teach-once)

End the very first round with:

```
**How to respond examples:**
- `OK` → take all suggestions as locked
- `1. OK, 2. ...` → accept suggestion for question 1
- `1. B, 2. ...` → override question 1 with this letter option
- Free text / discussion any time
- (runner mode) reply on this post; the worker resumes on the next poll
```

### Every subsequent round (terse, status-bar)

```
`OK` lock all / free text always
```

## No-noise-summary rule

After the human accepts a round:
- If they replied `OK` (all suggestions taken) on first try → **NO summary**. Move
  directly to the next round or stage.
- If they overrode any option, or the round had back-and-forth → 1-line summary of
  what got locked, then move on.

**Never re-recite locked decisions for ceremony.** Locked = locked, recorded in
`plan.json` (see Memory cadence), no need to repeat.

## Themes-upfront rule

At the start of any multi-round stage, announce the planned themes before round 1:

```
Themes planned for <stage>:
1. <theme>
2. <theme>
...
```

The human may add/drop/reorder themes before round 1 starts. After round 1, theme
changes need brief justification.

## Anti-max-bias rule

Default LOW. The range "1–3 rounds × 4–6 Qs" is a *range*, **not a target**.
**Hard cap: 3 rounds** per stage. Human interaction is the real cost, not artifacts.

**Explicit override:** request names a round count ("2 rounds of questions") →
that count wins over the sizing heuristic and the ranking gate (cap 3 still
holds). Run exactly that many rounds; fill late rounds with the best remaining
medium-impact Qs instead of skipping.

Sizing heuristic by task scope:
- Small/clear: 1 round, 3–4 Qs total
- Medium: 2 rounds × 4–5 Qs
- Large/complex: 3 rounds × 5–6 Qs (rare; this is the ceiling)

**Depth tier (adapt cold-start) tightens this further** — see `routes/adapt.md`
"Depth dial". `lite` caps stages at 1 round; `incremental` only deep-questions the
first increment's scope.

**Misc-bucket rule:** if 1–2 small Qs remain that don't fit any theme, group them
into a "Misc" mini-round at the END of the stage — do NOT spawn 1-Q themes.

**Ranking gate** before each round ≥2:
1. Rank candidate Qs by impact (high / medium / low)
2. Drop low-impact Qs
3. If <3 medium-or-high Qs remain → end the stage, do not run the round
4. Declare the ranking inline before posting the round (1 short paragraph)

## Auto-skip rule (obvious Qs)

Don't ask obvious questions. Two filters.

### Filter 1: don't generate trivial-tech-listing Qs in the first place

If you find yourself drafting a Q whose options are just a list of technology
choices where one is clearly right given context (e.g. "storage: SQLite, files,
Postgres?" for a small CLI tool), DON'T add it to the round. State the implied
choice inline during drafting:

> "Drafting SRS assuming SQLite for persistence — flag if not."

Questions should be **high-level concerns** (does this scale to millions? does it
need to survive crash? does it integrate with X?), not tech enumerations. Tech
choices follow from concern answers; the implementing agent picks the tech.

### Filter 2: auto-declare obvious Qs that pass the ranking gate

For Qs that DO pass the impact ranking but happen to have a high-confidence obvious
answer:

**If Suggestion confidence is ≥90% AND the non-suggested options are not
destructive/irreversible if taken silently** → don't ask. Declare the choice inline
and proceed:

> "Going with X (high confidence given context). Flag if not."

"Destructive/irreversible" guard means: never auto-declare on data deletion,
schema-breaking change, security posture, settled-doc reopen, license/legal choices,
anything user-facing brand or naming. **Always ask on those, even at 99% confidence.**

Apply this filter when drafting each Q. If it triggers, the Q is replaced by a
1-line declaration in the round opener or stage rollup, not posted as a numbered Q.

## Memory cadence (record decisions in plan.json)

This worker runs fresh each invocation (a runner re-trigger starts a new context),
so durable decisions live in the request dir, not a scratch file. Record under a
`questions` key in `plan.json`:

- **A round with overrides or free-text** → write the locked outcomes:
  ```json
  "questions": {"<stage>/round-<N>": {"1": "B", "2": "free: keep auth+session together"}}
  ```
  Decisions only — not the full Q+A transcript.
- **A pure-OK first try** → skip the round entry entirely.
- **Auto-declared choices (filter 2)** → record so a later pass can see what was
  assumed without re-asking:
  ```json
  "auto_declared": ["SQLite for persistence (90%+, not contradicted)"]
  ```
- **At each stage boundary** → a one-line-per-decision rollup, regardless of how the
  rounds went. The rollup is the resumable state if the route is re-triggered.

Because runner mode parks between rounds, `plan.json` is also how a re-triggered run
knows which rounds it already asked: read it back before posting a new round, so you
never re-ask an answered question.
