#!/usr/bin/env python3
"""
configure.py — interactive technical setup for specseed, run INSIDE a target repo.

PURE PYTHON: stdlib only, NO agent calls, no tokens spent. A human runs this once
on a repo (and any time they want to change settings). It writes files into the
storage dir below the configured specseed directory:

  configuration.json   PORTABLE "how you work" — poll interval + the approval
                       switches (local git + remote actions). Copyable between repos.
  remote.json          PER-REPO mirror wiring — provider, repo, plus mirror state.
                       NOT portable.
  token_remote.txt     The raw remote access token, on its own. Secret — the script
                       appends it to storage/.gitignore so it never gets committed.

Remote is the source of truth in this build: the runner polls it on an interval
(default 45s) and reacts to what changed. So the remote repo + access token live
here, and every action the agent could take against git/remote is gated by an
explicit approval switch the human sets below.

Re-running on an already-configured repo loads your current values as the defaults;
blast Enter to keep everything.

Usage:
  python3 <...>/configuring/configure.py            # interactive (default)
  python3 <...>/configuring/configure.py --show      # print resolved config, exit
  python3 <...>/configuring/configure.py --storage PATH
                                                     # override the storage dir (testing)

Stdlib only. Human-run (not part of the agent loop).
"""

import argparse
import getpass
import json
import shlex
import subprocess
import sys
from pathlib import Path

CONFIG_VERSION = 1
DEFAULT_POLL_INTERVAL = 45
DEFAULT_SPECSEED_DIR = ".specseed"


# --------------------------------------------------------------------------- #
# paths — storage is `../../storage/` relative to THIS file when already installed
# under the target repo's specseed dir.
# --------------------------------------------------------------------------- #
def default_storage_dir():
    # configuring/ -> specseed_target_src/ -> specseed dir + storage/
    return Path(__file__).resolve().parent.parent.parent / "storage"


