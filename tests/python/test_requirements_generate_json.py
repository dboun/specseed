import json

import pytest

import requirements_generate_json as RG
from conftest import run_core, write


def _srs_table(rows):
    return (
        "| ID | Requirement | Type | Priority | Depends on |\n"
        "| -- | ----------- | ---- | -------- | ---------- |\n"
        + "\n".join(rows)
        + "\n"
    )


def test_split_row_trims_outer_pipes_cells_and_trailing_separator():
    assert RG.split_row("| a | b |") == ["a", "b"]
    assert RG.split_row(" a | b | c ") == ["a", "b", "c"]


def test_split_row_honors_escaped_pipes():
    # '\|' is a literal pipe inside a cell, not a delimiter; it is unescaped.
    assert RG.split_row(r"| a \| b | c |") == ["a | b", "c"]


def test_parse_depends_on_empty_markers_and_lists():
    assert RG.parse_depends_on("") == []
    assert RG.parse_depends_on("none") == []
    assert RG.parse_depends_on("-") == []
    assert RG.parse_depends_on("SRS-FUNC-001, SRS-NFR-002") == [
        "SRS-FUNC-001",
        "SRS-NFR-002",
    ]
    assert RG.parse_depends_on("[SRS-FUNC-001, SRS-NFR-002]") == [
        "SRS-FUNC-001",
        "SRS-NFR-002",
    ]


def test_find_tables_maps_headers_case_insensitively_and_reports_rows():
    text = """
| Name | Value |
| ---- | ----- |
| x | y |

| ID | Requirement | TYPE | Priority | Depends on |
| -- | ----------- | ---- | -------- | ---------- |
| SRS-FUNC-001 | Do it | functional | must | none |
"""
    tables = list(RG.find_tables(text))

    assert len(tables) == 2
    header_map, ncols, rows = tables[1]
    assert ncols == 5
    assert header_map["type"] == 2
    assert rows == [(8, ["SRS-FUNC-001", "Do it", "functional", "must", "none"])]


def test_main_writes_sorted_reqs_json_from_valid_srs(tmp_path):
    spec = tmp_path / ".specseed" / "spec"
    write(
        spec / "product_srs.md",
        _srs_table([
            "| SRS-NFR-002 | Stay quick | non_functional | should | SRS-FUNC-001 |",
            "| SRS-FUNC-001 | Let users sign in | functional | must | none |",
        ]),
    )

    result = run_core("requirements_generate_json.py", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "OK: extracted 2 reqs" in result.stdout
    data = json.loads((spec / "reqs.json").read_text())
    assert list(data) == ["SRS-FUNC-001", "SRS-NFR-002"]
    assert data["SRS-FUNC-001"] == {
        "text": "Let users sign in",
        "type": "functional",
        "priority": "must",
        "depends_on": [],
    }
    assert data["SRS-NFR-002"]["depends_on"] == ["SRS-FUNC-001"]


@pytest.mark.parametrize(
    "row, expected",
    [
        ("| SRS-FUNC-001 | Do it | bogus | must | none |", "invalid type"),
        ("| SRS-FUNC-001 | Do it | functional | urgent | none |", "invalid priority"),
        ("| SRS-FUNC-001 |  | functional | must | none |", "empty Requirement text"),
    ],
)
def test_main_rejects_invalid_requirement_rows(tmp_path, row, expected):
    spec = tmp_path / ".specseed" / "spec"
    write(spec / "bad_srs.md", _srs_table([row]))

    result = run_core("requirements_generate_json.py", cwd=tmp_path)

    assert result.returncode == 1
    assert "ERROR:" in result.stderr
    assert expected in result.stderr
    assert not (spec / "reqs.json").exists()


def test_main_rejects_duplicate_ids(tmp_path):
    spec = tmp_path / ".specseed" / "spec"
    write(
        spec / "bad_srs.md",
        _srs_table([
            "| SRS-FUNC-001 | First | functional | must | none |",
            "| SRS-FUNC-001 | Second | functional | should | none |",
        ]),
    )

    result = run_core("requirements_generate_json.py", cwd=tmp_path)

    assert result.returncode == 1
    assert "duplicate ID" in result.stderr
    assert not (spec / "reqs.json").exists()


def test_main_rejects_malformed_requirement_row(tmp_path):
    spec = tmp_path / ".specseed" / "spec"
    write(
        spec / "bad_srs.md",
        _srs_table([
            "| SRS-FUNC-001 | Missing depends | functional | must |",
        ]),
    )

    result = run_core("requirements_generate_json.py", cwd=tmp_path)

    assert result.returncode == 1
    assert "row has 4 cells, expected 5" in result.stderr
    assert not (spec / "reqs.json").exists()


def test_main_exits_2_when_spec_dir_missing(tmp_path):
    result = run_core("requirements_generate_json.py", cwd=tmp_path)

    assert result.returncode == 2
    assert ".specseed/spec/ directory not found" in result.stderr


def test_main_exits_2_when_no_srs_files(tmp_path):
    (tmp_path / ".specseed" / "spec").mkdir(parents=True)

    result = run_core("requirements_generate_json.py", cwd=tmp_path)

    assert result.returncode == 2
    assert "no *srs.md files found" in result.stderr

