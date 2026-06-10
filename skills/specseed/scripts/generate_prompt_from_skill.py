"""generate_prompt_from_skill.py - assemble a self-contained invocation prompt for one
specseed skill route (stdlib only).

WHY: at invocation an agent would otherwise jump around the skill tree (SKILL.md -> the
route -> each `## Mandatory skill reads` -> their reads -> the script preambles). This
walks that graph ONCE and returns every doc the route needs, in reading order, so the
prompt already contains everything.

PUBLIC API: `generate_prompt_from_skill(mode, route, subroute=None) -> str` returns the
assembled prompt as a string (raises ValueError on a bad mode / missing route or
subroute file). The CLI just prints it. Every other function is private (`__`-prefixed).

WHAT IT PRODUCES:
  1. a header line: `Specseed skill (v<version>) instructions. ... Mode/Route[/Subroute]`
     (version read from `version.txt`);
  2. `SKILL.md`;
  3. the route file `routes/<route>.md` (and, if given, `spec_subroutes/<subroute>.md`);
  4. then a depth-first walk of each file's `## Mandatory skill reads` then
     `## Mandatory skill script preamble reads`: a read is inlined verbatim and recursed
     into; a script is inlined as its PREAMBLE ONLY (the text between the first pair of
     triple-double-quotes), never the full code;
  5. a trailing `===== END OF SPECSEED SKILL FILES. PROMPT FOLLOWS ... =====` marker.
Each file appears at most once (the walk dedupes), so cyclic reads cannot loop forever.

If `route` is `spec` and NO subroute is named, EVERY `spec_subroutes/*.md` is included
(full info for all subroutes).

The two table sections are parsed strictly: `| Read | Why |` and `| Script | Use |`,
first column only. A read cell is a skill-relative path in backticks (`references/x.md`,
or a directory like `templates/spec_doc_templates/` -> every FILE inside it, `.md`/`.csv`
verbatim and `.py` as preamble). A script cell is a bare filename, resolved under
`scripts/`. Only those two sections drive the walk; any other table (e.g. a route's
Subroutes table) is ignored. A missing path is emitted as a `(NOT FOUND)` stub, not a
crash.

INPUT (argv): `<mode> [route] [subroute]`
  mode    one of: chat | specseed-ui | github | gitlab  (echoed in the header only)
  route   OPTIONAL route name -> `routes/<route>.md` (e.g. spec, impl, review, ask,
          operate). OMIT for the WHOLE skill (every route).
  subroute  optional -> `spec_subroutes/<subroute>.md` (e.g. adopt, adapt, tweak). Only
          valid WITH a route.

OUTPUT: the assembled prompt on stdout. Exit 0 ok; 2 on bad usage / unknown mode /
missing route (or subroute) file.

CLI: `python3 generate_prompt_from_skill.py specseed-ui spec adapt`
     `python3 generate_prompt_from_skill.py specseed-ui`            # whole skill
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MODES = ("chat", "specseed-ui", "github", "gitlab")

SKILL_DIR = Path(__file__).resolve().parent.parent  # .../skills/specseed

_BACKTICK_RE = re.compile(r"`([^`]+)`")


def generate_prompt_from_skill(
    mode: str, route: str | None = None, subroute: str | None = None
) -> str:
    """Assemble and return the invocation prompt.

    ``route`` optional: omit it for the WHOLE skill (every route, each recursed). With a
    ``route``, that route only; with a ``subroute`` too, that subroute. A subroute without
    a route is invalid. Raises ValueError on an unknown ``mode`` or a missing route /
    subroute file (or a subroute given with no route).
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if subroute is not None and route is None:
        raise ValueError("cannot specify a subroute without a route")

    route_rel = None
    sub_rel = None
    if route is not None:
        route_rel = f"routes/{route}.md"
        if not (SKILL_DIR / route_rel).exists():
            raise ValueError(f"route file not found: {route_rel}")
        if subroute is not None:
            sub_rel = f"spec_subroutes/{subroute}.md"
            if not (SKILL_DIR / sub_rel).exists():
                raise ValueError(f"subroute file not found: {sub_rel}")

    vfile = SKILL_DIR / "version.txt"
    version = vfile.read_text(encoding="utf-8").strip() if vfile.exists() else "?"
    header = (
        f"Specseed skill (v{version}) instructions. Skill root dir: {SKILL_DIR}. "
        f"Mode: {mode}. Route: {route if route is not None else '(all)'}."
    )
    if subroute is not None:
        header += f" Subroute: {subroute}."

    parts: list[str] = [header, ""]
    emitted: set[str] = set()
    __emit("SKILL.md", emitted, parts)
    if route_rel is None:
        # whole skill: every route, then every subroute, each recursed.
        for f in sorted((SKILL_DIR / "routes").glob("*.md")):
            __emit(f.relative_to(SKILL_DIR).as_posix(), emitted, parts)
        for f in sorted((SKILL_DIR / "spec_subroutes").glob("*.md")):
            __emit(f.relative_to(SKILL_DIR).as_posix(), emitted, parts)
    else:
        __emit(route_rel, emitted, parts)
        if sub_rel is not None:
            __emit(sub_rel, emitted, parts)
        elif route == "spec":
            # spec with no subroute named -> include every subroute (full info).
            for f in sorted((SKILL_DIR / "spec_subroutes").glob("*.md")):
                __emit(f.relative_to(SKILL_DIR).as_posix(), emitted, parts)
    parts.append("===== END OF SPECSEED SKILL FILES. NO REASON TO LOOK AROUND SKILL OR READ SCRIPTS. YOU SHOULD KNOW EVERYTHING NEEDED. =====")
    parts.append("===== PROMPT FOLLOWS (IF EMPTY STOP AND WAIT) =====")
    return "\n".join(parts)


