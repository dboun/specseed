<!--
SRS (Software Requirements Specification). DEFAULT to a single `srs.md` titled by the
PROJECT. Split into per-component `<comp>-srs.md` (each titled by its component) ONLY for
a large project with multiple real components (per `references/work-breakdown.md`); do not
force a single small project into a "component" shorthand. The requirements are a
MACHINE-PARSED markdown table:
`scripts/requirements_generate_json.py` reads the header columns and enums below VERBATIM,
so keep them exact. Requirement prose gets the humanizer pass; table cells stay terse,
machine text. Deprecate, never delete: append `[DEPRECATED <ISO date>: reason]` to a
Requirement cell (the id is preserved for traceability).
-->

# SRS — <project name (single srs.md) | component name (split <comp>-srs.md)>

<one-paragraph scope: what the project / this component is responsible for>

## Requirements

<!--
ID         SRS-<COMP>-NNN   (COMP = uppercase component tag; NNN zero-padded, per-component
                             counter. Cross-cutting reqs use COMP = CC.)
Type       functional | non_functional | constraint
Priority   must | should | could | wont
Depends on comma-separated requirement IDs, or `-` for none
-->

| ID | Requirement | Type | Priority | Depends on |
|----|-------------|------|----------|------------|
| SRS-<COMP>-001 | <one testable requirement, one sentence> | functional | must | - |
| SRS-<COMP>-002 | <...> | non_functional | should | SRS-<COMP>-001 |
