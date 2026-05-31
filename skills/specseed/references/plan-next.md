# Plan-next mode

Extend an `incremental`-bootstrapped spec **forward** into the next increment. Deep-specs + breaks down the next slice of the roadmap (new sprint), without reopening anything settled.

**Not adapt.** Adapt *changes* settled docs (reopens, logs ADRs, re-cascades). Plan-next only **appends**: new SRS reqs, new SDD, deepened SAD skeleton, new tickets/issues/sprint for un-specced roadmap titles. If the slice forces a change to something already settled → that's adapt's job; plan-next stops and routes the user there (see "Boundary").

Load `references/question-protocol.md`, `references/component-questions.md`, `references/work-breakdown.md` as the stages below call them.

## When fires

- User invokes `/specseed plan-next` (or "plan the next sprint/phase", "break down the next slice").
- Or skill detects: `.specseed/spec/` exists with settled vision/SAD/roadmap AND `ROADMAP.md` has ticket titles with NO ticket folder yet (an un-detailed tail) → *offer* plan-next.

Precondition: an `incremental` bootstrap (or a prior plan-next) already ran. If there's no roadmap / nothing settled, this is bootstrap, not plan-next. If the spec is complete (every roadmap title has a ticket folder), there's nothing to plan-next — tell the user.

## State detection (no session_state — read disk)

Fresh session; reconstruct from artifacts:
1. Read `.specseed/spec/vision.md`, `sad.md`, all `*-srs.md` (note `settled`/`settled_scope` frontmatter), `ROADMAP.md`.
2. Read `.specseed/project_management/`: which roadmap ticket TITLES already have `tickets/<id>/` folders (= done/in-flight increments) vs which don't (= the pending tail). Read `sprints.json` for the highest sprint number.
3. Read `.specseed/memory/sprint_planning.md` for durable prefs.
4. Identify the **next slice**: the next coherent group of un-foldered roadmap titles (usually the next phase/area, or the next sprint's worth). Present it; let user confirm or re-scope ("do the API before the worker").

First-message template (per `SKILL.md`): `specseed on. Mode: plan-next.` + what's already specced + what this slice will add + that nothing settled gets reopened.

## Stages (the per-increment deep loop)

Mirror bootstrap's deep stages, scoped to the slice. Reuse the same subroutines so behavior matches.

### 1. Questioning (slice scope)
For components newly entering scope (or behavior of existing components not yet specced): run `references/component-questions.md`. Same pacing caps. Skip components already fully specced.

### 2. Extend SRS
Append new req rows to the relevant `*-srs.md` (new component → new file; if it was a deferred placeholder from bootstrap, replace the placeholder with real reqs). New IDs continue the per-component numbering. Do NOT edit existing settled rows. Then settle the new reqs (stage-7-style): set/extend `settled_scope`, keep prior settled rows untouched.

### 3. Extend SDD + deepen SAD
- SDD: write/extend `sdd.md` (or `<component>-sdd.md`) for the slice's scope only.
- SAD: deepen the **skeleton** blocks this slice touches (data flow, contracts, deployment topology) — replace their `> Skeleton — deepened via plan-next` markers with real content. Don't rewrite blocks that are already deep/settled.

### 4. reqs.json
Run `requirements_generate_json.py` then `requirements_analyze.py` over the now-larger SRS set. Resolve any new cycles (stage-9 moves). The regenerated `reqs.json` includes prior + new reqs.

### 5. ADRs
Append rows to `adr.csv` for new decisions made this slice. Append-only — never rewrite prior rows.

### 6. Work breakdown (the new sprint)
Use `references/work-breakdown.md`:
- Create `tickets/` + `issues/` folders for the slice's roadmap titles. Back-link. `satisfies_reqs` → the reqs just settled.
- Assemble bottom-up + validate:
  ```bash
  python .specseed/scripts/issues_assemble.py
  python .specseed/scripts/tickets_assemble.py
  python .specseed/scripts/issues_validate.py
  python .specseed/scripts/tickets_validate.py
  ```
- **Critical path — now recomputed across ALL tickets** (prior increments + this slice):
  ```bash
  python .specseed/scripts/tickets_analyze.py .specseed/project_management/tickets.json
  ```
  Show the user the updated project-level CP. This is the payoff of keeping CP at the ticket tier — each plan-next sharpens it as more of the roadmap materializes.
- Sprint: pack the new tickets into the next sprint (`SPRINT_…_<N+1>`). Read `sprint_planning.md`, run `sprint_plan.py`, one refinement pass, write the sprint folder + `sprint:` into each new ticket. Mark it `in_progress` only if the prior sprint is done; else leave `planned` (no two `in_progress` sprints — `sprints_validate.py` + claim ordering assume one target). Capture durable prefs → `sprint_planning.md`.
  ```bash
  python .specseed/scripts/sprints_assemble.py
  python .specseed/scripts/sprints_validate.py    # also checks NO backward sprint deps
  python .specseed/scripts/timeline_render.py
  python .specseed/scripts/roadmap_render.py
  ```
  `sprints_validate.py` enforces no backward dependency — the new sprint may depend on earlier ones, never the reverse. If it flags one, a slice was mis-ordered; re-scope.

### 7. Entry files (only if conventions changed)
`CLAUDE.md` was written at bootstrap. Touch it only if this slice introduces a new component dir (propose a per-component `CLAUDE.md`) or changes project conventions. Merge protocol applies. Usually nothing here.

### 8. Session end
- Tell user: slice `<scope>` specced + broken down as sprint `<N>`; issues claimable; updated CP.
- Remaining roadmap tail (if any): `/specseed plan-next` again when ready. If the roadmap is now fully detailed, say so.
- Delete `session_state.md`; keep `sprint_planning.md`.

## Boundary: plan-next vs adapt

| Situation | Mode |
|-----------|------|
| Spec the next un-detailed roadmap slice | **plan-next** |
| A settled req/design is wrong and must change | **adapt** |
| Slice reveals a settled decision was wrong | plan-next **stops**, routes to adapt for that change, then resumes |
| Roadmap itself needs restructure (new phases, dropped epics) | **adapt** (it owns roadmap revisions), then plan-next the new tail |
| Tiny single edit (one req, one priority) | **tweak** |

Plan-next's contract: **append-only forward**. The moment it would reopen something `settled`, it's out of scope — hand to adapt. This is exactly what keeps it distinct and safe.