def __first_cells(text: str, heading: str) -> list[str]:
    """First-column cells of the table under ``heading`` (header + separator dropped)."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines) and lines[i].strip() != heading:
        i += 1
    i += 1  # past the heading (or past end -> loop below does nothing)
    while i < len(lines) and not lines[i].startswith("## "):
        s = lines[i].strip()
        if s.startswith("|"):
            cell = s.strip("|").split("|")[0].strip()
            low = cell.lower()
            is_sep = cell != "" and set(cell) <= set("-: ")
            if low not in ("read", "script") and not is_sep:
                out.append(cell)
        i += 1
    return out


def __cell_path(cell: str) -> str | None:
    """The skill-relative path a table cell points at, or None."""
    m = _BACKTICK_RE.search(cell)
    return m.group(1).strip() if m else None


def __preamble(text: str) -> str:
    """The module docstring: text between the first pair of triple-double-quotes."""
    i = text.find('"""')
    if i == -1:
        return "(no module docstring)"
    j = text.find('"""', i + 3)
    if j == -1:
        return "(unterminated module docstring)"
    return text[i + 3:j].strip()


def __emit(relpath: str, emitted: set[str], parts: list[str]) -> None:
    """Append ``relpath`` (deduped) to ``parts`` and recurse into its read/script tables.

    A directory expands to its files (no recursion). A ``.py`` file contributes its
    preamble only. A ``.md`` (or other text) file is verbatim, then its two table
    sections are walked.
    """
    relpath = relpath.rstrip()
    full = SKILL_DIR / relpath

    if relpath.endswith("/") or full.is_dir():
        dir_key = relpath.rstrip("/") + "/"
        if dir_key in emitted:
            return
        emitted.add(dir_key)
        if not full.is_dir():
            __block(parts, relpath, "(NOT FOUND)")
            return
        for child in sorted(full.iterdir()):
            if child.is_file():
                __emit(child.relative_to(SKILL_DIR).as_posix(), emitted, parts)
        return

    if relpath in emitted:
        return
    emitted.add(relpath)

    if not full.exists():
        __block(parts, relpath, "(NOT FOUND)")
        return

    text = full.read_text(encoding="utf-8")

    if relpath.endswith(".py"):
        __block(parts, relpath + " (preamble)", __preamble(text))
        return

    __block(parts, relpath, text)
    for cell in __first_cells(text, "## Mandatory skill reads"):
        p = __cell_path(cell)
        if p:
            __emit(p, emitted, parts)
    for cell in __first_cells(text, "## Mandatory skill script preamble reads"):
        p = __cell_path(cell)
        if p:
            if "/" not in p:
                p = "scripts/" + p
            __emit(p, emitted, parts)


def __block(parts: list[str], label: str, content: str) -> None:
    parts.append(f"===== {label} =====")
    parts.append(content.rstrip("\n"))
    parts.append("")


def __main(argv: list[str]) -> int:
    if not (1 <= len(argv) <= 3):
        print("Usage: generate_prompt_from_skill.py <mode> [route] [subroute]", file=sys.stderr)
        return 2
    mode = argv[0]
    route = argv[1] if len(argv) >= 2 else None
    subroute = argv[2] if len(argv) == 3 else None
    try:
        out = generate_prompt_from_skill(mode, route, subroute)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(__main(sys.argv[1:]))
    except BrokenPipeError:
        # Output was piped to a reader that closed early (e.g. `| head`); not an error.
        raise SystemExit(0)
