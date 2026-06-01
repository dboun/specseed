# Migrations

How a `.specseed/` tree built by an OLDER skill version is brought up to date when a NEWER skill runs against it. Consumed by `routes/migrate.md`; authored by the release procedure (see this repo's `CLAUDE.md` → "Versioning & releases").

## Version model (x.y.z)

The skill's version lives in `skills/specseed/version.txt` (one line, ships to `~/.claude/skills/specseed/version.txt`). A target repo records the version its tree was last migrated to in `<repo>/.specseed/version.txt`.

- **z (patch)** — every change. NON-breaking to an existing `.specseed/` tree (docs, internal refactors, new optional behavior). **No migration file.**
- **y (minor)** — breaking to the produced artifacts: spec/frontmatter format, script CLI or I/O contract, folder layout, JSON schema. "Your tree will misbehave with the new skill unless migrated." **Needs a migration file.**
- **x (major)** — user-decided major rethink (agent may suggest, never bumps x on its own). May need a migration file.
- Stays `0.y.z` until the skill is usable + verified.

Only y and x boundaries get a migration file. A pure-patch gap is migrated by re-stamping `version.txt` with no edits.

## File naming

One file per breaking boundary, named by the version it brings a tree **up to**:

```
migrations/
  0.2.0.md     # 0.1.x format → 0.2.0
  0.3.0.md     # 0.2.x format → 0.3.0
  1.0.0.md     # 0.x   format → 1.0.0
```

`migrate` collects every file whose `to:` is in `(tree_version, skill_version]`, sorts ascending, applies in order, then stamps `version.txt`.

## Single-hop, chained — NOT cumulative

Each migration file encodes ONE hop: the diff from the **previous** breaking release to its own version. It assumes its input tree is already in the previous version's format. It does NOT try to handle every older format back to the beginning.

That is what lets them chain. A tree on `0.3.0` upgrading to a skill on `0.5.2` runs, in the same session, in order:

```
0.4.0.md   (input: 0.3.x format  → 0.4.0)
0.5.0.md   (input: 0.4.x format  → 0.5.0)   # patches 0.4.1 / 0.5.1 / 0.5.2 have no files
```

By the time `0.5.0.md` runs, `0.4.0.md` has already brought the tree to `0.4.x` format, so `0.5.0.md` only needs the `0.4→0.5` diff. This is exactly why the release procedure (diff `prevtag..HEAD`) produces a correct file: each release's migration is just that release's changes.

Apply order is strict and not parallelizable. If a step's `## Verify` fails, stop there and leave `version.txt` at the last fully-applied version, so a re-run resumes mid-chain rather than restarting.

## File format

```markdown
---
from: 0.1.0                # the format this hop ASSUMES as input = the previous breaking release
to: 0.2.0                  # the format it produces
summary: <one line — what broke and why migration is needed>
---

# Migrate to 0.2.0

## What changed (and why this is breaking)
<short prose: the format/CLI/layout change>

## Steps
1. <imperative, mechanical instruction on the tree's files>
2. ...
   - Mark any step the author is UNSURE about with `# REVIEW:` so migrate surfaces it.

## Verify
- <command(s) to run after — assemble/validate/etc — and what "good" looks like>
```

Steps operate on the target repo's `.specseed/` (and the four main-repo entry files when relevant). They are imperative instructions an agent applies, not a script. Keep them mechanical and ordered; an agent reading only this file, with no other context, should be able to apply it.

## Authoring

Migration files are NOT hand-written ahead of time. They are produced at release-cut time by diffing the previous release tag against the release commit. See this repo's `CLAUDE.md` → "Versioning & releases".
