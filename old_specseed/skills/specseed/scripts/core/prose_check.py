#!/usr/bin/env python3
"""
prose_check.py — deterministic em/en-dash gate for produced PROSE.

The skill's doc-writing rule bans em-dashes (U+2014) and en-dashes (U+2013) in
human-read prose. The humanizer pass is supposed to strip them, but a model can
under-apply it, so this is the mechanical backstop: grep the produced prose for the
banned codepoints and report offenders. NOT for machine artifacts (frontmatter/JSON/
SRS tables) or the skill's own internal docs — point it at produced prose only.

Usage:
  prose_check.py [FILE ...]      # check the given files
  prose_check.py                 # default: .specseed/spec/*.md + root README.md + CLAUDE.md

Exit 0 = clean, 1 = offenders found (prints `path:line: text`), 2 = usage error.
Fenced code blocks (``` ... ```) are skipped. Stdlib only.
"""

import glob
import sys
from pathlib import Path

BANNED = {"—": "em-dash", "–": "en-dash"}


def find_root(start=None):
    p = Path(start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / ".specseed").is_dir():
            return cand
    return p


def default_targets(root):
    targets = sorted(glob.glob(str(root / ".specseed" / "spec" / "*.md")))
    for name in ("README.md", "CLAUDE.md"):
        f = root / name
        if f.exists():
            targets.append(str(f))
    return targets


def scan(path):
    """Return [(lineno, char_name, line_text)] for banned dashes outside code fences."""
    hits = []
    in_fence = False
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return hits
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for ch, name in BANNED.items():
            if ch in line:
                hits.append((i, name, line.strip()))
                break
    return hits


def main(argv):
    files = argv or default_targets(find_root())
    if not files:
        print("prose_check: no files to check", file=sys.stderr)
        return 0
    offenders = 0
    for f in files:
        for lineno, name, text in scan(f):
            print(f"{f}:{lineno}: {name}: {text}")
            offenders += 1
    if offenders:
        print(f"\n{offenders} banned dash(es) found. "
              f"Replace em/en dashes with commas, colons, parens, or sentence breaks.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