def repo_root_from_cwd():
    """Best-effort target repo root, falling back to the current directory."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except Exception:
        pass
    return Path.cwd().resolve()


def storage_for_specseed_dir(specseed_dir):
    return Path(specseed_dir) / "storage"


def specseed_dir_from_storage(storage):
    storage = Path(storage)
    return storage.parent if storage.name == "storage" else storage


def _relative_to_repo(path, repo_root):
    path = Path(path).resolve()
    repo_root = Path(repo_root).resolve()
    try:
        return path.relative_to(repo_root)
    except ValueError:
        return path


def config_file(storage):
    return Path(storage) / "configuration.json"


def remote_file(storage):
    return Path(storage) / "remote.json"


def token_file(storage):
    return Path(storage) / "token_remote.txt"


def gitignore_file(storage):
    return Path(storage) / ".gitignore"


def repo_gitignore_file(repo_root):
    return Path(repo_root) / ".gitignore"


def load_token(storage):
    try:
        token = token_file(storage).read_text(encoding="utf-8").strip()
        return token or None
    except OSError:
        return None


def write_token(storage, token):
    p = token_file(storage)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text((token or "") + "\n", encoding="utf-8")
    return p


def ensure_gitignored(storage, entry="token_remote.txt"):
    """Append `entry` to storage/.gitignore (creating it), unless already listed."""
    p = gitignore_file(storage)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    try:
        existing = p.read_text(encoding="utf-8")
    except OSError:
        pass
    if entry in (line.strip() for line in existing.splitlines()):
        return p
    sep = "" if existing == "" or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(f"{sep}{entry}\n")
    return p


def ensure_repo_gitignored(repo_root, specseed_rel):
    """Append the specseed dir to the repo .gitignore, unless already listed."""
    entry = specseed_rel.as_posix().strip("/")
    if not entry:
        return None
    entry = f"{entry}/"
    p = repo_gitignore_file(repo_root)
    existing = ""
    try:
        existing = p.read_text(encoding="utf-8")
    except OSError:
        pass
    normalized = {line.strip().strip("/") for line in existing.splitlines()}
    if entry.strip("/") in normalized:
        return p
    sep = "" if existing == "" or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(f"{sep}{entry}\n")
    return p


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# defaults — everything that can act is OFF until the human turns it on
# --------------------------------------------------------------------------- #
def default_config():
    return {
        "version": CONFIG_VERSION,
        "specseed_dir": DEFAULT_SPECSEED_DIR,
        "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
        # Which coding-agent CLI the runner shells out to. provider claude|codex.
        "runner": {"provider": "claude", "model": None, "effort": "medium"},
        "backend": {"enabled": False, "provider": None},
        "approvals": {
            # Remote approval commands are only accepted from these usernames.
            # Empty means no remote author is allowed to resolve approvals.
            "approver_usernames": [],
        },
        # Code-review loop. enabled also gates the in_review state on every issue.
        # A passing review (verdict approve + confidence >= threshold) closes the
        # issue (or routes to awaiting_approval when require_human_approval). A
        # failing review reimplements until max_attempts, then opens a draft
        # spec-change:adapt post for the human to discuss.
        "review": {
            "enabled": False,
            "confidence_threshold": 0.75,
            "max_attempts": 3,
            "require_human_approval": False,
        },
        "permissions": {
            # local git. creating local branches is ALWAYS allowed when git is on
            # (no switch); merges into protected branches each get their own switch.
            "git": {
                "enabled": True,
                "merge_to_dev": False,
                "merge_to_main": False,
            },
            # remote actions. only meaningful when backend.enabled. replying to
            # spec-change posts is ALWAYS allowed when post_issues is on (no switch).
            "remote": {
                "post_issues": False,     # issues/tickets/epics + their labels + comments
                "post_dashboards": False,  # ROADMAP, TIMELINE, current branch (needs post_issues)
                "post_control": False,     # the CONTROL channel post (needs post_issues)
                "push_branches": False,
                "push_main": False,
                "make_prs": False,
            },
        },
    }


def default_remote_state():
    return {
        "provider": None,
        "repo": None,
        "state": {
            # mirror cursors — remote is source of truth, polled on the interval.
            "last_poll": None,
            "map": {},
        },
    }


def load_config(storage):
    """Load configuration.json with defaults backfilled for missing keys."""
    existing = _load_json(config_file(storage))
    cfg = default_config()
    if not isinstance(existing, dict):
        return cfg

    for key, value in existing.items():
        if key not in ("backend", "approvals", "permissions", "runner", "review"):
            cfg[key] = value

    if isinstance(existing.get("runner"), dict):
        cfg["runner"].update(existing["runner"])
    if isinstance(existing.get("backend"), dict):
        cfg["backend"].update(existing["backend"])
    if isinstance(existing.get("approvals"), dict):
        cfg["approvals"].update(existing["approvals"])
    if isinstance(existing.get("review"), dict):
        cfg["review"].update(existing["review"])
    if isinstance(existing.get("permissions"), dict):
        permissions = existing["permissions"]
        if isinstance(permissions.get("git"), dict):
            cfg["permissions"]["git"].update(permissions["git"])
        if isinstance(permissions.get("remote"), dict):
            cfg["permissions"]["remote"].update(permissions["remote"])
    return cfg


def load_remote_state(storage):
    """Load remote.json with defaults backfilled and legacy token stripped."""
    existing = _load_json(remote_file(storage))
    remote = default_remote_state()
    if isinstance(existing, dict):
        for key, value in existing.items():
            if key != "state":
                remote[key] = value
        if isinstance(existing.get("state"), dict):
            remote["state"].update(existing["state"])
    remote.pop("token", None)
    return remote


def write_config_files(
    storage,
    cfg,
    remote,
    token=None,
    repo_root=None,
    specseed_rel=None,
    ignore_specseed=False,
):
    """Persist config/remote/token files using configure.py's file policy."""
    written = {}
    written["config"] = _write_json(config_file(storage), cfg)
    if ignore_specseed and repo_root is not None and specseed_rel is not None:
        gitignore = ensure_repo_gitignored(repo_root, Path(specseed_rel))
        if gitignore is not None:
            written["repo_gitignore"] = gitignore
    if cfg.get("backend", {}).get("enabled"):
        written["remote"] = _write_json(remote_file(storage), remote)
        if token:
            written["token"] = write_token(storage, token)
            written["storage_gitignore"] = ensure_gitignored(storage)
    return written


# --------------------------------------------------------------------------- #
# interactive prompt primitives. EOF (e.g. piped/empty stdin) = accept default.
# --------------------------------------------------------------------------- #
def _input(prompt):
    try:
        return input(prompt)
    except EOFError:
        print("")
        raise


def ask_yn(prompt, default=True):
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            ans = _input(prompt + suffix).strip().lower()
        except EOFError:
            return default
        if ans == "":
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("  please answer y or n.")


def ask_str(prompt, default=""):
    shown = f" [{default}] " if default else " "
    try:
        ans = _input(prompt + shown).strip()
    except EOFError:
        return default
    return ans or default


