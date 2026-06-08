# Playground Dependency + Gitignore Report

Target inspected:
`/Users/dimitriosbounias/Work/Scrapbook/specseed/.playground/local_greenfield-2026_06_08-15_57_42/repo`

No target playground files were changed.

## Short Answer

Two things went wrong.

1. The spec-change agent planned the dependency chain, but generated body links without `#`.
2. Setup wrote `.specseed/` into `.gitignore`, but did not commit that `.gitignore` onto `main` before issue branches were cut.

So FEAT-0002/FEAT-0003/QA ran early, and the runtime state under `.specseed/storage/` then got committed into a work branch. That made later checkout/merge prep fail with dirty tracked storage files.

## Dependency Failure

The generated plan did include dependencies:

- FEAT-0002: `Depends on: {id:FEAT-0001 Scaffold the project layout}`
- FEAT-0003: `Depends on: {id:FEAT-0002 Build task storage}`
- QA-0001: `Depends on: {id:FEAT-0003 Implement command handlers}`

Source:
`.playground/local_greenfield-2026_06_08-15_57_42/repo/.specseed/storage/spec-change/5/plan.json`

But generated `apply.py` substituted `{id:title}` with `str(parent_id)`, producing:

- `Depends on: 9`
- `Depends on: 10`
- `Depends on: 11`

The runtime dependency parser only recognizes ids with a leading `#`.

Relevant code:

- `src/specseed_runtime/entities/entity_base.py`: `_ID_TOKEN_RE = re.compile(r"#\s*([0-9]+|[A-Za-z]+-[0-9]+)")`
- `skills/specseed/references/spec-change-protocol.md`: sample shows `#{id:...}`, but prose says `{id:...}`
- `skills/specseed/references/spec-change-protocol.md`: apply template replaces `{id:...}` with `str(parent_id)`

Net: the dependency gate saw no deps, so every issue was claimable immediately.

## Gitignore / Storage Failure

The target had `.specseed/storage/*` tracked on the active branch. That directly explains:

```text
Your local changes to the following files would be overwritten by checkout:
.specseed/storage/platform.log
.specseed/storage/specseed.db-wal
```

In the target:

- `main` has no files.
- FEAT-0001 branch has `.gitignore` with `.specseed/`.
- FEAT-0002 branch has `.specseed/storage/*` tracked.

The intended setup path exists:

- `src/specseed_runtime/configuring/scaffold.py`
- `ensure_repo_gitignored()` appends `<specseed_dir>/` to repo `.gitignore`.
- `scaffold_target()` always calls it.
- `configure.py` calls `scaffold_target()` during defaults/configure.
- `executing/run.py` also calls `scaffold_target()` as startup repair.

But `scaffold_target()` does this:

1. Write/update `.gitignore`.
2. Run `ensure_git_repo()`.
3. `ensure_git_repo()` creates an empty root commit with `--allow-empty`.

So on a fresh repo, `.gitignore` is present in the working tree, but the root commit on `main` is empty. Issue branches cut from `main` do not inherit `.gitignore`.

FEAT-0001 happened to add `.gitignore`, but because FEAT-0002 did not wait for FEAT-0001 to merge, FEAT-0002 ran from empty `main` without the ignore rule. Then `git add -A` in the runtime commit path swept in `.specseed/storage/*`.

## Low-Capability Agent Factor

Yes, low effort `gpt-5.4-mini` likely contributed to the dependency placeholder mistake. The instructions are ambiguous enough that a weaker agent can follow the wrong literal form:

- Work-breakdown says dependency links should be `#<scaffold>`.
- Protocol prose says child bodies reference parents as `{id:<parent title>}`.
- Protocol sample shows `#{id:...}`.
- Apply template works only if the body had `#{id:...}`.

That should not be left to agent judgment.

## Platform Responsibility

This is not only an agent issue.

Platform/code should harden at least these contracts:

- Generated dependency links must be parseable before approval or apply.
- The generic `apply.py` template should preserve/create `#` links for body references.
- Setup should put the mandatory `.gitignore` rule on the branch that future issue branches use.
- Branch prep failure should not continue into an implementation run as if checkout succeeded.
- Runtime commits should avoid tracking `.specseed/storage/*` even if the repo ignore is missing.

## Most Likely Root Cause Chain

1. Configure/startup wrote `.gitignore` but did not commit it to `main`.
2. Spec-change `apply.py` created issue bodies with bare dependency ids.
3. Runtime dependency parser ignored bare ids.
4. Scheduler launched FEAT-0001, FEAT-0002, FEAT-0003, and QA as independent work.
5. FEAT-0002 ran from empty `main`, without `.gitignore`.
6. Runtime `git add -A` committed `.specseed/storage/*`.
7. Later checkout/merge prep saw dirty tracked runtime state and parked branches blocked.

