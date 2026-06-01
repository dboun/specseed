# Migrate mode

Bring a `.specseed/` tree built by an OLDER skill version up to the version of the skill now running. Mechanical, ordered, reviewed-once. Read `migrations/README.md` for the version model + file format before running.

Fires:
- **Auto** — session-start reconnaissance detects the tree's minor version is behind the skill's (see `SKILL.md`). Offered before any other route.
- **Explicit** — `/specseed migrate`.

This route only edits the `.specseed/` tree (and the four main-repo entry files where a migration step says so). It never touches the rest of the repo's source.

## 1. Read both versions

- **Skill version** = `version.txt` at the skill root (sibling of `SKILL.md`, i.e. the file you'd find next to the `SKILL.md` you loaded). Call it `TARGET`.
- **Tree version** = `<repo>/.specseed/version.txt`. Call it `CURRENT`.
  - **Missing?** The tree predates version stamping → treat as `0.1.0`, and **confirm with the user**: "No `.specseed/version.txt` found; assuming this tree was built by 0.1.0. Correct? (give the version if not)". Use their answer as `CURRENT`.

Compare `CURRENT` vs `TARGET` (numeric x, then y, then z):
- **`CURRENT == TARGET`** → up to date. Say so, exit. (Nothing to do.)
- **`CURRENT > TARGET`** → the tree is NEWER than the skill (skill is stale on this machine). **Do NOT downgrade.** Warn the user to update their skill install (`bash install.sh` from the skill repo), exit.
- **`CURRENT < TARGET`** → migrate forward (continue).

## 2. Collect the migration steps

From `migrations/`, take every file whose `to:` version is in the half-open range `(CURRENT, TARGET]`. Sort ascending by `to:`. This is the migration plan — applied **in order, in this one session**.

Each file is a **single hop** (`from:` → `to:`); they chain. The first file's `from:` should be ≤ `CURRENT`, each subsequent file's `from:` should equal the previous file's `to:`, and the last file's `to:` should be `TARGET`. So a tree on 0.3.0 going to a skill on 0.5.2 runs `0.4.0.md` then `0.5.0.md` (input to the second is the output of the first); patch-only versions in between have no files. Each step's input is the tree as the previous step left it, NOT the original tree.

- **No files in range** (the gap is patch-only, e.g. 0.2.1 → 0.2.4) → nothing structural to do. Skip to step 5 (re-stamp `version.txt` to `TARGET`), tell the user it was a patch-only bump.
- **A minor/major gap exists but a file is missing** — either the chain has a hole (a file's `from:` doesn't match the previous `to:`) or an expected boundary file is absent (e.g. tree 0.1.0, skill 0.3.0, but `migrations/0.2.0.md` is missing) → STOP. This is a release-discipline bug, not something to guess through. Tell the user which hop is missing and that the skill install may be incomplete (re-run `install.sh`). Do not invent the missing hop.

## 3. Show the plan, confirm once

Present, before touching anything:

```
Migrate: <CURRENT> → <TARGET>
Steps (applied in order):
  • 0.2.0 — <summary from frontmatter>
  • 0.3.0 — <summary>
Files affected: .specseed/spec/*, .specseed/project_management/..., CLAUDE.md (per step)
```

If any migration file has `# REVIEW:` markers, list them now so the user knows where judgment is needed. Then ask for a single **OK** (or let the user veto/defer). Do not apply per-step confirmations — one gate, then run.

## 4. Apply sequentially

For each migration file in order:
1. Read the file's `## Steps`.
2. Apply each step to the tree. For `# REVIEW:` steps, do the best-effort change AND flag it in the run report for the user to check.
3. Run the file's `## Verify` commands. If a verify fails, STOP at that version — report what failed, leave `version.txt` at the last fully-applied version (so a re-run resumes from the failure, not the start). Do not push past a failed verify.

Report per applied version: what changed, files touched, any review flags, verify result.

## 5. Stamp the version

After all in-range files apply cleanly (or on a patch-only gap), write `TARGET` into `<repo>/.specseed/version.txt` (single line). This is what makes the migration idempotent — a re-run sees `CURRENT == TARGET` and no-ops.

## 6. Hand off

One-line summary: `<CURRENT> → <TARGET>, N migration step(s) applied, M flagged for review`. If this ran as the auto-preamble before another route, continue into that route now (the tree is current). If review flags were raised, point the user at them before proceeding.

## Notes

- Migrate does NOT add features, re-question, or re-spec. It only transforms an existing tree to the new format. Spec changes are adapt/tweak; new slices are plan-next.
- Fresh trees are stamped with the skill version at creation (configure mode's Persist step), so a just-built tree is already current and never triggers this route.