def ask_int(prompt, default):
    while True:
        try:
            ans = _input(prompt + f" [{default}] ").strip()
        except EOFError:
            return default
        if ans == "":
            return default
        try:
            return int(ans)
        except ValueError:
            print("  please enter a whole number.")


def ask_choice(prompt, choices, default):
    opts = "/".join(choices)
    while True:
        try:
            ans = _input(f"{prompt} ({opts}) [{default}] ").strip().lower()
        except EOFError:
            return default
        if ans == "":
            return default
        if ans in choices:
            return ans
        print(f"  please choose one of: {opts}")


def ask_csv(prompt, default=None):
    default = list(default or [])
    shown = f" [{', '.join(default)}] " if default else " "
    try:
        ans = _input(prompt + shown).strip()
    except EOFError:
        return default
    if ans == "":
        return default
    return [part.strip() for part in ans.replace(",", " ").split() if part.strip()]


def ask_secret(prompt, default=None):
    """Read a secret without echoing. Enter keeps the existing value (if any)."""
    hint = " [keep existing] " if default else " "
    try:
        ans = getpass.getpass(prompt + hint).strip()
    except (EOFError, KeyboardInterrupt):
        print("")
        return default
    return ans or default


# --------------------------------------------------------------------------- #
# token requirement blurbs (printed before we ask for the token)
# --------------------------------------------------------------------------- #
GITHUB_TOKEN_HELP = """\
  GitHub — create a fine-grained personal access token scoped to this repo with:
    Metadata        Read   (forced — always required)
    Issues          Read and write
    Pull requests   Read and write
    Contents        Read and write
"""

GITLAB_TOKEN_HELP = """\
  GitLab — create a personal/project access token with:
    Scope           api
    Role            Developer (minimum)
  Note: a Developer typically CANNOT push to a protected main/master. If you plan to
  let the agent push to main/master, grant Maintainer instead (or relax branch
  protection). Otherwise Developer is enough.
"""


# --------------------------------------------------------------------------- #
# interactive sections — each mutates cfg / remote in place
# --------------------------------------------------------------------------- #
def section_specseed_dir(cfg, storage, explicit_storage=False):
    """Choose the repo-relative specseed dir and whether to ignore it."""
    repo_root = repo_root_from_cwd()
    current_dir = specseed_dir_from_storage(storage)
    configured = cfg.get("specseed_dir")
    if configured:
        default_rel = Path(configured)
    elif explicit_storage:
        default_rel = _relative_to_repo(current_dir, repo_root)
    else:
        default_rel = Path(DEFAULT_SPECSEED_DIR)

    print("\n--- specseed directory ---")
    print(f"Target repo: {repo_root}")
    specseed_rel = Path(ask_str(
        "Specseed directory under the target repo",
        default_rel.as_posix(),
    ))
    if specseed_rel.is_absolute():
        try:
            specseed_rel = specseed_rel.resolve().relative_to(repo_root)
        except ValueError:
            print("  absolute path is outside the target repo; keeping the current storage dir.")
            specseed_rel = _relative_to_repo(current_dir, repo_root)
    specseed_value = specseed_rel.as_posix().strip("/")
    specseed_rel = Path(specseed_value) if specseed_value else Path(DEFAULT_SPECSEED_DIR)

    cfg["specseed_dir"] = specseed_rel.as_posix()
    next_storage = storage if explicit_storage else storage_for_specseed_dir(repo_root / specseed_rel)
    ignore = ask_yn(
        f"Append {specseed_rel.as_posix()}/ to {repo_gitignore_file(repo_root)}?",
        default=True,
    )
    return next_storage, repo_root, specseed_rel, ignore


def _detect_origin(storage):
    """Best-effort: read origin URL from the repo we live inside."""
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(Path(storage).resolve().parent.parent),
            capture_output=True,
            text=True,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def section_backend(cfg, remote, storage):
    """Set backend on/off + provider + repo in `remote`. Returns the access token
    (or None for local-only); the caller persists it to token_remote.txt."""
    backend = cfg["backend"]
    local = ask_yn(
        "Track work locally only (no GitHub/GitLab remote)?",
        default=not backend.get("enabled"),
    )
    if local:
        backend["enabled"] = False
        backend["provider"] = None
        remote["provider"] = None
        return None

    provider = ask_choice(
        "Mirror to which host?",
        ("github", "gitlab"),
        backend.get("provider") or "github",
    )
    backend["enabled"] = True
    backend["provider"] = provider
    remote["provider"] = provider

    detected = _detect_origin(storage)
    remote["repo"] = ask_str(
        "Repo (owner/name, URL, or self-hosted host/owner/name)",
        remote.get("repo") or detected or "",
    ) or None

    print("\nThe agent needs an access token to read/write the remote.")
    print(GITHUB_TOKEN_HELP if provider == "github" else GITLAB_TOKEN_HELP)
    token = ask_secret("  Paste access token:", load_token(storage))
    if not token:
        print("  (no token set — the remote stays unreachable until you add one.)")
    print("  NOTE: the token goes to token_remote.txt (gitignored automatically).\n")
    return token


