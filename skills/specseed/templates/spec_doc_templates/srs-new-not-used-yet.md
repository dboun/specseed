<!--
SRS TEMPLATE. Conforms to ISO/IEC/IEEE 29148 (SRS content + requirement quality) and ISO/IEC 25010 (quality model for non-functional reqs).

HOW TO USE:
- Fill every section. Keep section/subsection structure intact; do not renumber.
- Write content where each comment says "FILL:". Delete nothing structural.
- One requirement = one block. Every requirement gets unique stable ID, never reused even after deletion (see section 9 tombstone rule).
- Requirement text: use "shall" = binding, "should" = recommended (justify deviation), "may" = optional. Nothing else is normative.
- Each requirement must be: unambiguous, verifiable, complete, consistent, traceable, feasible, singular (one req per statement). If a req cannot be verified, rewrite it until it can.
- Verification method per req: one of Test / Analysis / Inspection / Demonstration (29148 default set). Test = run it. Analysis = math/model. Inspection = read code/doc. Demonstration = observe behavior, no instrumentation.
- Priority scheme (fixed for whole doc): P0 = must ship, P1 = important not blocking, P2 = nice to have. Use these only.
- Dates: ISO 8601, YYYY-MM-DD. Everywhere (revision history, last revision, gap targets where a date is used).
- Comments (like < ! - - ... - - > without spaces) are fill instructions. Never strip, harmless to leave.
- Examples appear only inside comments. Never put example text in live document.
- Keep prose plain and factual. No marketing, no filler. No em dashes or en dashes anywhere in live text; use colon, comma, or parentheses.
-->

# Software Requirements Specification

<!-- Owner/Approvers: this template leaves governance fields N/A. Fill them per project if a sign-off process applies; if so, also fill the approval block under the header table. Leave N/A if no formal sign-off is used. -->

| Field | Value |
|---|---|
| Document identifier | SRS |
| Version | 0.1 |
| Status | Template |
| Owner | N/A |
| Approvers | N/A |
| Last revision | initial draft |
| Standard reference | ISO/IEC/IEEE 29148; ISO/IEC 25010 |

<!-- Approval block: one row per approver who signs off the document. Fill only if a sign-off process applies; otherwise leave N/A. e.g. | Jane Roe | Engineering lead | 2026-01-15 | -->

| Approver | Role | Date |
|---|---|---|
| N/A | N/A | N/A |

---

## Table of contents

