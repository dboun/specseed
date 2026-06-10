<!--
SDD (Software Design Document). The HOW. DEFAULT to a single `sdd.md` titled by the
PROJECT. Split into per-component `<comp>-sdd.md` (each titled by its component) ONLY for
a large project with multiple real components (per `references/work-breakdown.md`); do not
force a single small project into a "component" shorthand. Design-level detail an impl
agent turns into code. Prose humanized, concrete. Do not restate requirements (SRS) or the
overall architecture (SAD): link them.
-->

# SDD — <project name (single sdd.md) | component name (split <comp>-sdd.md)>

Satisfies: SRS-<COMP>-NNN, ...
<!-- When split per component, also add: `Component: <the SAD component this details>` -->


## Responsibilities

<what this component does, in design terms>

## Internal structure

<modules / classes / functions and how they fit; key types and data structures>

## Key flows / algorithms

<the non-obvious logic: sequencing, state, algorithms, edge cases>

## Error handling

<failure modes and how they are handled>

## Dependencies

<external libs / services this component relies on, with versions where they matter>