def section_git_permissions(cfg):
    git = cfg["permissions"]["git"]
    print("\n--- local git ---")
    git["enabled"] = ask_yn(
        "Let the agent use local git (branch/commit/merge)?",
        default=git.get("enabled", True),
    )
    if not git["enabled"]:
        git["merge_to_dev"] = False
        git["merge_to_main"] = False
        return
    print("  (creating local branches is always allowed once git is on.)")
    git["merge_to_dev"] = ask_yn(
        "  Allow merging into the dev integration branch?",
        default=git.get("merge_to_dev", False),
    )
    git["merge_to_main"] = ask_yn(
        "  Allow merging into main/master?",
        default=git.get("merge_to_main", False),
    )


def section_remote_permissions(cfg):
    if not cfg["backend"].get("enabled"):
        return
    rem = cfg["permissions"]["remote"]
    print("\n--- remote actions ---")
    rem["post_issues"] = ask_yn(
        "Allow posting issues/tickets/epics (+ their labels + comments)?",
        default=rem.get("post_issues", False),
    )
    if rem["post_issues"]:
        rem["post_dashboards"] = ask_yn(
            "  Also post the dashboards (ROADMAP, TIMELINE, current branch)?",
            default=rem.get("post_dashboards", False),
        )
        rem["post_control"] = ask_yn(
            "  Also post the CONTROL channel?",
            default=rem.get("post_control", False),
        )
        print("  (replying to spec-change posts is always allowed while posting is on.)")
    else:
        rem["post_dashboards"] = False
        rem["post_control"] = False

    rem["push_branches"] = ask_yn(
        "Allow pushing branches to the remote?",
        default=rem.get("push_branches", False),
    )
    rem["push_main"] = ask_yn(
        "Allow pushing to main/master on the remote?",
        default=rem.get("push_main", False),
    )
    rem["make_prs"] = ask_yn(
        "Allow opening pull/merge requests?",
        default=rem.get("make_prs", False),
    )


def section_approvals(cfg):
    approvals = cfg.setdefault("approvals", {})
    print("\n--- approvals ---")
    approvals["approver_usernames"] = ask_csv(
        "Usernames allowed to approve HITL gates remotely (comma/space separated)",
        approvals.get("approver_usernames", []),
    )


def section_runner(cfg):
    runner = cfg.setdefault("runner", {"provider": "claude", "model": None, "effort": "medium"})
    print("\n--- coding agent ---")
    runner["provider"] = ask_choice(
        "Which coding-agent CLI should the runner drive?",
        ("claude", "codex"),
        runner.get("provider") or "claude",
    )
    default_model = runner.get("model") or ("gpt-5.4-mini" if runner["provider"] == "codex" else "claude-opus-4-8")
    runner["model"] = ask_str("  Model", default_model) or None
    if runner["provider"] == "codex":
        runner["effort"] = ask_choice(
            "  Reasoning effort",
            ("low", "medium", "high"),
            runner.get("effort") or "medium",
        )


def section_interval(cfg):
    print("\n--- runner ---")
    cfg["poll_interval_seconds"] = ask_int(
        "Poll the remote every how many seconds?",
        cfg.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL),
    )


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #
def summary_lines(cfg, remote, token):
    L = []
    L.append(f"specseed dir: {cfg.get('specseed_dir') or DEFAULT_SPECSEED_DIR}")
    backend = cfg["backend"]
    if backend.get("enabled"):
        L.append(f"backend: {backend['provider']} mirror — repo={remote.get('repo') or '?'}, "
                 f"token={'set' if token else 'MISSING'}")
    else:
        L.append("backend: local only")
    runner = cfg.get("runner") or {}
    L.append(f"agent: {runner.get('provider', 'claude')} "
             f"(model={runner.get('model') or 'default'}, effort={runner.get('effort', 'medium')})")
    L.append(f"poll interval: {cfg['poll_interval_seconds']}s")
    approvers = cfg.get("approvals", {}).get("approver_usernames", [])
    L.append("approvers: " + (", ".join(approvers) if approvers else "none configured"))
    git = cfg["permissions"]["git"]
    if not git["enabled"]:
        L.append("git: OFF (agent never touches git)")
    else:
        L.append(f"git: on — branches=always, merge_to_dev={git['merge_to_dev']}, "
                 f"merge_to_main={git['merge_to_main']}")
    if backend.get("enabled"):
        r = cfg["permissions"]["remote"]
        L.append(f"remote: post_issues={r['post_issues']} "
                 f"(dashboards={r['post_dashboards']}, control={r['post_control']}), "
                 f"push_branches={r['push_branches']}, push_main={r['push_main']}, "
                 f"make_prs={r['make_prs']}")
    return L


