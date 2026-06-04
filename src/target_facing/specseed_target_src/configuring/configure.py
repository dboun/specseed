#!/usr/bin/env python3
"""
configure.py — interactive technical setup for specseed, run INSIDE a target repo.

PURE PYTHON: stdlib only, NO agent calls, no tokens spent. A human runs this once
on a repo (and any time they want to change settings). It writes two files into the
co-located storage dir (`../../storage/` relative to this file, i.e.
`.specseed/storage/`):

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
import subprocess
import sys
from pathlib import Path

CONFIG_VERSION = 1
DEFAULT_POLL_INTERVAL = 45


# --------------------------------------------------------------------------- #
# paths — storage is `../../storage/` relative to THIS file (i.e. .specseed/storage/)
# --------------------------------------------------------------------------- #
def default_storage_dir():
    # configuring/ -> specseed_target_src/ -> .specseed/  + storage/
    return Path(__file__).resolve().parent.parent.parent / "storage"


def config_file(storage):
    return Path(storage) / "configuration.json"


def remote_file(storage):
    return Path(storage) / "remote.json"


def token_file(storage):
    return Path(storage) / "token_remote.txt"


def gitignore_file(storage):
    return Path(storage) / ".gitignore"


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
        "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
        "backend": {"enabled": False, "provider": None},
        "approvals": {
            # Remote approval commands are only accepted from these usernames.
            # Empty means no remote author is allowed to resolve approvals.
            "approver_usernames": [],
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
def _detect_origin(storage):
    """Best-effort: read origin URL from the repo we live inside."""
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(Path(storage).resolve().parent),
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
    backend = cfg["backend"]
    if backend.get("enabled"):
        L.append(f"backend: {backend['provider']} mirror — repo={remote.get('repo') or '?'}, "
                 f"token={'set' if token else 'MISSING'}")
    else:
        L.append("backend: local only")
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
def run_interactive(storage):
    existing = _load_json(config_file(storage))
    fresh = existing is None
    if existing is not None:
        cfg = default_config()
        cfg.update(existing)
        # backfill nested blocks so sections always have something to edit
        base = default_config()
        cfg.setdefault("backend", base["backend"])
        cfg.setdefault("approvals", base["approvals"])
        perms = cfg.setdefault("permissions", base["permissions"])
        perms.setdefault("git", base["permissions"]["git"])
        perms.setdefault("remote", base["permissions"]["remote"])
        print(f"Reconfiguring {config_file(storage)} (current values are the defaults).")
    else:
        cfg = default_config()
        print(f"Configuring a fresh repo (storage at {Path(storage).resolve()}).")

    remote = _load_json(remote_file(storage)) or default_remote_state()
    remote.pop("token", None)  # tokens never live in remote.json (legacy cleanup)
    print("Press Enter to accept the shown default at any prompt.")

    token = section_backend(cfg, remote, storage)
    section_git_permissions(cfg)
    section_remote_permissions(cfg)
    section_approvals(cfg)
    section_interval(cfg)

    print_summary(cfg, remote, token)
    if not ask_yn("\nWrite this config?", default=True):
        print("Aborted — nothing written.")
        return 1

    cp = _write_json(config_file(storage), cfg)
    print(f"\nWrote {cp}")
    if cfg["backend"].get("enabled"):
        rp = _write_json(remote_file(storage), remote)
        print(f"Wrote {rp}")
        if token:
            tp = write_token(storage, token)
            gp = ensure_gitignored(storage)
            print(f"Wrote {tp} (secret — added to {gp})")

    # TODO: Add explanation how to start things
    return 0


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
    return run_interactive(storage)


if __name__ == "__main__":
    sys.exit(main())
