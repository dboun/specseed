"""
drift_check.py [source_root]

Best-effort mechanical drift between spec and code. Advisory only — exits 0
regardless of warnings.

Checks:
  1. Ticket `artifacts.tests` paths missing on disk
  2. Settled spec docs (frontmatter `settled: true`) predating recent git
     commits to source paths referenced by tickets in their scope.
     Skipped if git unavailable.
  3. Test files on disk under `tests/` / `test/` / `__tests__/` not
     referenced by any ticket's `artifacts.tests`
  4. Python files with route-decorator patterns (e.g. `@x.get("/path")`)
     whose path string doesn't appear in any SRS req text. Heuristic.
     Python-only — disable with `--no-handler-check` if irrelevant
     (CLI tools, libraries, non-Python projects, etc.).

Flags:
  --no-git              skip check 2
  --quiet               print 'OK' if no warnings
  --no-handler-check    skip check 4

Exit: 0 (advisory only), 2 on missing input.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

STALE_COMMIT_THRESHOLD = 3   # commits since settle = "significant"

TEST_FILE_RE = re.compile(
    r"^(test_.+\.py|.+_test\.py|.+\.test\.[jt]sx?|.+\.spec\.[jt]sx?)$"
)

# Python route-decorator pattern. Matches @<obj>.<verb>("path", ...).
# Heuristic — covers common cases, not exhaustive.
ROUTE_RE = re.compile(
    r'@\w+\.(get|post|put|patch|delete|route)\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

SKIP_DIRS = {
    ".venv", "venv", "env", "node_modules", ".git", "__pycache__",
    ".pytest_cache", ".mypy_cache", "build", "dist", ".tox", "spec",
}


def git_available(repo_root):
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_root, capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def parse_frontmatter(text):
    """Minimal YAML-ish frontmatter. Returns dict or None."""
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return None
    start = 4 if text.startswith("---\n") else 5
    end = text.find("\n---", start)
    if end == -1:
        return None
    out = {}
    for line in text[start:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def parse_settled_at(s):
    """Accept YYYY-MM-DD or full ISO timestamp."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def infer_component(doc_path):
    """`api-srs.md` -> 'api'; `srs.md` / `sad.md` -> None (apply to all)."""
    stem = doc_path.stem
    m = re.match(r"^(.+)-(srs|sdd|sad)$", stem)
    return m.group(1) if m else None


# ---------- checks ----------

def check_missing_tests(tickets, repo_root, warnings):
    for tid, t in tickets.items():
        for path in (t.get("artifacts") or {}).get("tests", []) or []:
            if not (repo_root / path).exists():
                warnings.append({
                    "kind": "missing_test_file",
                    "ref": path,
                    "referenced_by": f"{tid} (artifacts.tests)",
                })


def check_settled_vs_git(spec_dir, tickets, repo_root, warnings):
    if not git_available(repo_root):
        return False

    # component -> touches paths
    comp_paths = {}
    all_paths = set()
    for t in tickets.values():
        comp = t.get("component")
        touches = (t.get("artifacts") or {}).get("touches", []) or []
        all_paths.update(touches)
        if comp:
            comp_paths.setdefault(comp, set()).update(touches)

    for doc in spec_dir.glob("*.md"):
        try:
            text = doc.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = parse_frontmatter(text) or {}
        if fm.get("settled", "").lower() != "true":
            continue
        settled_dt = parse_settled_at(fm.get("settled_at"))
        if not settled_dt:
            continue

        comp = infer_component(doc)
        paths = comp_paths.get(comp, all_paths) if comp else all_paths
        if not paths:
            continue

        since = (settled_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            r = subprocess.run(
                ["git", "log", f"--since={since}", "--oneline", "--", *paths],
                cwd=repo_root, capture_output=True, timeout=20, text=True,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

        commits = [l for l in r.stdout.splitlines() if l.strip()]
        if len(commits) >= STALE_COMMIT_THRESHOLD:
            warnings.append({
                "kind": "stale_settled_doc",
                "doc": str(doc),
                "settled_at": fm.get("settled_at"),
                "evidence": f"{len(commits)} commits to related paths since",
            })
    return True


def check_orphan_tests(repo_root, tickets, warnings):
    referenced = set()
    for t in tickets.values():
        for path in (t.get("artifacts") or {}).get("tests", []) or []:
            referenced.add(Path(path).as_posix())

    found = set()
    for d in ("tests", "test", "__tests__"):
        root = repo_root / d
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.is_file() and TEST_FILE_RE.match(f.name):
                try:
                    found.add(f.relative_to(repo_root).as_posix())
                except ValueError:
                    pass

    for path in sorted(found - referenced):
        warnings.append({
            "kind": "unreferenced_test_file",
            "ref": path,
            "detail": "test file on disk not referenced by any ticket",
        })


def check_orphan_handlers(repo_root, reqs, warnings):
    """Heuristic, Python-only. No-op cleanly on non-Python projects."""
    all_text = " ".join((r.get("text", "") for r in reqs.values())).lower()
    if not all_text:
        return

    for py in repo_root.rglob("*.py"):
        if any(p in SKIP_DIRS for p in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
            rel = py.relative_to(repo_root)
        except (OSError, ValueError):
            continue
        for m in ROUTE_RE.finditer(text):
            method = m.group(1).upper()
            route = m.group(2)
            route_lc = route.lower()
            stripped = route.lstrip("/").lower()
            if route_lc in all_text or (stripped and stripped in all_text):
                continue
            warnings.append({
                "kind": "unreferenced_handler",
                "ref": f"{rel.as_posix()} :: {method} {route}",
                "detail": "route path not mentioned in any SRS req text",
            })


# ---------- main ----------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("source_root", nargs="?", default=".")
    p.add_argument("--no-git", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--no-handler-check", action="store_true")
    args = p.parse_args()

    repo_root = Path(args.source_root).resolve()
    spec_dir = repo_root / ".specseed" / "spec"
    tickets_path = spec_dir / "tickets.json"
    reqs_path = spec_dir / "reqs.json"

    if not tickets_path.exists():
        print(f"ERROR: {tickets_path} not found", file=sys.stderr)
        sys.exit(2)
    if not reqs_path.exists():
        print(f"ERROR: {reqs_path} not found", file=sys.stderr)
        sys.exit(2)

    try:
        tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
        reqs = json.loads(reqs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed JSON: {e}", file=sys.stderr)
        sys.exit(2)

    warnings = []
    checks_run = 0
    git_used = False

    check_missing_tests(tickets, repo_root, warnings)
    checks_run += 1

    if not args.no_git:
        git_used = check_settled_vs_git(spec_dir, tickets, repo_root, warnings)
        if git_used:
            checks_run += 1

    check_orphan_tests(repo_root, tickets, warnings)
    checks_run += 1

    if not args.no_handler_check:
        check_orphan_handlers(repo_root, reqs, warnings)
        checks_run += 1

    if args.quiet and not warnings:
        print("OK")
        sys.exit(0)

    print(json.dumps({
        "ok": True,
        "errors": [],
        "warnings": warnings,
        "stats": {
            "checks_run": checks_run,
            "n_warnings": len(warnings),
            "git_available": git_used,
        },
    }, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
