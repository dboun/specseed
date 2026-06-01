"""gitlab_functions.py — pure helpers only, no network."""

import sys

import pytest

from conftest import REPO_ROOT, write

REMOTE = REPO_ROOT / "skills" / "specseed" / "scripts" / "remote"
sys.path.insert(0, str(REMOTE))

import gitlab_functions as gl


@pytest.mark.parametrize("raw, expected", [
    ("group/proj", ("https", None, "group/proj")),
    ("group/sub/proj", ("https", None, "group/sub/proj")),
    ("https://git.example.com/group/sub/proj.git",
     ("https", "git.example.com", "group/sub/proj")),
    ("git@git.example.com:group/sub/proj.git",
     ("https", "git.example.com", "group/sub/proj")),
])
def test_split_repo(raw, expected):
    assert gl._split_repo(raw) == expected


def test_resolve_gitlab_dot_com_and_self_hosted(monkeypatch):
    monkeypatch.delenv("GITLAB_URL", raising=False)
    assert gl._resolve("group/proj") == (
        "https://gitlab.com/api/v4", "group%2Fproj",
    )
    assert gl._resolve("https://git.example.com/group/sub/proj.git") == (
        "https://git.example.com/api/v4", "group%2Fsub%2Fproj",
    )

    monkeypatch.setenv("GITLAB_URL", "https://git.internal")
    assert gl._resolve("group/proj") == (
        "https://git.internal/api/v4", "group%2Fproj",
    )


def test_resolve_rejects_missing_or_invalid_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gl._dotenv_loaded = False
    monkeypatch.delenv("GITLAB_REPO", raising=False)
    monkeypatch.delenv("GITLAB_URL", raising=False)
    with pytest.raises(gl.GitLabError):
        gl._resolve()
    with pytest.raises(gl.GitLabError):
        gl._resolve("single-segment")


def test_next_link_csv_and_stringify():
    header = (
        '<https://gitlab.com/api/v4/projects/x/issues?page=2>; rel="next", '
        '<https://gitlab.com/api/v4/projects/x/issues?page=5>; rel="last"'
    )
    assert gl._next_link(header) == "https://gitlab.com/api/v4/projects/x/issues?page=2"
    assert gl._next_link('<https://gitlab.com/api/v4/projects/x/issues?page=5>; rel="last"') is None
    assert gl._next_link(None) is None

    assert gl._csv(["a", "b"]) == "a,b"
    assert gl._csv([]) is None
    assert gl._stringify("plain") == "plain"
    assert gl._stringify({"message": ["bad"]}) == '{"message": ["bad"]}'
    assert gl._stringify(["bad"]) == '["bad"]'
    assert gl._stringify(None) is None


def test_load_dotenv_reads_local_file_without_overriding_env(tmp_path, monkeypatch):
    write(tmp_path / ".env", "GITLAB_PAT=from-file\nGITLAB_REPO=group/proj\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITLAB_PAT", raising=False)
    monkeypatch.setenv("GITLAB_REPO", "already/set")
    gl._dotenv_loaded = False

    gl._load_dotenv()
    assert gl._dotenv_loaded is True
    assert gl.os.environ["GITLAB_PAT"] == "from-file"
    assert gl.os.environ["GITLAB_REPO"] == "already/set"