def print_summary(cfg, remote, token):
    print("\n--- config summary ---")
    for line in summary_lines(cfg, remote, token):
        print("  " + line)
    print("----------------------")


# --------------------------------------------------------------------------- #
# drivers
# --------------------------------------------------------------------------- #
def run_interactive(storage, explicit_storage=False):
    initial_cfg = load_config(storage)
    storage, repo_root, specseed_rel, ignore_specseed = section_specseed_dir(
        initial_cfg, storage, explicit_storage=explicit_storage
    )

    existing = _load_json(config_file(storage))
    if existing is not None:
        cfg = load_config(storage)
        print(f"Reconfiguring {config_file(storage)} (current values are the defaults).")
    else:
        cfg = default_config()
        cfg["specseed_dir"] = specseed_rel.as_posix()
        print(f"Configuring a fresh repo (storage at {Path(storage).resolve()}).")

    remote = load_remote_state(storage)
    print("Press Enter to accept the shown default at any prompt.")

    token = section_backend(cfg, remote, storage)
    section_runner(cfg)
    section_git_permissions(cfg)
    section_remote_permissions(cfg)
    section_approvals(cfg)
    section_interval(cfg)

    print_summary(cfg, remote, token)
    if not ask_yn("\nWrite this config?", default=True):
        print("Aborted — nothing written.")
        return 1

    written = write_config_files(
        storage,
        cfg,
        remote,
        token=token,
        repo_root=repo_root,
        specseed_rel=specseed_rel,
        ignore_specseed=ignore_specseed,
    )
    print(f"\nWrote {written['config']}")
    if "repo_gitignore" in written:
        print(f"Updated {written['repo_gitignore']} with {specseed_rel.as_posix()}/")
    if "remote" in written:
        print(f"Wrote {written['remote']}")
    if "token" in written:
        print(f"Wrote {written['token']} (secret — added to {written['storage_gitignore']})")

    _print_start_help(storage)
    return 0


def _print_start_help(storage):
    """Tell the human how to launch the scheduler now that config is written."""
    repo_root = repo_root_from_cwd()
    specseed_dir = _relative_to_repo(specseed_dir_from_storage(storage), repo_root)
    run_script = specseed_dir / "specseed_target_src" / "executing" / "run.py"
    print(
        "\nNext: start the scheduler from your repo root.\n"
        f"  python3 {shlex.quote(run_script.as_posix())}\n"
        "It polls the remote on your interval, syncs changes into the local mirror,\n"
        "and drains the work queue (running agents in a stoppable background thread).\n"
        "Useful flags:\n"
        "  --once               run a single poll+drain pass and exit (good for cron)\n"
        "  --interval SECONDS   override the poll interval\n"
        "  --storage PATH       use a non-default storage dir\n"
        "Control it live from the CONTROL post with: STATUS, START, PAUSE, STOP\n"
        "The first remote population also creates a draft `spec-change:adapt` post.\n"
        "Describe what you want there, remove the `draft` label, and save it.\n"
        "(only the approver usernames you configured may issue commands). Stop locally\n"
        "with Ctrl-C; the loop finishes the current step and exits cleanly."
    )


def run_show(storage):
    cfg = _load_json(config_file(storage)) or default_config()
    print(json.dumps(cfg, indent=2))
    remote = _load_json(remote_file(storage))
    if remote is not None:
        print(json.dumps(remote, indent=2))
    print(f'token_remote.txt: {"set" if load_token(storage) else "MISSING"}')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Interactive technical setup for specseed (run inside the target repo).")
    ap.add_argument("--storage", default=None,
                    help="override the storage dir (default: ../../storage relative to this file)")
    ap.add_argument("--show", action="store_true",
                    help="print the resolved config (token redacted) and exit")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    storage = Path(args.storage) if args.storage else default_storage_dir()
    if args.show:
        return run_show(storage)
    return run_interactive(storage, explicit_storage=args.storage is not None)


if __name__ == "__main__":
    sys.exit(main())
