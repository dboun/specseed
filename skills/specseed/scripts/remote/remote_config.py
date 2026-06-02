"""
remote_config.py — per-repo mirror STATE I/O + a provider-agnostic adapter for
the optional remote-mirror workflow (see references/remote.md).

`.specseed/memory/remote.json` holds ONLY project-specific state (the things you
must NOT copy to another repo): the target `repo`, the command `allowlist`, the
specseed-id <-> issue-number `map`, poll cursors, and the `permanent` dashboard
issue numbers. The PORTABLE backend choice (enabled + provider) and runner knobs
live in `.specseed/memory/config.json` (see scripts/core/config.py) — copy THAT
between repos, never remote.json.

Three jobs:
  1. Locate `.specseed/`, load/save `remote.json` (state only — save filters to
     STATE_KEYS so injected runtime fields like `provider` never leak in).
  2. `load_runtime()` — merge config.json's backend `provider` onto the state dict
     so the mirror engine can work from a single `cfg`.
  3. Expose a uniform `Remote` adapter over github_functions / gitlab_functions so
     remote_sync.py / remote_control.py don't branch on provider.

Stdlib only. The PAT is NOT stored here — it stays in the env / `.env` that the
function wrappers already read (GITHUB_PAT / GITLAB_PAT).

CLI (debug):
  python remote_config.py show              # print resolved state
  python remote_config.py ping              # auth check via the provider
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CORE_DIR = SCRIPT_DIR.parent / "core"

# The only keys persisted to remote.json. Anything else on a runtime cfg (e.g. the
# `provider` injected from config.json, or `retry_delay_minutes`) is dropped on save.
STATE_KEYS = ("repo", "allowlist", "permanent", "map",
              "cli_cursor", "cli_cursor_ids", "pull_cursor", "labels_seeded",
              "initialized")

STATUS_STATES = ["todo", "in_progress", "blocked", "in_review",
                 "awaiting_approval", "done", "wont_do", "deprecated"]
TIER_LABELS = ["tier:epic", "tier:ticket", "tier:issue"]
TERMINAL = {"done", "wont_do", "deprecated"}

# label colors (hex, no #): state palette + tier palette
LABEL_COLORS = {
    "status:todo": "ededed", "status:in_progress": "1d76db",
    "status:blocked": "b60205", "status:in_review": "fbca04",
    "status:awaiting_approval": "d93f0b", "status:done": "0e8a16",
    "status:wont_do": "555555", "status:deprecated": "555555",
    "tier:epic": "5319e7", "tier:ticket": "0052cc", "tier:issue": "006b75",
    "draft": "cfd3d7", "ignore": "555555", "specseed:ignore": "555555",
    "changes-requested": "d93f0b", "needs-more-info": "fbca04",
    "needs-triage": "bfe5bf",
    # change-request intake + status labels (CRs are not work entities)
    "change-request": "8250df", "cr:open": "1d76db",
    "cr:done": "0e8a16", "cr:rejected": "555555",
}


# --------------------------------------------------------------------------- #
# locate .specseed / config
# --------------------------------------------------------------------------- #
def find_root(start=None):
    """Repo root containing a `.specseed/` dir, searching upward from `start`."""
    start = Path(start or Path.cwd()).resolve()
    for parent in (start, *start.parents):
        if (parent / ".specseed").is_dir():
            return parent
    raise FileNotFoundError("no .specseed/ found from " + str(start))


def state_path(root=None):
    return find_root(root) / ".specseed" / "memory" / "remote.json"


def load_state(root=None):
    p = state_path(root)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(cfg, root=None):
    """Persist ONLY the canonical state keys (drops any injected runtime fields)."""
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    state = {k: cfg[k] for k in STATE_KEYS if k in cfg}
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return p


def default_state(repo=None, allowlist=None):
    return {
        "repo": repo,
        "allowlist": allowlist or [],   # github/gitlab usernames whose command/inbox comments run; [] = owner-only
        "permanent": {"roadmap": None, "timeline": None, "control": None, "sprint": None},
        "map": {}, "cli_cursor": None, "cli_cursor_ids": [], "pull_cursor": None,
        "labels_seeded": False, "initialized": False,
    }


def _load_config_mod():
    spec = importlib.util.spec_from_file_location("config", CORE_DIR / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_runtime(root=None):
    """Build the single mirror runtime cfg the engine works from: per-repo state
    (remote.json) ∪ the portable backend `provider` (config.json). Returns
    (cfg, enabled, config) where `enabled` is config.backend.enabled and `config`
    is the full portable config (None if config.json is missing)."""
    config_mod = _load_config_mod()
    config = config_mod.load_config(root)
    backend = (config or {}).get("backend") or {}
    enabled = bool(backend.get("enabled"))
    state = load_state(root) or default_state()
    backend_full = config_mod.backend_config(config or {})
    cfg = {**state, "provider": backend.get("provider"),
           "ignore_labels": list(backend_full.get("ignore_labels") or [])}
    return cfg, enabled, config


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_provider(provider):
    name = {"github": "github_functions", "gitlab": "gitlab_functions"}.get(provider)
    if not name:
        raise ValueError(f"unknown provider {provider!r}")
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# uniform adapter
# --------------------------------------------------------------------------- #
class Remote:
    """Provider-agnostic surface. Normalizes github_functions / gitlab_functions
    into one vocabulary: number, title, body, state(open|closed), labels[str],
    assignees[str]."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.provider = cfg["provider"]
        self.repo = cfg["repo"]
        self.gh = self.provider == "github"
        self.m = _load_provider(self.provider)

    # -- normalization ------------------------------------------------------- #
    def _norm(self, raw):
        if raw is None:
            return None
        if self.gh:
            return {
                "number": raw.get("number"), "title": raw.get("title"),
                "body": raw.get("body") or "",
                "state": raw.get("state"),  # open | closed
                "labels": [l["name"] for l in (raw.get("labels") or [])
                           if isinstance(l, dict)],
                "assignees": [a["login"] for a in (raw.get("assignees") or [])],
                "raw": raw,
            }
        return {
            "number": raw.get("iid"), "title": raw.get("title"),
            "body": raw.get("description") or "",
            "state": "open" if raw.get("state") == "opened" else "closed",
            "labels": list(raw.get("labels") or []),
            "assignees": [a["username"] for a in (raw.get("assignees") or [])],
            "raw": raw,
        }

    # -- issues -------------------------------------------------------------- #
    def create_issue(self, title, body=None, labels=None, assignees=None):
        if self.gh:
            r = self.m.create_github_issue(title, body=body, labels=labels,
                                           assignees=assignees, repo=self.repo)
        else:
            r = self.m.create_gitlab_issue(title, description=body, labels=labels,
                                           assignee_usernames=assignees, repo=self.repo)
        return self._norm(r)

    def get_issue(self, number):
        try:
            if self.gh:
                return self._norm(self.m.get_github_issue(number, repo=self.repo))
            return self._norm(self.m.get_gitlab_issue(number, repo=self.repo))
        except Exception as e:
            if "404" in str(e):
                return None
            raise

    def update_issue(self, number, title=None, body=None, labels=None, assignees=None):
        if self.gh:
            return self._norm(self.m.update_github_issue(
                number, title=title, body=body, labels=labels,
                assignees=assignees, repo=self.repo))
        return self._norm(self.m.update_gitlab_issue(
            number, title=title, description=body, labels=labels,
            assignee_usernames=assignees, repo=self.repo))

    def close_issue(self, number, planned=True):
        if self.gh:
            return self.m.close_github_issue(
                number, state_reason="completed" if planned else "not_planned",
                repo=self.repo)
        return self.m.close_gitlab_issue(number, repo=self.repo)

    def reopen_issue(self, number):
        if self.gh:
            return self.m.reopen_github_issue(number, repo=self.repo)
        return self.m.reopen_gitlab_issue(number, repo=self.repo)

    def set_labels(self, number, labels):
        if self.gh:
            return self.m.set_labels_on_github_issue(number, labels, repo=self.repo)
        return self.m.set_labels_on_gitlab_issue(number, labels, repo=self.repo)

    def comment(self, number, body):
        if self.gh:
            return self.m.add_comment_to_github_issue(number, body, repo=self.repo)
        return self.m.add_comment_to_gitlab_issue(number, body, repo=self.repo)

    def pin(self, number):
        if self.gh:
            return self.m.pin_github_issue(number, repo=self.repo)
        return {"ok": True, "skipped": "gitlab has no issue pinning"}

    def ensure_label(self, name, color=None):
        try:
            return self.m.create_label(name, color=color or LABEL_COLORS.get(name) or "808080",
                                       repo=self.repo)
        except Exception as e:
            if "already exists" in str(e).lower() or "409" in str(e):
                return {"ok": True, "exists": True}
            raise

    # -- polling ------------------------------------------------------------- #
    def list_open_issues(self):
        """All open issues, normalized; PRs/MRs excluded."""
        if self.gh:
            items = self.m.list_github_issues(state="open", repo=self.repo)
        else:
            items = self.m.list_gitlab_issues(state="opened", repo=self.repo)
        return [self._norm(i) for i in items]

    def comments_since(self, since_iso):
        """Issue comments since `since_iso`, normalized to
        {issue_number, author, body, created_at, id}."""
        out = []
        if self.gh:
            for c in self.m.list_repo_issue_comments(since=since_iso, repo=self.repo):
                url = c.get("issue_url", "")
                num = int(url.rsplit("/", 1)[-1]) if url.rsplit("/", 1)[-1].isdigit() else None
                out.append({"issue_number": num,
                            "author": (c.get("user") or {}).get("login"),
                            "body": c.get("body") or "", "id": c.get("id"),
                            "created_at": c.get("created_at")})
        else:
            after = (since_iso or "")[:10] or None  # GitLab events filter is date-only
            # GitLab's events filter wants lowercase enum values; a comment is a `note`
            # event (capitalized "Issue" is rejected with a 400). The issue the note
            # belongs to is the note's `noteable_iid`, not the event's `target_iid`.
            for e in self.m.list_project_events(action="commented", after=after,
                                                target_type="note", repo=self.repo):
                note = e.get("note") or {}
                if note.get("noteable_type") != "Issue":
                    continue
                out.append({"issue_number": note.get("noteable_iid"),
                            "author": (e.get("author") or {}).get("username"),
                            "body": note.get("body") or "", "id": note.get("id"),
                            "created_at": e.get("created_at")})
            if since_iso:  # tighten the date-only filter to the real timestamp
                out = [c for c in out if (c["created_at"] or "") >= since_iso]
        return out

    def whoami(self):
        return self.m.get_authenticated_user()


# --------------------------------------------------------------------------- #
# CLI (debug)
# --------------------------------------------------------------------------- #
def main(argv):
    cmd = argv[0] if argv else "show"
    cfg, enabled, _ = load_runtime()
    if cmd == "show":
        print(json.dumps(cfg, indent=2))
        print(f"# backend.enabled = {enabled}")
        return 0
    if cmd == "ping":
        if not enabled:
            print("mirror not enabled (backend.enabled=false in config.json)", file=sys.stderr); return 1
        print(json.dumps(Remote(cfg).whoami(), indent=2)); return 0
    print(f"unknown: {cmd}  (show | ping)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
