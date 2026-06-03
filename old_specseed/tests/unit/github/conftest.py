"""
LIVE GitHub tests — they hit the real GitHub REST API.

NOT part of the default test run: `pytest.ini` sets testpaths=tests/unit/python,
so a bare `python3 -m pytest` never collects these. Run them deliberately before
a release:

    python3 -m pytest tests/unit/github

Requirements:
  * GITHUB_PAT + GITHUB_REPO in the environment or repo-root .env (the wrapper's
    own _load_dotenv finds the .env). Missing creds → every test SKIPS.
  * The repo is a throwaway playground. Tests use unique timestamped names and
    tear everything down in finally blocks, so they neither assume an empty repo
    nor leave residue. (GitHub issues can't be DELETEd via REST — cleanup closes
    them; labels/files are deleted outright.)

A 2s gap is enforced between tests (autouse fixture) to stay clear of GitHub's
secondary rate limits on content creation.
"""

import sys
import time
import uuid
from pathlib import Path

import pytest

# repo_root/tests/unit/github/conftest.py → repo_root
REPO_ROOT = Path(__file__).resolve().parents[3]
REMOTE = REPO_ROOT / "skills" / "specseed" / "scripts" / "remote"
sys.path.insert(0, str(REMOTE))

import github_functions as gh  # noqa: E402


@pytest.fixture(scope="session")
def ghclient():
    """The github_functions module, with creds verified (else skip the suite)."""
    gh._load_dotenv()
    if not gh.os.environ.get("GITHUB_PAT") or not gh.os.environ.get("GITHUB_REPO"):
        pytest.skip("GITHUB_PAT / GITHUB_REPO not set (env or .env)")
    return gh


@pytest.fixture(autouse=True)
def _rate_gap():
    """~2s between tests so we don't trip GitHub's secondary rate limits."""
    yield
    time.sleep(2)


# Retry transient 5xx (502/503/504) so a momentary GitHub/infra hiccup doesn't
# flake the smoke run.
_TRANSIENT = {502, 503, 504}


@pytest.fixture(autouse=True, scope="session")
def _retry_transient():
    orig = gh._raw_request

    def wrapped(method, url, body=None):
        last = None
        for attempt in range(4):
            try:
                return orig(method, url, body)
            except gh.GitHubError as e:
                if e.status not in _TRANSIENT:
                    raise
                last = e
                time.sleep(2 * (attempt + 1))
        raise last

    gh._raw_request = wrapped
    yield
    gh._raw_request = orig


@pytest.fixture
def tag():
    """A short unique token for naming created resources."""
    return f"specseed-test-{int(time.time())}-{uuid.uuid4().hex[:6]}"
