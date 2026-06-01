"""
requirements_generate_json.py

Parses all SRS markdown files in .specseed/spec/ and extracts requirement
table rows into .specseed/spec/reqs.json.

See module-level docstring in the skill spec for full contract. Summary:
- Walks .specseed/spec/ for *srs.md and srs.md
- Finds markdown tables with required columns: ID, Requirement, Type, Priority,
  Depends on (NO Verified by — that data is derived on demand by
  verification_map.py from tickets.json)
- Validates type ∈ {functional, non_functional, constraint}, priority ∈
  {must, should, could, wont}
- Writes .specseed/spec/reqs.json sorted by ID
- Errors on malformed rows (file+line); exits 1
- Exits 2 if .specseed/spec/ not found or no SRS files
"""

import json
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"^SRS-[A-Z]+-\d+$")
VALID_TYPES = {"functional", "non_functional", "constraint"}
VALID_PRIORITIES = {"must", "should", "could", "wont"}
EMPTY_MARKERS = {"-", "", "—", "(none)", "n/a", "none"}
REQUIRED_COLS = {"id", "requirement", "type", "priority", "depends on"}

SEPARATOR_RE = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$")

# A column delimiter is a pipe NOT preceded by a backslash; '\|' is a literal.
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def split_row(line):
    """Split a markdown table row '| a | b | c |' into ['a', 'b', 'c'].

    A pipe escaped as '\\|' is a literal character inside a cell, not a column
    delimiter, and is unescaped to '|' in the returned value."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in CELL_SPLIT_RE.split(s)]


def find_tables(file_text):
    """Yield (header_map, header_count, [(line_num, cells), ...]) per table.

    line_num is 1-based for human-readable errors.
    """
    lines = file_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if SEPARATOR_RE.match(next_line):
                # Found a table. Parse header.
                header_cells = split_row(lines[i])
                header_lower = [c.lower() for c in header_cells]
                header_map = {name: idx for idx, name in enumerate(header_lower)}

                # Walk data rows
                rows = []
                j = i + 2
                while j < len(lines):
                    stripped = lines[j].strip()
                    if not stripped or not stripped.startswith("|"):
                        break
                    cells = split_row(lines[j])
                    rows.append((j + 1, cells))
                    j += 1

                yield header_map, len(header_cells), rows
                i = j
                continue
        i += 1


def parse_depends_on(cell):
    s = cell.strip()
    if s.lower() in EMPTY_MARKERS:
        return []
    # Tolerate a JSON-ish bracketed list, e.g. "[SRS-A, SRS-B]".
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    parts = [p.strip() for p in s.split(",")]
    return [p for p in parts if p]


def main():
    spec_dir = Path(".specseed/spec")
    if not spec_dir.exists():
        print("ERROR: .specseed/spec/ directory not found in current working directory",
              file=sys.stderr)
        sys.exit(2)

    srs_files = sorted({p.resolve() for p in spec_dir.glob("*srs.md")})
    if not srs_files:
        print("ERROR: no *srs.md files found under .specseed/spec/", file=sys.stderr)
        sys.exit(2)

    out = {}
    errors = []

    for srs_path in srs_files:
        try:
            text = srs_path.read_text(encoding="utf-8")
        except Exception as e:
            errors.append(f"{srs_path}: cannot read file: {e}")
            continue

        for header_map, ncols, rows in find_tables(text):
            # Only process tables with all required columns
            if not REQUIRED_COLS.issubset(set(header_map.keys())):
                continue

            id_col = header_map["id"]
            req_col = header_map["requirement"]
            type_col = header_map["type"]
            pri_col = header_map["priority"]
            dep_col = header_map["depends on"]

            for line_num, cells in rows:
                if len(cells) != ncols:
                    # malformed row — only error if first cell looks like SRS id
                    if cells and ID_RE.match(cells[0].strip()):
                        errors.append(
                            f"{srs_path}:{line_num}: row has {len(cells)} cells, "
                            f"expected {ncols}"
                        )
                    continue

                rid = cells[id_col].strip()
                if not ID_RE.match(rid):
                    continue  # not a SRS req row

                text_val = cells[req_col].strip()
                type_val = cells[type_col].strip().lower()
                pri_val = cells[pri_col].strip().lower()
                dep_val = parse_depends_on(cells[dep_col])

                if not text_val:
                    errors.append(f"{srs_path}:{line_num}: {rid}: empty Requirement text")
                    continue
                if type_val not in VALID_TYPES:
                    errors.append(
                        f"{srs_path}:{line_num}: {rid}: invalid type {type_val!r} "
                        f"(expected one of {sorted(VALID_TYPES)})"
                    )
                    continue
                if pri_val not in VALID_PRIORITIES:
                    errors.append(
                        f"{srs_path}:{line_num}: {rid}: invalid priority {pri_val!r} "
                        f"(expected one of {sorted(VALID_PRIORITIES)})"
                    )
                    continue

                if rid in out:
                    errors.append(
                        f"{srs_path}:{line_num}: {rid}: duplicate ID "
                        f"(already defined earlier)"
                    )
                    continue

                out[rid] = {
                    "text": text_val,
                    "type": type_val,
                    "priority": pri_val,
                    "depends_on": dep_val,
                }

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Sort by ID for stable output
    sorted_out = {k: out[k] for k in sorted(out.keys())}
    output_path = spec_dir / "reqs.json"
    output_path.write_text(json.dumps(sorted_out, indent=2) + "\n", encoding="utf-8")

    print(f"OK: extracted {len(sorted_out)} reqs from "
          f"{len(srs_files)} SRS file(s) → {output_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
