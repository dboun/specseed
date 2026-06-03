"""github_functions.py — pure helpers only, no network."""

import sys

import pytest

from conftest import REPO_ROOT, write

REMOTE = REPO_ROOT / "skills" / "specseed" / "scripts" / "remote"
sys.path.insert(0, str(REMOTE))

import github_functions as gh


@pytest.mark.parametrize("raw", [
    "owner/name",
    "https://github.com/owner/name.git",
    "github.com/owner/name",
    "github.com/owner/name/",
    "git@github.com:owner/name.git",
])
def test_normalize_repo_accepts_common_forms(raw):
    assert gh._normalize_repo(raw) == "owner/name"


def test_repo_rejects_garbage_reference():
    assert gh._normalize_repo("garbage") == "garbage"
    with pytest.raises(gh.GitHubError):
        gh._repo("garbage")


def test_next_link_extracts_rel_next():
    header = (
        '<https://api.github.com/repos/o/r/issues?page=2>; rel="next", '
        '<https://api.github.com/repos/o/r/issues?page=5>; rel="last"'
    )
    assert gh._next_link(header) == "https://api.github.com/repos/o/r/issues?page=2"
    assert gh._next_link('<https://api.github.com/repos/o/r/issues?page=5>; rel="last"') is None
    assert gh._next_link(None) is None


def test_coerce_list_accepts_json_and_bare_scalar():
    # valid JSON list still parses as before
    assert gh._coerce('["a", "b"]', list) == ["a", "b"]
    # bare scalar (e.g. `--labels draft`) falls back to a single-element list
    assert gh._coerce("draft", list) == ["draft"]


def test_coerce_dict_still_requires_valid_json():
    assert gh._coerce('{"k": 1}', dict) == {"k": 1}
    with pytest.raises(Exception):
        gh._coerce("not-json", dict)


def test_load_dotenv_reads_local_file_without_overriding_env(tmp_path, monkeypatch):
    write(tmp_path / ".env", "GITHUB_PAT=from-file\nGITHUB_REPO=owner/name\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("GITHUB_REPO", "already/set")
    gh._dotenv_loaded = False

    gh._load_dotenv()
    assert gh._dotenv_loaded is True
    assert gh.os.environ["GITHUB_PAT"] == "from-file"
    assert gh.os.environ["GITHUB_REPO"] == "already/set"
