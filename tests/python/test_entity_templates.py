"""entity_templates.py - canonical and host-projected template files."""

import json

from conftest import run_core, write

import entity_templates as et


def _root(tmp_path, backend=None):
    (tmp_path / ".specseed" / "memory").mkdir(parents=True)
    if backend is not None:
        write(tmp_path / ".specseed" / "memory" / "config.json",
              json.dumps({"backend": backend}))
    return tmp_path


def test_sync_writes_canonical_templates_only_by_default(tmp_path):
    root = _root(tmp_path)
    results = et.sync(root)

    assert {r["name"] for r in results} == set(et.ALL_TEMPLATES)
    assert all(r["kind"] == "canonical" for r in results)
    for name in et.ALL_TEMPLATES:
        assert (root / ".specseed" / "entity_templates" / f"{name}.md").exists()
    assert not (root / ".github").exists()
    assert not (root / ".gitlab").exists()


def test_sync_projects_user_facing_templates_to_github_when_enabled(tmp_path):
    root = _root(tmp_path, {
        "enabled": True,
        "provider": "github",
        "entity_templates": {"enabled": True},
    })

    results = et.sync(root)

    projected = [r for r in results if r["kind"] == "github"]
    assert {r["name"] for r in projected} == set(et.USER_FACING)
    assert (root / ".github" / "ISSUE_TEMPLATE" / "bug.md").exists()
    assert not (root / ".github" / "ISSUE_TEMPLATE" / "ticket.md").exists()


def test_sync_projects_user_facing_templates_to_gitlab_when_enabled(tmp_path):
    root = _root(tmp_path, {
        "enabled": True,
        "provider": "gitlab",
        "entity_templates": {"enabled": True},
    })

    et.sync(root)

    assert (root / ".gitlab" / "issue_templates" / "feature.md").exists()
    assert not (root / ".gitlab" / "issue_templates" / "epic.md").exists()


def test_sync_keeps_existing_files_unless_forced(tmp_path):
    root = _root(tmp_path)
    custom = root / ".specseed" / "entity_templates" / "bug.md"
    write(custom, "custom bug\n")

    et.sync(root)
    assert custom.read_text(encoding="utf-8") == "custom bug\n"

    et.sync(root, force=True)
    assert custom.read_text(encoding="utf-8").startswith("# Bug Report")


def test_cli_sync_from_config_json(tmp_path):
    root = _root(tmp_path, {
        "enabled": True,
        "provider": "github",
        "entity_templates": {"enabled": True},
    })

    r = run_core("entity_templates.py", "sync", "--json", cwd=root)

    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert any(x["kind"] == "github" and x["name"] == "change-request"
               for x in payload)
