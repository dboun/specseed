<!--
SAD (System Architecture Document). ONE per project. The big-picture architecture as
built/intended: components, how they talk, data flow, and the canonical layout. Prose
sections get the humanizer pass; be concrete and real, not aspirational. `## Project
layout` is MANDATORY and authoritative: the single place the directory structure is
decided. Every impl agent reads it and matches it, so issues never each invent their own.
-->

# SAD — <Project name>

## Overview

<what the system is, its top-level shape, the main quality drivers>

## Components

<!-- One subsection per functional component: its responsibility and what it owns. -->

### <Component>

## Interfaces

<the contracts between components: APIs, events, shared schemas>

## Data flow

<how a request / data moves through the components>

## Data model

<persistent shapes at the architecture level: DBs, file formats, on-disk state>

## Project layout

<!--
MANDATORY + authoritative. State ONE concrete layout (not options), idiomatic for the
language/framework: package/module roots, where tests live, src- vs flat-layout, entry
points, config/manifest files.
-->

```
<repo>/
├── <src or package root>/
├── tests/
└── <config / manifest files>
```
