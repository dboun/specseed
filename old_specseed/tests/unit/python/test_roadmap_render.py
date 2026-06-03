import json

from conftest import write


def _seed(pm):
    write(pm / "tickets.json", json.dumps({
        "PROJ-0001": {"issues_done": 1, "issues_total": 3},
        "PROJ-0002": {"issues_done": 2, "issues_total": 2},
    }))
    write(pm / "ROADMAP.md",
          "# Roadmap\n\n"
          "- PROJ-0001 Base (0/3 complete)\n"
          "- PROJ-0002 Ship\n"
          "- PROJ-9999 Deferred (0/0 complete)\n"
          "- Cross-cutting PROJ-0001 and PROJ-0002 (0/0 complete)\n")


def test_refreshes_ticket_counts_and_tolerates_missing_titles(repo):
    _seed(repo.pm)
    res = repo.core("roadmap_render.py")
    assert res.returncode == 0, res.stderr

    text = (repo.pm / "ROADMAP.md").read_text()
    assert "- PROJ-0001 Base (1/3 complete)" in text
    assert "- PROJ-0002 Ship (2/2 complete)" in text
    assert "- PROJ-9999 Deferred (0/0 complete)" in text
    assert "- Cross-cutting PROJ-0001 and PROJ-0002 (0/0 complete)" in text


def test_check_current_exits_zero_and_preserves_file(repo):
    _seed(repo.pm)
    assert repo.core("roadmap_render.py").returncode == 0
    before = (repo.pm / "ROADMAP.md").read_text()

    res = repo.core("roadmap_render.py", "--check")
    assert res.returncode == 0, res.stderr
    assert "up to date" in res.stdout
    assert (repo.pm / "ROADMAP.md").read_text() == before


def test_check_stale_exits_one_and_does_not_write(repo):
    _seed(repo.pm)
    before = (repo.pm / "ROADMAP.md").read_text()

    res = repo.core("roadmap_render.py", "--check")
    assert res.returncode == 1
    assert "DRIFT" in res.stderr
    assert (repo.pm / "ROADMAP.md").read_text() == before


def test_missing_inputs_exit_two(repo):
    res = repo.core("roadmap_render.py")
    assert res.returncode == 2
    assert "ROADMAP.md not found" in res.stderr