1. [Introduction](#1-introduction)
2. [Overall description](#2-overall-description)
3. [Specific requirements](#3-specific-requirements)
4. [Non-functional requirements](#4-non-functional-requirements)
5. [External interface requirements](#5-external-interface-requirements)
6. [Verification](#6-verification)
7. [Out-of-scope statements](#7-out-of-scope-statements)
8. [Known gaps and future revisions](#8-known-gaps-and-future-revisions)
9. [Traceability conventions](#9-traceability-conventions)
10. [References](#10-references)
11. [Glossary and acronyms](#11-glossary-and-acronyms)
12. [Revision history](#12-revision-history)

<!-- ToC: keep in sync with section headings + anchors. Anchor = heading lowercased, spaces to hyphens, punctuation dropped. Update when sections added/removed. -->

---

## 1. Introduction

<!-- 29148 intro. Sets purpose, boundary, vocabulary, who cares. Keep short. No requirements live here. -->

### 1.1 Purpose

<!-- FILL: what this doc defines + what downstream work consumes it (design, test, etc.). 2-4 sentences. e.g. "This SRS defines the requirements for [system]. It is the source for the design, test, and acceptance activities." -->

### 1.2 Scope

<!-- FILL: name the system. What it does, top-level. What it explicitly does NOT cover (point to other docs). Bound it so reader knows edges. -->

### 1.3 Conventions

<!-- Define modal verbs + notation. Keep block below; edit only if project uses different convention. Priority scheme + date format are fixed in the top HOW TO USE block; do not redefine per project. -->

The modal verb **shall** denotes a binding requirement. **Should** denotes a non-binding recommendation; any deviation must be justified. **May** denotes a permitted option. Statements without these modals are descriptive and non-normative.

Requirement priority uses P0 (must ship), P1 (important, not blocking), P2 (nice to have). Dates use ISO 8601 (YYYY-MM-DD).

### 1.4 Stakeholders

<!-- Table: who has a stake + what they care about. One row per role. Drives whose needs the reqs serve, and is the source list for the "Source" attribute on requirements (section 3). Replace the example row. e.g. | Operator | Runs the system day to day. | -->

| Role | Concern |
|---|---|
|  |  |

---

## 2. Overall description

<!-- 29148 "overall description". Context + big picture, NOT detailed reqs. Reader should grasp the system before hitting section 3. -->

### 2.1 Product perspective

<!--
FILL: where system sits. Standalone? Part of bigger system? Replaces what? Name external systems it talks to.
Include a context diagram: system as one box, arrows to every external entity (users, other systems, hardware), data flow labelled. Boundaries only, no internals. Use a mermaid block.

Filled example (DO NOT copy - RIGHTARROW should be '- - >' without spaces):
```mermaid
flowchart LR
    user([Operator]) RIGHTARROW|commands| sys[System]
    sys RIGHTARROW|results| user
    sys RIGHTARROW|reads/writes| db[(Datastore)]
    idp[Identity provider] RIGHTARROW|auth| sys
```
-->

### 2.2 Product functions

<!-- FILL: high-level summary of main capabilities. Bullet list fine. This is the 30-second overview; detail goes in section 3. -->

### 2.3 User characteristics

<!-- FILL: who uses it. Skill level, role, frequency, training assumed. Affects usability + interface reqs. -->

### 2.4 Operating environment

<!-- FILL: where it runs. Hardware, OS, network, runtime dependencies. Deployment topologies if more than one. -->

### 2.5 Constraints

<!-- FILL: hard limits not negotiable by design. Regulatory, standards, language, hardware, existing-system compatibility, org policy. -->

### 2.6 Assumptions and dependencies

<!-- FILL: things assumed true (if an assumption breaks, some reqs break) + dependencies on external parties/components/services. List each so impact traceable. -->

---

## 3. Specific requirements

<!--
Heart of doc. All functional reqs. Group by feature/subsystem/mode (29148 allows several schemes; pick one, stay consistent).
Each req = one block in the format below. Copy block per requirement. Rename/add 3.x group headings per your decomposition.

Block format:
  #### [ID]: [short title]
  - **Description.** One "shall" statement. Singular. Testable.
  - **Acceptance.** Bullet list. Concrete pass/fail conditions.
  - **Rationale.** Why this req exists. Short.
  - **Source.** Originating stakeholder or need (29148 first-class attribute). Pull the role from section 1.4. e.g. Operator; regulatory obligation; Need-3.
  - **Verification.** One of Test / Analysis / Inspection / Demonstration.
  - **Priority.** P0 / P1 / P2.
  - **Depends on.** Other req IDs this one cannot be met without. Blank if none.
  - **Refines.** Parent req ID this one decomposes (e.g. SRS-FN-1 if this is SRS-FN-1.1). Blank if top-level.
  - **Related.** Other req IDs worth cross-referencing, not a dependency. Blank if none.
  - **Traceability.** Test IDs that verify it. (Status/lifecycle tracked in the register, see section 9, NOT here.)

ID scheme: stable prefix + group + number, e.g. SRS-FN-1.1. Pick scheme, never reuse a retired ID (section 9 tombstone rule).

Filled example (DO NOT copy into live doc):
  #### SRS-FN-1.1: User login
  - **Description.** The system shall authenticate a user against the configured identity store before granting access.
  - **Acceptance.**
    - Valid credentials grant a session.
    - Invalid credentials are rejected and logged.
    - Account locks after N configurable failed attempts.
  - **Rationale.** Prevents unauthorized access.
  - **Source.** Information security officer.
  - **Verification.** Test.
  - **Priority.** P0.
  - **Depends on.** SRS-FN-1.0 (identity store configured).
  - **Refines.** SRS-FN-1 (authentication).
  - **Related.** SRS-NF-6.1 (credential hashing).
  - **Traceability.** TST-1, TST-2.

Example:
### 3.1 [group name]

#### SRS-FN-1.1:
- **Description.**
- **Acceptance.**
  -
- **Rationale.**
- **Source.**
- **Verification.**
- **Priority.**
- **Depends on.**
- **Refines.**
- **Related.**
- **Traceability.**
-->

---

## 4. Non-functional requirements

<!--
Quality requirements, organized under the ISO/IEC 25010 product quality model. 25010 = 9 characteristics, each with sub-characteristics.
Keep all 9 subsections. If a characteristic genuinely does not apply, keep the heading and write "No requirements." + one-line reason. Do not delete headings (keeps coverage auditable).
Each NFR uses the section 3 block format and MUST be measurable. Bad: "fast". Good: "95th-percentile response under 200 ms at 100 concurrent users". For an NFR the measurable threshold often IS the acceptance, so Description + Acceptance may collapse into one measurable statement; keep the other attributes (Source, Verification, Priority, dependencies, Traceability).
Tag each NFR with the 25010 sub-characteristic it addresses (in title or rationale). ID prefix e.g. SRS-NF-x.

Sub-characteristic reference (pick where a req goes):
- Functional suitability: completeness, correctness, appropriateness
- Performance efficiency: time behaviour, resource utilization, capacity
- Compatibility: co-existence, interoperability
- Interaction capability (usability): appropriateness recognizability, learnability, operability, user error protection, UI aesthetics, accessibility
- Reliability: faultlessness, availability, fault tolerance, recoverability
- Security: confidentiality, integrity, non-repudiation, accountability, authenticity
- Maintainability: modularity, reusability, analysability, modifiability, testability
- Flexibility: adaptability, scalability, installability, replaceability
- Safety: operational constraint, risk identification, fail-safe, hazard warning, safe integration

FILL each subsection with requirement blocks, or "No requirements." + reason.
-->

### 4.1 Functional suitability
<!-- Does it do the right things, correctly, completely? Reqs about coverage + correctness of function. -->

### 4.2 Performance efficiency
<!-- Time behaviour (latency, throughput), resource use (CPU/mem/disk), capacity (max load). Always put numbers. -->

### 4.3 Compatibility
<!-- Co-existence (shares environment without breaking others) + interoperability (exchanges data with named systems). -->

### 4.4 Interaction capability
<!-- Usability + accessibility. Learnability, operability, error protection, accessibility standards. -->

### 4.5 Reliability
<!-- Availability (e.g. uptime %), fault tolerance, recoverability (RTO/RPO). Measurable targets. -->

### 4.6 Security
<!-- Confidentiality, integrity, authn/authz, non-repudiation, audit/accountability. Name mechanisms + standards. -->

### 4.7 Maintainability
<!-- Modularity, testability (e.g. coverage targets), modifiability, analysability. -->

### 4.8 Flexibility
<!-- Adaptability across environments, scalability, installability, replaceability. -->

### 4.9 Safety
<!-- Fail-safe behaviour, hazard warnings, safe integration, operational constraints. Mark "No requirements." + reason if system has no safety dimension. -->

---

## 5. External interface requirements

<!--
29148 external interfaces. Every boundary where system meets something outside: other software (APIs), hardware, users (UI), comms protocols.
One entry per interface. State: what it connects to, direction, protocol/format, where full spec lives if external. ID prefix e.g. SRS-IF-x. Reference back to the functional reqs that use the interface.
Four 29148 interface kinds to consider: user, hardware, software, communications. Skip kinds that don't apply.

Entry format:
  ### SRS-IF-1: [interface name]
  [what it connects to, direction, protocol/format, link to detailed spec if any]

Filled example (DO NOT copy):
  ### SRS-IF-1: Identity provider
  Outbound to an OpenID Connect provider, authorization-code flow with PKCE. Full spec in [link].

Add entries below.
-->

---

## 6. Verification

<!--
29148 wants every requirement verifiable + a verification approach stated. Section 6 = the approach; per-req method = the "Verification" field in each block.
Keep the method definitions below. Then FILL: state where the verification matrix lives (here, or generated into the traceability register in section 9).
Optional: insert a matrix (req ID, method, test ID) directly here.
-->

Each requirement in this document carries a verification method, one of:

- **Test:** execute the system and compare observed results against expected.
- **Analysis:** establish conformance by calculation, modelling, or reasoning over the design.
- **Inspection:** confirm by examination of code, configuration, or documentation.
- **Demonstration:** confirm by observing system behaviour under defined conditions, without instrumentation.

<!-- FILL: verification approach + pointer to the verification matrix or traceability register. -->

---

## 7. Out-of-scope statements

<!--
Explicit "we are NOT building this in this release". Kills scope creep + assumption mismatches. Each entry = one excluded thing + one line why / where it lives instead. ID prefix e.g. OUT-x.
Not 29148-mandated but standard practice + cheap to keep.

Entry format:
  ### OUT-1: [excluded item]
  [what is excluded and, briefly, why or where it is handled instead]

Filled example (DO NOT copy):
  ### OUT-1: Native mobile app
  No native mobile client in this release. Web console only.

Add entries below.
-->

---

## 8. Known gaps and future revisions

<!--
Honest list of holes in THIS doc, deferred on purpose. Each: what's missing, who owns it, when it resolves (date YYYY-MM-DD or named milestone). Resolving an entry = edit the doc + bump revision history. Keeps reviewers from "discovering" gaps you already know about.
Replace the example row. e.g. | GAP-1 | Latency targets not yet measured on real hardware. | Eng | beta |
-->

| Identifier | Gap | Owner | Target |
|---|---|---|---|
|  |  |  |  |

---

## 9. Traceability conventions

<!--
How links work across docs. Status/lifecycle of reqs lives HERE / in the register, NOT in the requirement blocks (keeps blocks stable).
Edit the bracketed parts below to match your tooling, then remove brackets.
Lifecycle states: Draft, Approved, Deprecated, Removed. Deprecation rule: a deprecated or removed requirement KEEPS its ID forever as a tombstone (recorded in the register with state + reason + superseding ID if any). IDs are never reused. This is what makes "never reuse a retired ID" enforceable.
-->

Every requirement maintains links to: its originating need or higher-level requirement, the design element(s) that realise it, and the test specification(s) that verify it. Links are bidirectional. The canonical join, and the lifecycle state of each requirement (Draft, Approved, Deprecated, Removed), is maintained in [name the traceability register or tool].

A deprecated or removed requirement retains its identifier permanently as a tombstone, recorded with its state, the reason, and any superseding requirement identifier. Identifiers are never reused. [State whether dangling links or untested verified-requirements fail the build.]

---

## 10. References

<!-- Every external doc cited: standards, specs, prior art, internal docs. Stable form (title, version/year, identifier). Add project references below the two standards. -->

- ISO/IEC/IEEE 29148, Systems and software engineering, Life cycle processes, Requirements engineering.
- ISO/IEC 25010, Systems and software engineering, SQuaRE, Product quality model.

---

## 11. Glossary and acronyms

<!-- Two tables: terms (domain words + their meaning in THIS doc) and acronyms (expansion). Populate from the requirement text: any domain term or acronym used in a requirement must be defined here. Keeps reqs unambiguous. Replace the example rows. -->

### 11.1 Terms

| Term | Definition |
|---|---|
|  |  |

### 11.2 Acronyms

| Acronym | Expansion |
|---|---|
|  |  |

---

## 12. Revision history

<!-- One row per version. Date YYYY-MM-DD. Summarize what changed + who. Bump version when reqs change. Match the Version field in the header table to the latest row. -->

| Version | Date | Author | Change summary |
|---|---|---|---|
| 0.1 | initial | N/A | Initial template. |