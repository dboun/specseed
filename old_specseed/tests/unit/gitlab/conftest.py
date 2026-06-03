"""
LIVE GitLab tests — they hit the real GitLab REST API (works against self-hosted
instances too; the API root is derived from GITLAB_REPO's host / GITLAB_URL).

NOT part of the default test run: `pytest.ini` sets testpaths=tests/unit/python,
so a bare `python3 -m pytest` never collects these. Run them deliberately before
a release:

    python3 -m pytest tests/unit/gitlab

Requirements:
  * GITLAB_PAT + GITLAB_REPO in the environment or repo-root .env (the wrapper's
    own _load_dotenv finds the .env). Missing creds → every test SKIPS.
  * The project is a throwaway playground. Tests use unique timestamped names and
    tear everything down in finally blocks, so they neither assume an empty
    project nor leave residue. (No delete-issue wrapper exists — cleanup closes
    issues; labels/files/branches are deleted outright.)

A 2s gap is enforced between tests (autouse fixture) to be gentle on the host.
"""

import sys
import time
import uuid
from pathlib import Path

import pytest

# repo_root/tests/unit/gitlab/conftest.py → repo_root
REPO_ROOT = Path(__file__).resolve().parents[3]
REMOTE = REPO_ROOT / "skills" / "specseed" / "scripts" / "remote"
sys.path.insert(0, str(REMOTE))

import gitlab_functions as gl  # noqa: E402


@pytest.fixture(scope="session")
def glclient():
    """The gitlab_functions module, with creds verified (else skip the suite)."""
    gl._load_dotenv()
    if not gl.os.environ.get("GITLAB_PAT") or not gl.os.environ.get("GITLAB_REPO"):
        pytest.skip("GITLAB_PAT / GITLAB_REPO not set (env or .env)")
    return gl


@pytest.fixture(autouse=True)
def _rate_gap():
    """~2s between tests so we stay gentle on the GitLab host."""
    yield
    time.sleep(2)


# Self-hosted GitLab behind a proxy occasionally returns a transient 5xx
# (502/503/504); retry those so the smoke run doesn't flake on infra hiccups.
_TRANSIENT = {502, 503, 504}


@pytest.fixture(autouse=True, scope="session")
def _retry_transient():
    orig = gl._raw_request

    def wrapped(method, url, body=None):
        last = None
        for attempt in range(4):
            try:
                return orig(method, url, body)
            except gl.GitLabError as e:
                if e.status not in _TRANSIENT:
                    raise
                last = e
                time.sleep(2 * (attempt + 1))
        raise last

    gl._raw_request = wrapped
    yield
    gl._raw_request = orig


@pytest.fixture
def tag():
    """A short unique token for naming created resources."""
    return f"specseed-test-{int(time.time())}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def base_ref(glclient):
    """The default branch to push against — or skip if the project can't be pushed.

    The repo-file / branch tests need a base commit + push rights. An empty repo
    (no commits → no ref) or a token without push permission (e.g. Developer on a
    protected default branch) makes those functions untestable here; skip cleanly
    rather than fail on an environmental limit.
    """
    proj = glclient.get_project()
    if proj.get("empty_repo"):
        pytest.skip("GitLab project is empty (no base ref to branch from / push to)")
    return proj["default_branch"]
