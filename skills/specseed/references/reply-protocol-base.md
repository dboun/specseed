# Reply protocol (shared base)

The shared base for EVERY user-facing reply the skill emits: the FORM it renders in (per
mode) and the QUESTION DISCIPLINE behind it. **Outbound only.** What the human sends BACK
is just a comment the runtime parses elsewhere; not this doc's concern.

**Route-specific reply rules — WHEN to reply, whether the route makes approval asks —
live in `reply-protocol-<route>.md`, not here.** This base covers only `main_body` and
`questions`; approvals are a route-specific concept (only some routes have them).

Caveman + humanizer applies to all prose here (esp. `main_body`).

## Mandatory skill reads

| Read | Why |
|------|-----|

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## Step 0 — pick the render mode. STRICT. Do this BEFORE composing.

Three modes, two forms. Choose by the tracker provider the runtime handed you:

| Condition | Mode | Form |
|---|---|---|
| Provider is **local** (specseed's own tracker; the specseed web UI is the only renderer) | **specseed-UI** | **STRUCTURED** JSON envelope |
| Provider is **github** / **gitlab** (read natively on that platform) | **external** | **NATURAL** prose |
| **No runtime** (plain Claude chat — no scheduler, no `<specseed_dir>/storage/`) | **chat** | **NATURAL** prose |
| **Cannot tell** which | — | **Assume chat** |

Rules:
- **The runtime states your mode outright** (a `Reply render mode:` line in the execution
  facts, plus the `Mode:` bundle header). TRUST that line above everything else — it is
  the config-resolved truth. specseed-UI ⇒ structured envelope; external ⇒ natural prose.
  Only fall back to the provider/no-runtime inference below when NO mode line is given.
- Provider is FINAL per repo; the runtime context tells you which. Local ⇒ structured.
  github/gitlab or no runtime ⇒ natural. Any doubt ⇒ chat. (A local repo has no provider
  in `remote.json` — do NOT read that as "cannot tell" and drop to chat; the stated mode
  wins.)
- NEVER emit the JSON envelope outside specseed-UI. NEVER emit bare prose in specseed-UI.
  The CONTENT (body, questions, themes, confidences, suggestions) is the same across all
  three; only the form differs.
- Reaction prompts (`React 👍 …`) belong ONLY to approval asks, which are a route-specific
  concept (see the route's reply protocol). The `main_body` / `questions` sections here
  never carry reactions, so this base emits none.

## Delivery — async park vs live

- **Runner (async; local/github/gitlab).** You cannot interview a human live. Post a
  round as a comment (or comments) on the relevant post and **park** (the route's reply
  protocol says how it parks and what status it sets), then STOP. The next invocation
  re-triggers with the human's reply in the comments; read it and continue. A round may
  carry SEVERAL questions — the headless constraint is "one round, then park," NOT "one
  question." Never loop live.
- **Chat (live).** Ask the round, wait for the reply in the conversation, continue. No
  park, no comment.

## Sections

A reply is a list of sections. The base defines two:

- `main_body` — the prose answer. Always FIRST when present.
- `questions` — one round of questions (carry no reaction).

(Some routes add an `approval` section — defined in that route's reply protocol, not
here.)

## Structured form (specseed-UI)

One reply = one `user_facing_thread_entry`. `schema_version` lets the contract evolve
(bump engine `Y` when it changes).

```json
{
  "schema_version": 1,
  "user_facing_thread_entry": {
    "allow_regular_user_reply": true,
    "sections": [
      {
        "section_type": "main_body",
        "content": {"markdown": "The prose answer. Always on top. Caveman + humanizer for legibility."}
      },
      {
        "section_type": "questions",
        "content": {
          "markdown": "Round header / theme. Short. E.g. 'Understanding the idea.'",
          "questions": [
            {
              "question_type": "multiple_choice",
              "question_theme": "1-5 words, e.g. 'Deployment pipeline'",
              "question_context_markdown": "Context for this question. 0-3 sentences.",
              "question": "Short 1-sentence question ending in a question mark?",
              "options": {
                "A": {"option": "Short answer, ideally 1 word or short phrase.", "option_markdown": "More detail. 0-2 sentences. Inline markdown only (italics/code), no sub-sections."},
                "B": {"option": "...", "option_markdown": "..."},
                "C": {"option": "...", "option_markdown": "..."}
              },
              "confidence": {"A": 0.8, "B": 0.15, "C": 0.05},
              "suggested_answer": "A",
              "suggested_answer_justification": "1-2 sentences justifying the default.",
              "allow_other_option": true,
              "allow_user_selection_comment": true
            },
            {
              "question_type": "open_ended",
              "question_theme": "1-5 words",
              "question_context_markdown": "Context. 0-3 sentences.",
              "question": "Short 1-sentence question ending in a question mark?",
              "allow_skip": false
            },
            {
              "question_type": "assumptions_check",
              "question_theme": "1-5 words",
              "question_context_markdown": "Context. 0-3 sentences.",
              "assumptions": [
                {"assumption": "The user has read the entire document.", "confidence": 0.95},
                {"assumption": "The user understands the content.", "confidence": 0.9}
              ],
              "allow_user_deselection_comment": true
            }
          ],
          "post_questions_message": {"markdown": "E.g. Feedback received. If no major open points, we will ..."}
        }
      }
    ]
  }
}
```

Field rules:
- `multiple_choice`: `confidence` keys MUST match the `options` keys (0-1 floats). The
  confidences cover the LISTED options; with `allow_other_option: true` an "Other" reply
  takes the remaining mass — don't reserve a phantom option for it.
- `assumptions_check`: `assumptions` is a list of `{assumption, confidence}` objects
  (each carries its OWN confidence — never two parallel positional arrays).

## Natural form (external + chat)

Same content, no JSON — plain markdown. Same theme + confidence/suggestion discipline as
structured. One example per section type.

**`main_body`**
```
Got it — folding the change into the worker. Main body stays prose; the questions get
their own structure so the UI can render them later.
```

**`questions`** — round opener (1–3 lines), then per-question. Numbering is within-round
(1., 2., …); the round number is in the opener. Context goes INSIDE each question, not in
a big top block.

```
**Round 1 — Understanding the idea**

**1. Deployment pipeline**
How should a finished issue reach primary?
- **A)** Auto-merge on green review
- **B)** Always gate behind a human 👍
- **C)** Gate only `hard` issues
Confidence: A 80% / B 15% / C 5%
Suggestion: **A**. Matches the existing merge-gate default; humans flip it per-repo.
```
Per-question rules: always the full words `Confidence:` and `Suggestion:` (never
`Conf:`/`Sug:`); confidences sum to 100; exactly one suggested option with a one-line
rationale; options mutually exclusive and short; a one-line counter for a non-suggested
option is allowed if non-trivial. Phrase at a level a generic technical reader grasps
(technologies in parentheses if needed) — no word salad.

Round CLOSER. First round (teach-once, verbose):
```
**How to respond:**
- `OK` → take all suggestions as locked
- `1. OK, 2. ...` → accept the suggestion for question 1
- `1. B, 2. ...` → override question 1 with that option
- Free text / discussion any time
- (external) reply on this post; the worker resumes on the next poll
```
Every subsequent round (terse): `` `OK` lock all / free text always ``. In **chat**, drop
the "reply on this post …" line (no poll). A well-suggested round often costs the human
one word (`OK`).

## Question discipline (mode-independent)

These govern question GENERATION regardless of form. The confidence/suggestion shape is
the point: it forces a defensible default and exposes uncertainty instead of asking
open-ended.

**Anti-max-bias.** Default LOW. The ranges below are ranges, NOT targets. **Hard cap: 3
rounds per stage.** Human interaction is the real cost, not artifacts.
- Small/clear: 1 round, 3–4 Qs total
- Medium: 2 rounds × 4–5 Qs
- Large/complex: 3 rounds × 5–6 Qs (rare; the ceiling)

Explicit override: a request naming a round count ("2 rounds of questions") wins over the
heuristic and the ranking gate (cap 3 still holds) — run exactly that many, filling late
rounds with the best remaining medium-impact Qs. The **depth tier** (adapt cold-start,
`spec_subroutes/adapt.md` "Depth dial") tightens further: `lite` caps stages at 1 round;
`incremental` only deep-questions the first increment's scope.

**Themes-upfront.** At the start of a multi-round stage, announce the planned themes
before round 1; the human may add/drop/reorder before round 1. After round 1, theme
changes need brief justification. Misc-bucket: 1–2 leftover small Qs that fit no theme
group into a "Misc" mini-round at the END — never spawn 1-Q themes.

**Ranking gate** before each round ≥2: rank candidate Qs by impact, drop low-impact; if
<3 medium-or-high remain → END the stage, don't run the round; declare the ranking inline
(one short paragraph) before posting.

**Auto-skip (don't ask obvious Qs).** Two filters.
1. Don't generate trivial tech-listing Qs. If a Q's options are just tech choices with one
   clearly right (e.g. "storage: SQLite/files/Postgres?" for a small CLI), state the
   implied choice inline instead ("Drafting SRS assuming SQLite — flag if not").
   Questions are high-level concerns (scale? crash-survival? integrates with X?), not tech
   enumerations — the implementing agent picks tech from the concern answers.
2. For a Q that passes ranking but has a high-confidence obvious answer: if the suggestion
   is ≥90% AND the non-suggested options aren't destructive/irreversible if taken silently
   → don't ask; declare inline ("Going with X (high confidence). Flag if not."). NEVER
   auto-declare on data deletion, schema-breaking change, security posture, settled-doc
   reopen, license/legal, or naming/brand — ALWAYS ask those, even at 99%.

**No-noise-summary.** After the human accepts a round: pure `OK` on first try → NO
summary, move on. Any override or back-and-forth → one-line summary of what locked, then
move on. Never re-recite locked decisions for ceremony.

## Ownership — what is and isn't a skill concern

Nothing magic sits under a reply. Whatever the human responds becomes a plain comment (the
specseed UI may capture it structured — not a skill concern). The skill emits CONTENT; it
does not own resolution. **Questions** never carry reactions: they are answered in prose,
parsed by the runtime; the skill just asks. Approval resolution (where a route has it) is
runtime/code-owned — see that route's reply protocol.
