"""Live smoke tests for the basic gitlab_functions primitives.

Each test exercises one small lifecycle (create → read → clean up) against the
playground project. See conftest.py for creds/skip + the inter-test rate gap.
"""

import pytest

import gitlab_functions as gl


def skip_if_unpushable(err):
    """Turn a 403 'not allowed to push' into a skip; re-raise anything else."""
    if isinstance(err, gl.GitLabError) and err.status == 403:
        pytest.skip("token lacks repository push permission on this project")
    raise err


# --------------------------------------------------------------------------- #
# meta / auth (read-only)
# --------------------------------------------------------------------------- #
def test_authenticated_user_and_project(glclient):
    user = glclient.get_authenticated_user()
    assert isinstance(user, dict) and user.get("username")

    version = glclient.get_gitlab_version()
    assert version.get("version")

    project = glclient.get_project()
    # path_with_namespace is "group/.../project"; default_branch backs other tests.
    assert project.get("path_with_namespace")
    assert project.get("default_branch")


# --------------------------------------------------------------------------- #
# issue lifecycle (cleanup = close)
# --------------------------------------------------------------------------- #
def test_issue_lifecycle(glclient, tag):
    issue = glclient.create_gitlab_issue(title=f"{tag} issue", description="hello")
    iid = issue["iid"]
    try:
        fetched = glclient.get_gitlab_issue(iid)
        assert fetched["iid"] == iid
        assert fetched["title"] == f"{tag} issue"

        glclient.add_comment_to_gitlab_issue(iid, "a note")
        notes = glclient.list_gitlab_issue_comments(iid)
        assert any(n["body"] == "a note" for n in notes)
    finally:
        closed = glclient.close_gitlab_issue(iid)
        assert closed["state"] == "closed"


# --------------------------------------------------------------------------- #
# label lifecycle (cleanup = delete)
# --------------------------------------------------------------------------- #
def test_label_lifecycle(glclient, tag):
    name = f"{tag}-label"
    glclient.create_label(name, color="#ededed", description="temp")
    try:
        names = {lab["name"] for lab in glclient.list_labels()}
        assert name in names
    finally:
        glclient.delete_label(name)


# --------------------------------------------------------------------------- #
# file lifecycle (cleanup = delete)
# --------------------------------------------------------------------------- #
def test_file_lifecycle(glclient, tag, base_ref):
    # Push to a throwaway feature branch off the default branch — the default
    # branch is typically protected (Developers can't push to it), but feature
    # branches are fair game. Deleting the branch cleans up the file too.
    branch = f"specseed-test-{tag}"
    path = f"specseed-test/{tag}.txt"
    content = f"content {tag}\n"
    try:
        glclient.create_branch(branch, ref=base_ref)
    except gl.GitLabError as e:
        skip_if_unpushable(e)
    try:
        glclient.create_or_update_file(
            path, content, commit_message=f"test: add {path}", branch=branch)
        got = glclient.get_file_contents(path, ref=branch)
        assert got.get("decoded_content") == content
    finally:
        glclient.delete_branch(branch)


# --------------------------------------------------------------------------- #
# branch lifecycle (cleanup = delete)
# --------------------------------------------------------------------------- #
def test_branch_lifecycle(glclient, tag, base_ref):
    name = f"specseed-test-{tag}"
    try:
        glclient.create_branch(name, ref=base_ref)
    except gl.GitLabError as e:
        skip_if_unpushable(e)
    try:
        names = {b["name"] for b in glclient.list_branches()}
        assert name in names
    finally:
        glclient.delete_branch(name)
