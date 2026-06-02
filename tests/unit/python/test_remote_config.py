"""remote_config.py — remote state I/O + provider adapter wiring."""

import json
import re
import sys

from conftest import REPO_ROOT, write

REMOTE = REPO_ROOT / "skills" / "specseed" / "scripts" / "remote"
sys.path.insert(0, str(REMOTE))

import remote_config as rc


def _specseed_root(tmp_path):
    (tmp_path / ".specseed" / "memory").mkdir(parents=True)
    return tmp_path


def test_default_state_round_trips_only_state_keys(tmp_path):
    root = _specseed_root(tmp_path)
    state = rc.default_state("owner/repo", ["alice"])
    state["provider"] = "github"
    state["retry_delay_minutes"] = 15

    p = rc.save_state(state, root)
    assert p == root / ".specseed" / "memory" / "remote.json"

    loaded = rc.load_state(root)
    assert loaded["repo"] == "owner/repo"
    assert loaded["allowlist"] == ["alice"]
    assert loaded["permanent"] == {
        "roadmap": None, "timeline": None, "control": None, "sprint": None,
    }
    assert loaded["map"] == {}
    assert loaded["cli_cursor"] is None
    assert loaded["cli_cursor_ids"] == []
    assert loaded["pull_cursor"] is None
    assert loaded["labels_seeded"] is False
    assert loaded["initialized"] is False
    assert "provider" not in loaded
    assert "retry_delay_minutes" not in loaded


def test_find_root_walks_up_to_specseed_dir(tmp_path):
    root = _specseed_root(tmp_path)
    nested = root / "a" / "b" / "c"
    nested.mkdir(parents=True)

    assert rc.find_root(nested) == root


def test_load_runtime_merges_backend_provider(tmp_path):
    root = _specseed_root(tmp_path)
    write(root / ".specseed" / "memory" / "config.json", json.dumps({
        "backend": {"enabled": True, "provider": "gitlab",
                    "ignore_labels": ["draft", "park"]},
    }))
    rc.save_state(rc.default_state("group/project", ["alice"]), root)

    cfg, enabled, config = rc.load_runtime(root)
    assert enabled is True
    assert config["backend"]["provider"] == "gitlab"
    assert cfg["repo"] == "group/project"
    assert cfg["allowlist"] == ["alice"]
    assert cfg["provider"] == "gitlab"
    assert cfg["ignore_labels"] == ["draft", "park"]


def test_load_runtime_missing_files_uses_empty_defaults(tmp_path):
    root = _specseed_root(tmp_path)

    cfg, enabled, config = rc.load_runtime(root)
    assert enabled is False
    assert config is None
    assert cfg["repo"] is None
    assert cfg["allowlist"] == []
    assert cfg["provider"] is None
    assert "draft" in cfg["ignore_labels"]


def test_now_iso_is_utc_timestamp():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", rc.now_iso())


def test_remote_constructs_and_normalizes_without_network():
    gh = rc.Remote({"provider": "github", "repo": "owner/repo"})
    assert gh.provider == "github"
    assert gh.repo == "owner/repo"
    assert gh.gh is True
    assert gh._norm({
        "number": 7, "title": "Bug", "body": None, "state": "open",
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "alice"}],
    }) == {
        "number": 7, "title": "Bug", "body": "", "state": "open",
        "labels": ["bug"], "assignees": ["alice"],
        "raw": {
            "number": 7, "title": "Bug", "body": None, "state": "open",
            "labels": [{"name": "bug"}],
            "assignees": [{"login": "alice"}],
        },
    }

    gl = rc.Remote({"provider": "gitlab", "repo": "group/project"})
    assert gl.provider == "gitlab"
    assert gl.gh is False
    norm = gl._norm({
        "iid": 8, "title": "Task", "description": None, "state": "opened",
        "labels": ["bug"], "assignees": [{"username": "bob"}],
    })
    assert norm["number"] == 8
    assert norm["body"] == ""
    assert norm["state"] == "open"
    assert norm["labels"] == ["bug"]
    assert norm["assignees"] == ["bob"]
