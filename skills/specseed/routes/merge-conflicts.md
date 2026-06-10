# merge-conflicts route

## Short description

Resolve git merge conflicts in the target repo so the runtime can finish (or abort) a
merge it already started. Runtime-internal: the scheduler invokes you mid-merge, not a
human label. You ONLY edit the conflicted files; the runtime owns git and completes or
aborts the merge from what you leave behind.

## Mandatory skill reads

| Read | Why |
|------|-----|

## Mandatory skill script preamble reads

| Script | Use |
|--------|-----|

## Inputs (handed in at runtime)

- The conflicted file list, the issue branch + primary branch names, and the merge
  direction: the final `branch -> primary` merge, or bringing primary INTO the branch to
  ready it for a later gate. The repo is mid-merge on the branch right now.

## Hard rules

- Resolve EVERY conflict by editing the files. Keep both sides' intent where they are
  compatible, pick the correct result where they are not, and REMOVE all conflict markers
  (`<<<<<<<`, `=======`, `>>>>>>>`). Leave the tree building and test-passing.
- NEVER run git: no add, commit, merge, rebase, or abort. The runtime completes the merge
  when no markers remain and aborts when any do.
- A conflict you cannot resolve mechanically safe: leave that file's markers in place and
  say so in your report. The runtime aborts and hands it to a human. A clean abort beats a
  wrong merge.
- Scope: touch ONLY the conflicted files. Do not refactor untouched code, change workflow
  labels, or approve anything.
