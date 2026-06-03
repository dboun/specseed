"""prose_check.py — deterministic em/en-dash gate for produced prose."""

from conftest import write, run_core


def test_flags_em_and_en_dashes(tmp_path):
    f = tmp_path / "vision.md"
    f.write_text("Clean line.\nThis has an em dash — here.\nEn dash – too.\n",
                 encoding="utf-8")
    r = run_core("prose_check.py", str(f), cwd=tmp_path)
    assert r.returncode == 1
    assert "em-dash" in r.stdout
    assert "en-dash" in r.stdout
    assert f"{f}:2" in r.stdout
    assert f"{f}:3" in r.stdout


def test_clean_file_and_hyphen_minus_pass(tmp_path):
    f = tmp_path / "doc.md"
    # plain hyphen-minus (lists, ranges, table rule) must NOT be flagged
    f.write_text("# Title\n\n- bullet one\n- bullet two\n\n| a | b |\n| --- | --- |\n"
                 "Range 1-5 and a well-formed sentence.\n", encoding="utf-8")
    r = run_core("prose_check.py", str(f), cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert r.stdout.strip() == ""


def test_skips_fenced_code_blocks(tmp_path):
    f = tmp_path / "sdd.md"
    f.write_text("Prose is clean.\n\n```\ncode with an em dash — is allowed\n```\n",
                 encoding="utf-8")
    r = run_core("prose_check.py", str(f), cwd=tmp_path)
    assert r.returncode == 0, r.stdout


def test_default_targets_scan_spec_and_root_docs(tmp_path):
    write(tmp_path / ".specseed" / "spec" / "sad.md", "An em dash — sneaks in.\n")
    write(tmp_path / "README.md", "Clean readme.\n")
    r = run_core("prose_check.py", cwd=tmp_path)   # no args → defaults
    assert r.returncode == 1
    assert "sad.md" in r.stdout
