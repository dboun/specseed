"""
entity_templates.py - write specseed entity templates.

Canonical templates live in the target repo at `.specseed/entity_templates/` so
agents can use them when filing/scaffolding work. If the configured backend is a
GitHub or GitLab mirror and the user opted in, this also projects only the
user-facing templates into the host's conventional issue-template directory:

  GitHub: .github/ISSUE_TEMPLATE/*.md
  GitLab: .gitlab/issue_templates/*.md

No network. No agent calls. Existing files are left alone unless `--force`.

CLI:
  python .specseed/scripts/core/entity_templates.py sync
  python .specseed/scripts/core/entity_templates.py sync --provider github --publish
  python .specseed/scripts/core/entity_templates.py sync --force --json
"""

import argparse
import json
import sys
from pathlib import Path


ALL_TEMPLATES = ("epic", "ticket", "issue", "bug", "feature", "change-request")
USER_FACING = ("bug", "feature", "change-request")
PROVIDERS = ("github", "gitlab")


FALLBACK_TEMPLATES = {
    "epic": """# Epic

## Goal

## Outcome

## Why

## Candidate Tickets
""",
    "ticket": """# Ticket

## Story

## Description

## Acceptance Criteria

## Satisfies Requirements

## Dependencies
""",
    "issue": """# Issue

## Technical Goal

## Acceptance Criteria

## Plan Notes

## Artifacts
""",
    "bug": """# Bug Report

## What happened

## Expected behavior

## Steps to reproduce

## Impact

## Extra context
""",
    "feature": """# Feature Request

## What should change

## Why it matters

## Acceptance criteria

## Constraints
""",
    "change-request": """# Spec Change Request

## Requested spec change

## Why now

## Scope

## Risks or constraints
""",
}


def find_root(start=None):
    start = Path(start or Path.cwd()).resolve()
    for parent in (start, *start.parents):
        if (parent / ".specseed").is_dir():
            return parent
    raise FileNotFoundError("no .specseed/ found from " + str(start))


def _skill_template_dir():
    return Path(__file__).resolve().parents[2] / "templates" / "entity_templates"


def template_text(name):
    """Template text from shipped template files when present; embedded fallback
    keeps the script usable after it is copied into a target `.specseed/scripts/`.
    """
    p = _skill_template_dir() / f"{name}.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return FALLBACK_TEMPLATES[name].rstrip() + "\n"


def canonical_path(root, name):
    return root / ".specseed" / "entity_templates" / f"{name}.md"


def host_path(root, provider, name):
    if provider == "github":
        return root / ".github" / "ISSUE_TEMPLATE" / f"{name}.md"
    if provider == "gitlab":
        return root / ".gitlab" / "issue_templates" / f"{name}.md"
    raise ValueError(f"unknown provider: {provider!r}")


def _write_if_needed(path, text, force=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return "kept"
    existed = path.exists()
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return "updated" if existed else "created"


def _load_backend(root):
    p = root / ".specseed" / "memory" / "config.json"
    if not p.exists():
        return {"enabled": False, "provider": None,
                "entity_templates": {"enabled": False}}
    cfg = json.loads(p.read_text(encoding="utf-8"))
    backend = cfg.get("backend") or {}
    et = backend.get("entity_templates") or {}
    return {
        "enabled": bool(backend.get("enabled", False)),
        "provider": backend.get("provider"),
        "entity_templates": {"enabled": bool(et.get("enabled", False))},
    }


def sync(root=None, provider=None, publish=None, force=False):
    root = find_root(root)
    backend = _load_backend(root)
    if provider is None:
        provider = backend.get("provider") if backend.get("enabled") else None
    if publish is None:
        publish = bool(backend.get("enabled") and backend.get("provider") and
                       backend.get("entity_templates", {}).get("enabled"))
    if provider is not None and provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")

    results = []
    for name in ALL_TEMPLATES:
        path = canonical_path(root, name)
        status = _write_if_needed(path, template_text(name), force=force)
        results.append({"kind": "canonical", "name": name, "path": str(path),
                        "status": status})

    if publish:
        if provider not in PROVIDERS:
            raise ValueError("publish requires provider github or gitlab")
        for name in USER_FACING:
            src = canonical_path(root, name).read_text(encoding="utf-8")
            path = host_path(root, provider, name)
            status = _write_if_needed(path, src, force=force)
            results.append({"kind": provider, "name": name, "path": str(path),
                            "status": status})
    return results


def main(argv=None):
    argv = list(argv or [])
    cmd = argv[0] if argv else "sync"
    if cmd != "sync":
        print("usage: entity_templates.py sync [--root PATH] [--provider github|gitlab] "
              "[--publish] [--no-publish] [--force] [--json]", file=sys.stderr)
        return 2
    p = argparse.ArgumentParser()
    p.add_argument("cmd", nargs="?")
    p.add_argument("--root", default=None)
    p.add_argument("--provider", choices=PROVIDERS)
    pub = p.add_mutually_exclusive_group()
    pub.add_argument("--publish", action="store_true")
    pub.add_argument("--no-publish", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    publish = True if args.publish else False if args.no_publish else None
    try:
        results = sync(args.root, provider=args.provider, publish=publish,
                       force=args.force)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{r['status']}: {r['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
