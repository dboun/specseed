"""
remote_config.py — config/registry I/O + a provider-agnostic adapter for the
optional remote-mirror workflow (see references/remote.md).

Two jobs:
  1. Locate `.specseed/`, load/save `.specseed/memory/remote.json` (config + the
     specseed-id <-> github-issue-number map + poll cursors).
  2. Expose a uniform `Remote` adapter over github_functions / gitlab_functions so
     remote_sync.py / remote_cli.py don't branch on provider.

Stdlib only. The PAT is NOT stored here — it stays in the env / `.env` that the
function wrappers already read (GITHUB_PAT / GITLAB_PAT).

CLI (debug):
  python remote_config.py show              # print resolved config
  python remote_config.py ping              # auth check via the provider
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

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


def config_path(root=None):
    return find_root(root) / ".specseed" / "memory" / "remote.json"


def load_config(root=None):
    p = config_path(root)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_config(cfg, root=None):
    p = config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return p


def default_config(provider, repo, allowlist=None):
    return {
        "enabled": True, "provider": provider, "repo": repo,
        "allowlist": allowlist or [],
        "retry_delay_minutes": 30,   # after a failed claude run (e.g. session limit), wait this long before retrying
        "permanent": {"roadmap": None, "timeline": None, "control": None, "sprint": None},
        "map": {}, "cli_cursor": None, "pull_cursor": None, "labels_seeded": False,
    }


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
            return self.m.create_label(name, color=color or LABEL_COLORS.get(name),
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
            for e in self.m.list_project_events(action="commented", after=after,
                                                target_type="Issue", repo=self.repo):
                note = e.get("note") or {}
                out.append({"issue_number": e.get("target_iid"),
                            "author": (e.get("author") or {}).get("username"),
                            "body": note.get("body") or "", "id": note.get("id"),
                            "created_at": e.get("created_at")})
            if since_iso:  # tighten the date-only filter to the real timestamp
                out = [c for c in out if (c["created_at"] or "") > since_iso]
        return out

    def whoami(self):
        return self.m.get_authenticated_user()


# --------------------------------------------------------------------------- #
# CLI (debug)
# --------------------------------------------------------------------------- #
def main(argv):
    cmd = argv[0] if argv else "show"
    cfg = load_config()
    if cmd == "show":
        print(json.dumps(cfg, indent=2) if cfg else "no remote.json (mirror off)")
        return 0
    if cmd == "ping":
        if not cfg:
            print("mirror not configured", file=sys.stderr); return 1
        print(json.dumps(Remote(cfg).whoami(), indent=2)); return 0
    print(f"unknown: {cmd}  (show | ping)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
