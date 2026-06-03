"""Live smoke tests for the basic github_functions primitives.

Each test exercises one small lifecycle (create → read → clean up) against the
playground repo. See conftest.py for creds/skip + the inter-test rate gap.
"""

import github_functions as gh


# --------------------------------------------------------------------------- #
# meta / auth (read-only)
# --------------------------------------------------------------------------- #
def test_authenticated_user_and_repo(ghclient):
    user = ghclient.get_authenticated_user()
    assert isinstance(user, dict) and user.get("login")

    repo = ghclient.get_repo()
    # full_name is "owner/name"; matches what GITHUB_REPO normalizes to.
    assert repo.get("full_name", "").lower() == ghclient._repo().lower()


# --------------------------------------------------------------------------- #
# issue lifecycle (cleanup = close; REST can't delete issues)
# --------------------------------------------------------------------------- #
def test_issue_lifecycle(ghclient, tag):
    issue = ghclient.create_github_issue(title=f"{tag} issue", body="hello")
    num = issue["number"]
    try:
        fetched = ghclient.get_github_issue(num)
        assert fetched["number"] == num
        assert fetched["title"] == f"{tag} issue"

        ghclient.add_comment_to_github_issue(num, "a comment")
        comments = ghclient.list_github_issue_comments(num)
        assert any(c["body"] == "a comment" for c in comments)
    finally:
        closed = ghclient.close_github_issue(num)
        assert closed["state"] == "closed"


# --------------------------------------------------------------------------- #
# label lifecycle (cleanup = delete)
# --------------------------------------------------------------------------- #
def test_label_lifecycle(ghclient, tag):
    name = f"{tag}-label"
    ghclient.create_label(name, color="ededed", description="temp")
    try:
        names = {lab["name"] for lab in ghclient.list_labels()}
        assert name in names
    finally:
        res = ghclient.delete_label(name)
        assert res == {"ok": True} or res is None


# --------------------------------------------------------------------------- #
# file lifecycle (cleanup = delete)
# --------------------------------------------------------------------------- #
def test_file_lifecycle(ghclient, tag):
    path = f"specseed-test/{tag}.txt"
    content = f"content {tag}\n"
    created = ghclient.create_or_update_file(
        path, content, message=f"test: add {path}")
    sha = created["content"]["sha"]
    try:
        got = ghclient.get_file_contents(path)
        assert got.get("decoded_content") == content
    finally:
        ghclient.delete_file(path, message=f"test: rm {path}", sha=sha)
        # gone now → reading 404s
        try:
            ghclient.get_file_contents(path)
            raise AssertionError("file should be deleted")
        except gh.GitHubError as e:
            assert e.status == 404
