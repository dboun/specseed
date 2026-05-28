# specseed

A skill for producing and maintaining software specification artifacts — vision, SRS, SAD, ADRs, SDD, requirements JSON, tickets JSON — for greenfield projects or adapting existing ones. Agents (or humans) can pick these tickets and implement. Adaptations iteratively can happen.

Designed for agent harnesses that can read files (Claude Code, Codex, similar). Works in chat too with progressive artifact delivery.


## What it does

Runs in one of three modes (no need to explicitly specify, could also be inferred):

- **bootstrap** — greenfield: produces the full `spec/` tree through a structured questioning flow
- **adapt** — existing spec, non-trivial changes: localizes impact, patches in place, re-runs analyzers
- **tweak** — single doc edits (add a req, flip a status, fix a typo)

Trigger via `/specseed <mode>` or natural-language asks like "spec out this project" / "draft requirements" / "add a req".

The output is files in `spec/`, `docs/`, plus a root `CLAUDE.md` that tells implementation agents how to pick up tickets and execute them. The skill enforces a settled-doc contract: once approved, spec docs aren't edited mid-implementation — agents that find issues file `spec_concern.md` for the next adapt session.


## Install

Just run `bash install.sh` or `chmod +x install.sh; ./install.sh`.


## License / contact

See [LICENSE](./LICENSE) file.
