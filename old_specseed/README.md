# specseed

A skill for producing and maintaining software specification artifacts — vision, SRS, SAD, ADRs, SDD, requirements JSON — plus a three-tier project-management breakdown (epics → tickets → issues) for greenfield projects or adapting existing ones. Agents (or humans) pick up the technical *issues* and implement. Adaptations iteratively can happen.

Designed for agent harnesses that can read files (Claude Code, Codex, similar). Works in chat too with progressive artifact delivery and zipped handoffs when the host supports file artifacts.


## What it does

Runs in one of five modes (no need to explicitly specify — chosen from disk evidence at session start, then inferred from the ask):

- **bootstrap** — greenfield: produces the full `.specseed/` tree through a structured questioning flow
- **adopt** — existing code, no spec: reverse-bootstraps the spec FROM the codebase (read-only on your source), imports any docs you already have, maps built work in the roadmap, breaks down remaining gaps
- **plan-next** — extends an incremental bootstrap forward: specs + breaks down the next roadmap slice (append-only)
- **adapt** — existing spec, non-trivial changes: localizes impact, patches in place, re-runs analyzers
- **tweak** — single doc edits (add a req, flip a status, fix a typo)

Trigger via `/specseed <mode>` or natural-language asks like "spec out this project" / "draft requirements" / "add a req".

The output lives under `.specseed/` — `spec/` (vision, SRS, SAD, SDD, ADRs, reqs) and `project_management/` (ROADMAP + epics → tickets → issues, plus optional **sprints** that batch tickets into ~weekly time-boxes and a generated `TIMELINE.md`) — plus a root `CLAUDE.md` that tells implementation agents how to pick up *issues* and execute them (sprint-scoped: the active sprint's work first). The skill enforces a settled-doc contract: once approved, spec docs aren't edited mid-implementation — agents that hit a problem file a `spec_concern.md` for the next adapt session.


## Install

Just run `bash install.sh` or `chmod +x install.sh; ./install.sh`.


## Versioning & releases

The skill carries a version in `skills/specseed/version.txt`, as `x.y.z`:

- **z (patch)** bumps on essentially every change. Non-breaking: an existing `.specseed/` tree keeps working as is.
- **y (minor)** bumps when the produced artifacts change in a breaking way (spec or frontmatter format, a script's CLI or output, the folder layout, a JSON schema). An older tree needs migrating before the new skill drives it cleanly.
- **x (major)** bumps only when you decide the skill has been substantially rethought. The agent can suggest it but never sets it on its own.

It stays on `0.y.z` until the skill is genuinely usable and verified.

When the skill runs against a repo whose `.specseed/` tree was built by an older version, it notices the gap at session start and offers to **migrate** the tree to the current format first (also `/specseed migrate` on demand). Each breaking release ships a migration file under `skills/specseed/migrations/`; migrate applies the ones between the tree's version and the skill's, in order, then re-stamps `.specseed/version.txt`.

### Cutting a release

Tell the agent "let's cut a release". It will: find the previous release tag, compare it against the current commit, decide the bump with you, write a migration file if the change is breaking, bump `version.txt`, and hand you the exact `git tag` / `git push` commands for the release commit. Nothing is tagged or pushed without you. The full procedure lives in `CLAUDE.md` ("Versioning & releases").

The first release, `0.1.0`, is the state on `main` and is not yet tagged. To tag and push it:

```bash
git tag -a v0.1.0 bcd2234 -m "specseed 0.1.0"
git push origin v0.1.0
```

## License / contact

See [LICENSE](./LICENSE) file.
