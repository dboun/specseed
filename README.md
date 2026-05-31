# specseed

A skill for producing and maintaining software specification artifacts — vision, SRS, SAD, ADRs, SDD, requirements JSON — plus a three-tier project-management breakdown (epics → tickets → issues) for greenfield projects or adapting existing ones. Agents (or humans) pick up the technical *issues* and implement. Adaptations iteratively can happen.

Designed for agent harnesses that can read files (Claude Code, Codex, similar). Works in chat too with progressive artifact delivery.


## What it does

Runs in one of three modes (no need to explicitly specify, could also be inferred):

- **bootstrap** — greenfield: produces the full `.specseed/` tree through a structured questioning flow
- **adapt** — existing spec, non-trivial changes: localizes impact, patches in place, re-runs analyzers
- **tweak** — single doc edits (add a req, flip a status, fix a typo)

Trigger via `/specseed <mode>` or natural-language asks like "spec out this project" / "draft requirements" / "add a req".

The output lives under `.specseed/` — `spec/` (vision, SRS, SAD, SDD, ADRs, reqs) and `project_management/` (ROADMAP + epics → tickets → issues, plus optional **sprints** that batch tickets into ~weekly time-boxes and a generated `TIMELINE.md`) — plus a root `CLAUDE.md` that tells implementation agents how to pick up *issues* and execute them (sprint-scoped: the active sprint's work first). The skill enforces a settled-doc contract: once approved, spec docs aren't edited mid-implementation — agents that hit a problem file a `spec_concern.md` for the next adapt session.


## Install

Just run `bash install.sh` or `chmod +x install.sh; ./install.sh`.


## License / contact

See [LICENSE](./LICENSE) file.
