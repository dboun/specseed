"""requirements_generate_json.py - SRS requirement tables -> reqs.json.

A skill helper (stdlib only). Walks a spec dir for ``*srs.md`` files, extracts the
requirement tables, and writes ``reqs.json`` next to them. Operates ONLY on local
spec files; it never touches the remote or the tracker DB.

Table contract: a markdown table whose header carries the columns ``ID``,
``Requirement``, ``Type``, ``Priority``, ``Depends on`` (case-insensitive). A row is a
requirement when its ID cell matches ``SRS-<COMP>-<N>``. ``type`` must be one of
{functional, non_functional, constraint}; ``priority`` one of {must, should, could,
wont}. ``component`` is derived from the id (the ``<COMP>`` segment).

Output ``reqs.json`` shape (sorted by id):

    {"SRS-API-001": {"text": "...", "type": "functional", "priority": "must",
                     "component": "API", "depends_on": ["SRS-API-000"]}, ...}

CLI: ``python3 requirements_generate_json.py [spec_dir]``  (spec_dir default ``spec``).
Exit 0 ok, 1 malformed rows, 2 spec dir / SRS files missing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"^SRS-([A-Z0-9]+)-\d+$")
VALID_TYPES = {"functional", "non_functional", "constraint"}
VALID_PRIORITIES = {"must", "should", "could", "wont"}
EMPTY_MARKERS = {"-", "", "—", "(none)", "n/a", "none"}
REQUIRED_COLS = {"id", "requirement", "type", "priority", "depends on"}

SEPARATOR_RE = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$")
# A column delimiter is a pipe NOT preceded by a backslash; '\|' is a literal.
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def split_row(line: str) -> list[str]:
    """Split a markdown row '| a | b |' into ['a', 'b'] (un-escaping '\\|')."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in CELL_SPLIT_RE.split(s)]


def find_tables(file_text: str):
    """Yield (header_map, header_count, [(line_num, cells), ...]) per table."""
    lines = file_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and SEPARATOR_RE.match(lines[i + 1].strip()):
            header_cells = split_row(lines[i])
            header_map = {name.lower(): idx for idx, name in enumerate(header_cells)}
            rows = []
            j = i + 2
            while j < len(lines):
                stripped = lines[j].strip()
                if not stripped or not stripped.startswith("|"):
                    break
                rows.append((j + 1, split_row(lines[j])))
                j += 1
            yield header_map, len(header_cells), rows
            i = j
            continue
        i += 1


def parse_depends_on(cell: str) -> list[str]:
    s = cell.strip()
    if s.lower() in EMPTY_MARKERS:
        return []
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [p.strip() for p in s.split(",") if p.strip()]


def generate(spec_dir: str | Path) -> tuple[dict, list[str]]:
    """Parse every ``*srs.md`` under ``spec_dir``. Returns (reqs, errors)."""
    spec_path = Path(spec_dir)
    out: dict[str, dict] = {}
    errors: list[str] = []
    srs_files = sorted({p.resolve() for p in spec_path.glob("*srs.md")})
    for srs_path in srs_files:
        try:
            text = srs_path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{srs_path}: cannot read file: {exc}")
            continue
        for header_map, ncols, rows in find_tables(text):
            if not REQUIRED_COLS.issubset(header_map.keys()):
                continue
            id_col, req_col = header_map["id"], header_map["requirement"]
            type_col, pri_col = header_map["type"], header_map["priority"]
            dep_col = header_map["depends on"]
            for line_num, cells in rows:
                if len(cells) != ncols:
                    if cells and ID_RE.match(cells[0].strip()):
                        errors.append(f"{srs_path}:{line_num}: row has {len(cells)} cells, expected {ncols}")
                    continue
                rid = cells[id_col].strip()
                match = ID_RE.match(rid)
                if not match:
                    continue
                text_val = cells[req_col].strip()
                type_val = cells[type_col].strip().lower()
                pri_val = cells[pri_col].strip().lower()
                if not text_val:
                    errors.append(f"{srs_path}:{line_num}: {rid}: empty Requirement text")
                    continue
                if type_val not in VALID_TYPES:
                    errors.append(f"{srs_path}:{line_num}: {rid}: invalid type {type_val!r}")
                    continue
                if pri_val not in VALID_PRIORITIES:
                    errors.append(f"{srs_path}:{line_num}: {rid}: invalid priority {pri_val!r}")
                    continue
                if rid in out:
                    errors.append(f"{srs_path}:{line_num}: {rid}: duplicate ID")
                    continue
                out[rid] = {
                    "text": text_val,
                    "type": type_val,
                    "priority": pri_val,
                    "component": match.group(1),
                    "depends_on": parse_depends_on(cells[dep_col]),
                }
    return {k: out[k] for k in sorted(out)}, errors


def main(argv: list[str]) -> int:
    spec_dir = Path(argv[1]) if len(argv) > 1 else Path("spec")
    if not spec_dir.exists():
        print(f"ERROR: spec dir {spec_dir} not found", file=sys.stderr)
        return 2
    if not sorted(spec_dir.glob("*srs.md")):
        print(f"ERROR: no *srs.md files under {spec_dir}", file=sys.stderr)
        return 2
    reqs, errors = generate(spec_dir)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    out_path = spec_dir / "reqs.json"
    out_path.write_text(json.dumps(reqs, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {len(reqs)} reqs -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
