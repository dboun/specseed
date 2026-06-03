"""
Shared pytest fixtures for the specseed script tests.

These tests cover the stdlib PLUMBING + analysis-seam scripts only — config,
assemble/validate, claim ordering, review_gate, add_work, renders. They NEVER
invoke an agent and consume NO tokens (so agents_runner.py's `claude` shell-out is
deliberately untested here). Everything runs against tiny on-disk `.specseed/`
fixtures built under pytest's tmp_path.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# repo_root/tests/unit/python/conftest.py → repo_root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "skills" / "specseed" / "scripts"
CORE = SCRIPTS / "core"

# Make the core modules importable as plain names (config, claim_issue, …).
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(SCRIPTS))


def run_core(script, *args, cwd):
    """Run a core script via subprocess; return CompletedProcess (text captured)."""
    return subprocess.run(
        [sys.executable, str(CORE / script), *map(str, args)],
        cwd=str(cwd), capture_output=True, text=True,
    )


def run_scripts(script, *args, cwd):
    """Run a top-level script (e.g. add_work.py) via subprocess."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        cwd=str(cwd), capture_output=True, text=True,
    )


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def fm_issue(iid, ticket, **over):
    f = {
        "id": iid, "title": iid, "ticket": ticket, "type": "feature",
        "component": "api", "effort_hours": 0.5, "depends_on": [],
        "status": "todo", "claimed_at": "null", "claimed_by": "null",
    }
    f.update(over)
    lines = ["---"]
    for k, v in f.items():
        lines.append(f"{k}: {v}")
    lines += ['artifacts: {"touches": [], "tests": [], "migrations": []}',
              "---", "## Acceptance criteria", "- x", ""]
    return "\n".join(lines)


class Repo:
    def __init__(self, root):
        self.root = root
        self.pm = root / ".specseed" / "project_management"
        self.spec = root / ".specseed" / "spec"
        self.mem = root / ".specseed" / "memory"

    def core(self, script, *args):
        return run_core(script, *args, cwd=self.root)

    def assemble(self):
        for s in ("issues_assemble.py", "tickets_assemble.py", "sprints_assemble.py"):
            if s == "sprints_assemble.py" and not (self.pm / "sprints").exists():
                continue
            r = self.core(s)
            assert r.returncode == 0, f"{s} failed: {r.stderr}"

    def validate(self):
        results = {}
        for s in ("issues_validate.py", "tickets_validate.py", "sprints_validate.py"):
            if s == "sprints_validate.py" and not (self.pm / "sprints").exists():
                continue
            results[s] = self.core(s)
        return results

    def issues(self):
        return json.loads((self.pm / "issues.json").read_text())


@pytest.fixture
def repo(tmp_path):
    """A minimal valid .specseed tree: 1 epic-less ticket + 1 issue + reqs."""
    r = Repo(tmp_path)
    write(r.spec / "reqs.json", json.dumps({"SRS-001": {"text": "do a thing"}}))
    write(r.pm / "tickets" / "PROJ-0001" / "PROJ-0001.md",
          "---\nid: PROJ-0001\ntitle: Base\nepic: null\ntype: feature\n"
          "priority: medium\nstatus: todo\ndepends_on: []\n"
          'satisfies_reqs: ["SRS-001"]\nissues: ["FEAT-0001"]\nsprint: null\n'
          "---\n## Story\nx\n")
    write(r.pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
          fm_issue("FEAT-0001", "PROJ-0001"))
    return r


@pytest.fixture
def repo_with_sprint(tmp_path):
    """Like `repo` but the ticket sits in an in_progress sprint (assembled)."""
    r = Repo(tmp_path)
    write(r.spec / "reqs.json", json.dumps({"SRS-001": {"text": "do a thing"}}))
    write(r.pm / "tickets" / "PROJ-0001" / "PROJ-0001.md",
          "---\nid: PROJ-0001\ntitle: Base\nepic: null\ntype: feature\n"
          "priority: medium\nstatus: todo\ndepends_on: []\n"
          'satisfies_reqs: ["SRS-001"]\nissues: ["FEAT-0001"]\n'
          "sprint: SPRINT_2026_W01_A\n---\n## Story\nx\n")
    write(r.pm / "issues" / "FEAT-0001" / "FEAT-0001.md",
          fm_issue("FEAT-0001", "PROJ-0001"))
    write(r.pm / "sprints" / "SPRINT_2026_W01_A" / "SPRINT_2026_W01_A.md",
          "---\nid: SPRINT_2026_W01_A\ntitle: Foundations\nstatus: in_progress\n"
          'tickets: ["PROJ-0001"]\n---\n## Goal\nx\n')
    r.assemble()
    return r
