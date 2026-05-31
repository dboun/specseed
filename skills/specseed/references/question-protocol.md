# Question protocol (shared)

Format for all interactive question rounds in this skill. Used by bootstrap, adapt (reduced), and component-questions.

## Round opener

1–3 lines max. Format:
```
**Round X — <theme name>**

<optional 1-2 lines context for the round overall — only if needed>
```

**Context for each question goes INSIDE the question.** No big top-context block that maps to individual questions.

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

## Round closer

### First round of a session (verbose, teach-once)

End the very first round with:

```
**How to respond examples:**
- `OK` → take all suggestions as locked
- `1. OK, 2. ...` → accept suggestion for question 1
- `1. B, 2. ...` → override question 1 with this letter option
- Free text / discussion any time
- `next round` → advance only when you say so
```

### Every subsequent round (terse, status-bar)

```
`OK` lock all / free text always
```

## No-noise-summary rule

After user accepts a round:
- If user replied `OK` (all suggestions taken) on first try → **NO summary**. Move directly to next round or stage
- If user overrode any option, or round had back-and-forth → 1-line summary of what got locked, then move on

**Never re-recite locked decisions for ceremony.** Locked = locked, written to `session_state.md` (if revision-gated cadence triggers), no need to repeat.

## Themes-upfront rule

At start of any multi-round stage, announce planned themes before round 1:

```
Themes planned for <stage>:
1. <theme>
2. <theme>
...
```

User may add/drop/reorder themes before round 1 starts. After round 1, theme changes need brief justification.

## Anti-max-bias rule

Default LOW. Range "1–4 rounds × 4–6 Qs" is a *range*, **not a target**.

Sizing heuristic by task scope:
- Small/clear: 1 round, 3–4 Qs total
- Medium: 2 rounds × 4–5 Qs
- Large/complex: 3–4 rounds × 5–6 Qs (rare)

**Misc-bucket rule:** if 1–2 small Qs remain that don't fit any theme, group them into a "Misc" mini-round at the END of the stage — do NOT spawn 1-Q themes.

**Ranking gate** before each round ≥2:
1. Rank candidate Qs by impact (high / medium / low)
2. Drop low-impact Qs
3. If <3 medium-or-high Qs remain → end stage, do not run round
4. Declare ranking inline before posting the round (1 short paragraph)

## Auto-skip rule (obvious Qs)

Don't ask obvious questions. Two filters:

### Filter 1: don't generate trivial-tech-listing Qs in the first place

If you find yourself drafting a Q whose options are just a list of technology choices where one is clearly right given context (e.g. "storage: SQLite, files, Postgres?" for a small CLI tool), DON'T add it to the round. State the implied choice inline during drafting:

> "Drafting SRS assuming SQLite for persistence — flag if not."

Questions should be **high-level concerns** (does this scale to millions? does it need to survive crash? does it integrate with X?), not tech enumerations. Tech choices follow from concern answers; the agent can pick the tech.

### Filter 2: auto-declare obvious Qs that pass the ranking gate

For Qs that DO pass the impact ranking but happen to have a high-confidence obvious answer:

**If Suggestion confidence is ≥90% AND the non-suggested options are not destructive/irreversible if taken silently** → don't ask. Declare the choice inline and proceed:

> "Going with X (high confidence given context). Flag if not."

"Destructive/irreversible" guard means: never auto-declare on data deletion, schema-breaking change, security posture, settled-doc reopen, license/legal choices, anything user-facing brand or naming. Always ask on those, even at 99% confidence.

Apply this filter when drafting each Q. If filter triggers, the Q is replaced by a 1-line declaration in the round opener or stage rollup, not posted as a numbered Q.

## Memory cadence (revision-gated)

Tied to whether the round had revisions:

- **Round had revisions or pivots** → write to `session_state.md` under `## <stage>/round-<N>`:
  ```
  ## <stage>/round-<N>
  - 1: <locked option letter, or summary of free-text>
  - 2: ...
  ```
  Decisions only — not full Q+A transcript

- **Round was pure-OK first try** → skip the round entry entirely

- **Auto-declared choices (filter 2)** → write to `session_state.md` under `## <stage>/auto-declared`:
  ```
  ## <stage>/auto-declared
  - Going with X for <concern> (90%+ confidence, not contradicted by user)
  ```
  So a future review pass can find what was decided without asking.

- **At any stage boundary** → ALWAYS write a stage rollup:
  ```
  ## <stage> — rollup
  - <one-line per major decision>
  - <component/feature summaries if applicable>
  ```

Stage rollups exist regardless of how rounds went — they're the resumable state after a context compression.
