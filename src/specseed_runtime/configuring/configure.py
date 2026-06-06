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

storage/ also carries the sqlite databases and version.txt (the storage version
marker). This script runs pending migrations (migrating/migrate.py) before it
reads or writes anything there.

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


def _add_package_parent_to_path():
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "specseed_runtime").is_dir():
            sys.path.insert(0, str(parent / "src"))
            return
        if (parent / "specseed_runtime").exists():
            sys.path.insert(0, str(parent))
            return


_add_package_parent_to_path()

from specseed_runtime.migrating.migrate import run_migrations
from specseed_runtime.storage_paths import default_storage_dir as _dev_default_storage_dir

# Single source of truth for the runner vocabulary - a mirrored copy here once
# drifted and hid a new function from configure + the UI.
from specseed_runtime.executing.agent_runner import (  # noqa: E402
    PROVIDER_DEFAULT_HOME,
    RUNNER_FUNCTIONS,
    default_runner_chains,
    default_runner_spec,
)

DEFAULT_POLL_INTERVAL = 45
DEFAULT_SPECSEED_DIR = ".specseed"
DEFAULT_DEV_BRANCH = "main"

RUNNER_PROVIDERS = ("claude", "codex")

# Action-class gate taxonomy the implementation agent honours. Mirrors
# executing/permissions.py (kept here so configure.py stays import-light / standalone).
AGENT_CATEGORIES = {
    "container":        "docker/podman build, run, push, pull",
    "heavy_compute":    "GPU / training / experiment scripts / long jobs",
    "network":          "outbound non-localhost calls (downloads, external APIs)",
    "deps":             "add/remove a dependency, or major-version bump",
    "data_destructive": "delete data, drop/rewrite schema, destructive migration",
    "external_publish": "deploy, submission, upload — anything leaving the repo",
    "outside_repo":     "writes outside the repo root",
    "secrets":          "reading/writing credentials or secret material",
}
AGENT_LEVELS = ("block", "surface", "auto", "require_human_approval")
DEFAULT_AGENT_GATES = {
    "container":        "block",
    "heavy_compute":    "block",
    "network":          "surface",
    "deps":             "block",
    "data_destructive": "block",
    "external_publish": "block",
    "outside_repo":     "block",
    "secrets":          "surface",
}


# --------------------------------------------------------------------------- #
# paths — a real config run passes --storage (<target>/<specseed_dir>/storage).
# The dev default is this code repo's own storage/ (see storage_paths.py).
# --------------------------------------------------------------------------- #
def default_storage_dir():
    return _dev_default_storage_dir()


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


def detect_default_branch(repo_root):
    """Best-effort dev branch: prefer `main`, fall back to `master`, else `main`.

    Reads local branches via git. A repo that already lives on `master` keeps it;
    everything else defaults to `main`.
    """
    try:
        out = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        if out.returncode == 0:
            heads = {line.strip() for line in out.stdout.splitlines() if line.strip()}
            if "main" in heads:
                return "main"
            if "master" in heads:
                return "master"
    except Exception:
        pass
    return DEFAULT_DEV_BRANCH


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


def _load_json_object(path, label):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return data


# --------------------------------------------------------------------------- #
# defaults — everything that can act is OFF until the human turns it on
# --------------------------------------------------------------------------- #
def default_agent_gates():
    return dict(DEFAULT_AGENT_GATES)


def default_config():
    return {
        "specseed_dir": DEFAULT_SPECSEED_DIR,
        # The integration branch work merges into. "dev branch" = where changes go;
        # default main (or master if that is the repo's branch).
        "dev_branch": DEFAULT_DEV_BRANCH,
        "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
        # The account the platform posts as on the tracker. Lets the runtime skip
        # its own comments (loop guard). Blank = rely on the "specseed: " body
        # prefix alone (bot and human share a username).
        "platform_username": "",
        # Per-function ordered fallback chains of agent specs. Each spec is
        # {provider, provider_data_dir, model, effort}; the first is primary, the
        # rest are tried on failure.
        "runner": default_runner_chains(),
        "approvals": {
            # Remote approval commands are only accepted from these usernames.
            # Empty means no remote author is allowed to resolve approvals.
            "approver_usernames": [],
        },
        # Failure recovery: failed tasks retry with backoff (1'/5'/15'...), each
        # failure surfaces as a platform_error post, and the resolve agent
        # investigates + converses there. max_retries caps the automatic retries.
        "recovery": {
            "enabled": True,
            "max_retries": 5,
        },
        # Code-review loop. enabled also gates the in_review state on every issue.
        # A passing review (verdict approve + confidence >= threshold) closes the
        # issue. The review step itself is not human-gated; pass/fail is the
        # confidence threshold. A failing review reimplements until max_attempts,
        # then opens a draft spec-change:adapt post for the human to discuss.
        "review": {
            "enabled": False,
            "confidence_threshold": 0.75,
            "max_attempts": 3,
        },
        "permissions": {
            # local git. creating local branches is ALWAYS allowed when git is on
            # (no switch); merging into the dev branch gets its own switch.
            "git": {
                "enabled": True,
                "merge_to_dev_branch": False,
            },
            # remote actions. only meaningful once the remote is enabled (remote.json).
            # posting issues/tickets/epics is ALWAYS allowed (the tracker lives on the
            # remote; no opt-out), so it is not a switch here.
            "remote": {
                "post_control": False,     # the CONTROL channel post
                "push_branches": False,
                "push_dev_branch": False,  # push to the dev branch on the remote
                "make_prs": False,
            },
            # platform-level autos. off = a human approves first.
            "platform": {
                "auto_implement_issue": True,
                "auto_proceed_to_next_sprint_if_available": False,
            },
            # action-class gates the implementation agent honours mid-work.
            "agents": default_agent_gates(),
        },
    }


def default_remote_state():
    return {
        # whether work mirrors to a real github/gitlab remote (vs the local stand-in).
        "enabled": False,
        "provider": None,
        "repo": None,
        "state": {
            # mirror cursors — remote is source of truth, polled on the interval.
            "last_poll": None,
            "map": {},
        },
    }


def _coerce_runner(existing):
    """Normalize a stored ``runner`` into the per-function chain shape.

    Handles three cases: the new ``{function: [specs]}`` shape (kept, known
    functions only), the legacy flat ``{provider, model, effort}`` single runner
    (migrated to a one-spec chain per function), and anything else (``None``).
    """
    if not isinstance(existing, dict):
        return None
    has_functions = any(fn in existing for fn in RUNNER_FUNCTIONS)
    is_legacy_flat = (not has_functions) and any(
        k in existing for k in ("provider", "model", "effort")
    )
    if is_legacy_flat:
        provider = (existing.get("provider") or "claude").lower()
        spec = {
            "provider": provider,
            "provider_data_dir": PROVIDER_DEFAULT_HOME.get(provider, "~/.claude"),
            "model": existing.get("model"),
            "effort": existing.get("effort") or "high",
        }
        return {fn: [dict(spec)] for fn in RUNNER_FUNCTIONS}
    out = {}
    for fn in RUNNER_FUNCTIONS:
        chain = existing.get(fn)
        if isinstance(chain, list) and chain:
            out[fn] = chain
    return out or None


def coerce_config(existing):
    """Return a config object with defaults backfilled."""
    cfg = default_config()
    if not isinstance(existing, dict):
        return cfg

    for key, value in existing.items():
        # legacy "backend" lived here before it moved to remote.json; legacy
        # "version" before storage/version.txt became the marker. Drop both.
        if key not in ("backend", "version", "approvals", "permissions", "runner", "review"):
            cfg[key] = value

    coerced = _coerce_runner(existing.get("runner"))
    if coerced:
        cfg["runner"].update(coerced)
    if isinstance(existing.get("approvals"), dict):
        cfg["approvals"].update(existing["approvals"])
    if isinstance(existing.get("review"), dict):
        cfg["review"].update(existing["review"])
        cfg["review"].pop("require_human_approval", None)  # legacy: review step is not human-gated
    if isinstance(existing.get("permissions"), dict):
        permissions = existing["permissions"]
        for block in ("git", "remote", "platform", "agents"):
            if isinstance(permissions.get(block), dict):
                cfg["permissions"][block].update(permissions[block])
    return cfg


def load_config(storage):
    """Load configuration.json with defaults backfilled for missing keys."""
    return coerce_config(_load_json(config_file(storage)))


def coerce_remote_state(existing):
    """Return a remote state object with defaults backfilled."""
    remote = default_remote_state()
    if isinstance(existing, dict):
        for key, value in existing.items():
            if key != "state":
                remote[key] = value
        if isinstance(existing.get("state"), dict):
            remote["state"].update(existing["state"])
    remote.pop("token", None)
    return remote


def load_remote_state(storage):
    """Load remote.json with defaults backfilled and legacy token stripped."""
    return coerce_remote_state(_load_json(remote_file(storage)))


def write_config_files(
    storage,
    cfg,
    remote,
    token=None,
    repo_root=None,
    specseed_rel=None,
    ignore_specseed=False,
):
    """Persist config/remote/token files using configure.py's file policy.

    remote.json is ALWAYS written now (it holds the enabled+provider choice). The
    token is only written when the remote is enabled and a token was supplied.
    """
    written = {}
    written["config"] = _write_json(config_file(storage), cfg)
    if ignore_specseed and repo_root is not None and specseed_rel is not None:
        gitignore = ensure_repo_gitignored(repo_root, Path(specseed_rel))
        if gitignore is not None:
            written["repo_gitignore"] = gitignore
    written["remote"] = _write_json(remote_file(storage), remote)
    if remote.get("enabled") and token:
        written["token"] = write_token(storage, token)
        written["storage_gitignore"] = ensure_gitignored(storage)
    return written


def _specseed_config_value(storage, repo_root):
    specseed_root = specseed_dir_from_storage(storage)
    try:
        rel = Path(specseed_root).resolve().relative_to(Path(repo_root).resolve())
        return rel.as_posix(), rel
    except ValueError:
        return Path(specseed_root).resolve().as_posix(), None


def run_defaults(
    storage,
    *,
    config_path=None,
    remote_path=None,
    ignore_specseed=True,
    overwrite_defaults=False,
):
    """Write config files without prompting."""
    repo_root = repo_root_from_cwd()
    cfg = (
        coerce_config(_load_json_object(config_path, "configuration file"))
        if config_path
        else default_config() if overwrite_defaults else load_config(storage)
    )
    remote = (
        coerce_remote_state(_load_json_object(remote_path, "remote file"))
        if remote_path
        else default_remote_state() if overwrite_defaults else load_remote_state(storage)
    )
    specseed_value, specseed_rel = _specseed_config_value(storage, repo_root)
    cfg["specseed_dir"] = specseed_value
    if not cfg.get("dev_branch") or cfg.get("dev_branch") == DEFAULT_DEV_BRANCH:
        cfg["dev_branch"] = detect_default_branch(repo_root)

    written = write_config_files(
        storage,
        cfg,
        remote,
        repo_root=repo_root,
        specseed_rel=specseed_rel,
        ignore_specseed=ignore_specseed and specseed_rel is not None,
    )
    print(f"Configured {repo_root}")
    for name in ("config", "remote", "repo_gitignore"):
        if name in written:
            print(f"  {name}: {written[name]}")
    return 0


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
    """Set remote on/off + provider + repo in `remote` (remote.json). Returns the
    access token (or None for local-only); the caller persists it to token_remote.txt."""
    local = ask_yn(
        "Track work locally only (no GitHub/GitLab remote)?",
        default=not remote.get("enabled"),
    )
    if local:
        remote["enabled"] = False
        remote["provider"] = None
        return None

    provider = ask_choice(
        "Mirror to which host?",
        ("github", "gitlab"),
        remote.get("provider") or "github",
    )
    remote["enabled"] = True
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
    dev_branch = cfg.get("dev_branch") or DEFAULT_DEV_BRANCH
    print("\n--- local git ---")
    git["enabled"] = ask_yn(
        "Let the agent use local git (branch/commit/merge)?",
        default=git.get("enabled", True),
    )
    if not git["enabled"]:
        git["merge_to_dev_branch"] = False
        return
    print("  (creating local branches is always allowed once git is on.)")
    git["merge_to_dev_branch"] = ask_yn(
        f"  Allow merging into the dev branch ({dev_branch})?",
        default=git.get("merge_to_dev_branch", False),
    )


def section_dev_branch(cfg):
    print("\n--- dev branch ---")
    cfg["dev_branch"] = ask_str(
        "Dev branch (where work merges to; main/master)",
        cfg.get("dev_branch") or DEFAULT_DEV_BRANCH,
    ) or DEFAULT_DEV_BRANCH


def section_remote_permissions(cfg, remote):
    if not remote.get("enabled"):
        return
    rem = cfg["permissions"]["remote"]
    dev_branch = cfg.get("dev_branch") or DEFAULT_DEV_BRANCH
    print("\n--- remote actions ---")
    print("  (posting issues/tickets/epics is always allowed — the tracker lives on the remote.)")
    rem["post_control"] = ask_yn(
        "Allow posting the CONTROL channel?",
        default=rem.get("post_control", False),
    )
    rem["push_branches"] = ask_yn(
        "Allow pushing branches to the remote?",
        default=rem.get("push_branches", False),
    )
    rem["push_dev_branch"] = ask_yn(
        f"Allow pushing to the dev branch ({dev_branch}) on the remote?",
        default=rem.get("push_dev_branch", False),
    )
    rem["make_prs"] = ask_yn(
        "Allow opening pull/merge requests?",
        default=rem.get("make_prs", False),
    )


def section_platform(cfg):
    plat = cfg["permissions"].setdefault("platform", {})
    print("\n--- platform autos ---")
    plat["auto_implement_issue"] = ask_yn(
        "Auto-implement ready issues (off = each needs human approval first)?",
        default=plat.get("auto_implement_issue", True),
    )
    plat["auto_proceed_to_next_sprint_if_available"] = ask_yn(
        "Auto-proceed to the next sprint when available (off = human approves)?",
        default=plat.get("auto_proceed_to_next_sprint_if_available", False),
    )


def section_agents(cfg):
    gates = cfg["permissions"].setdefault("agents", default_agent_gates())
    print("\n--- agent action gates ---")
    print("  Level for each action class the implementation agent may hit mid-work:")
    print("  block = never · surface = do + announce · auto = do silently · "
          "require_human_approval = only after a human approves")
    for category, desc in AGENT_CATEGORIES.items():
        gates[category] = ask_choice(
            f"  {category} ({desc})",
            AGENT_LEVELS,
            gates.get(category) or DEFAULT_AGENT_GATES.get(category, "block"),
        )


def section_approvals(cfg):
    approvals = cfg.setdefault("approvals", {})
    print("\n--- approvals ---")
    approvals["approver_usernames"] = ask_csv(
        "Usernames allowed to approve HITL gates remotely (comma/space separated)",
        approvals.get("approver_usernames", []),
    )
    # Loop guard: the platform never reacts to its own comments. Blank = detect
    # by the "specseed: " body prefix alone.
    cfg["platform_username"] = ask_str(
        "Tracker username the platform posts as (blank if same as yours)",
        cfg.get("platform_username", ""),
    )


def _ask_runner_spec(spec):
    """Prompt for one agent spec, defaulting from ``spec``. Returns a spec dict."""
    spec = spec or default_runner_spec()
    provider = ask_choice("    Provider", RUNNER_PROVIDERS, spec.get("provider") or "claude")
    default_model = spec.get("model") or ("gpt-5.4-mini" if provider == "codex" else "opus")
    model = ask_str("    Model", default_model) or default_model
    effort = ask_choice("    Reasoning effort", ("low", "medium", "high"), spec.get("effort") or "high")
    default_dir = spec.get("provider_data_dir") or PROVIDER_DEFAULT_HOME.get(provider, "~/.claude")
    data_dir = ask_str("    Provider data dir", default_dir) or default_dir
    return {"provider": provider, "provider_data_dir": data_dir, "model": model, "effort": effort}


def _edit_runner_chain(function, chain):
    """Edit one function's ordered fallback chain. Primary required, then optional
    fallbacks. Returns a non-empty list of specs."""
    chain = list(chain) if chain else [default_runner_spec()]
    print(f"\n  {function}: primary agent (tried first)")
    new_chain = [_ask_runner_spec(chain[0])]
    idx = 1
    while True:
        existing = chain[idx] if idx < len(chain) else None
        prompt = "  Add a fallback agent?" if existing is None else f"  Keep/replace fallback #{idx}?"
        if not ask_yn(prompt, default=existing is not None):
            break
        print(f"  {function}: fallback #{idx} (tried if the previous one fails)")
        new_chain.append(_ask_runner_spec(existing))
        idx += 1
    return new_chain


def section_runner(cfg):
    runner = _coerce_runner(cfg.get("runner")) or default_runner_chains()
    print("\n--- coding agents (per-function fallback chains) ---")
    for function in RUNNER_FUNCTIONS:
        runner[function] = _edit_runner_chain(function, runner.get(function))
    cfg["runner"] = runner


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
    dev_branch = cfg.get("dev_branch") or DEFAULT_DEV_BRANCH
    if remote.get("enabled"):
        L.append(f"remote: {remote.get('provider')} mirror — repo={remote.get('repo') or '?'}, "
                 f"token={'set' if token else 'MISSING'}")
    else:
        L.append("remote: local only")
    runner = _coerce_runner(cfg.get("runner")) or {}
    for function in RUNNER_FUNCTIONS:
        chain = runner.get(function) or []
        if not chain:
            continue
        specs = ", ".join(
            "{0}/{1}/{2}".format(s.get("provider"), s.get("model") or "default", s.get("effort") or "?")
            for s in chain
        )
        L.append(f"agent[{function}]: {specs}")
    L.append(f"dev branch: {dev_branch}")
    L.append(f"poll interval: {cfg['poll_interval_seconds']}s")
    approvers = cfg.get("approvals", {}).get("approver_usernames", [])
    L.append("approvers: " + (", ".join(approvers) if approvers else "none configured"))
    git = cfg["permissions"]["git"]
    if not git["enabled"]:
        L.append("git: OFF (agent never touches git)")
    else:
        L.append(f"git: on — branches=always, merge_to_dev_branch={git['merge_to_dev_branch']}")
    plat = cfg["permissions"].get("platform", {})
    L.append(f"platform: auto_implement_issue={plat.get('auto_implement_issue', True)}, "
             f"auto_proceed_to_next_sprint={plat.get('auto_proceed_to_next_sprint_if_available', False)}")
    if remote.get("enabled"):
        r = cfg["permissions"]["remote"]
        L.append(f"remote actions: post_control={r['post_control']}, "
                 f"push_branches={r['push_branches']}, push_dev_branch={r['push_dev_branch']}, "
                 f"make_prs={r['make_prs']}")
    gates = cfg["permissions"].get("agents", {})
    blocked = sorted(k for k, v in gates.items() if v == "block")
    L.append("agent gates: " + (f"{len(gates)} classes (block: {', '.join(blocked) or 'none'})"
                                 if gates else "defaults"))
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
        cfg["dev_branch"] = detect_default_branch(repo_root)
        print(f"Configuring a fresh repo (storage at {Path(storage).resolve()}).")

    remote = load_remote_state(storage)
    print("Press Enter to accept the shown default at any prompt.")

    token = section_backend(cfg, remote, storage)
    section_runner(cfg)
    section_dev_branch(cfg)
    section_git_permissions(cfg)
    section_remote_permissions(cfg, remote)
    section_platform(cfg)
    section_agents(cfg)
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
    run_script = specseed_dir / "specseed_runtime" / "executing" / "run.py"
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
    ap.add_argument("--defaults", action="store_true",
                    help="write default config files without prompting")
    ap.add_argument("--defaults-overwrite", action="store_true",
                    help="overwrite existing config with defaults without prompting")
    ap.add_argument("--use-config-file", default=None,
                    help="read configuration.json values from this file")
    ap.add_argument("--use-remote-file", default=None,
                    help="read remote.json values from this file")
    ap.add_argument("--no-gitignore", action="store_true",
                    help="do not add the specseed dir to the repo .gitignore")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    storage = Path(args.storage) if args.storage else default_storage_dir()
    # Storage migrates BEFORE this script reads or rewrites it.
    run_migrations(storage=storage)
    if args.show:
        return run_show(storage)
    if args.defaults or args.defaults_overwrite or args.use_config_file or args.use_remote_file:
        try:
            return run_defaults(
                storage,
                config_path=args.use_config_file,
                remote_path=args.use_remote_file,
                ignore_specseed=not args.no_gitignore,
                overwrite_defaults=args.defaults_overwrite,
            )
        except ValueError as exc:
            print(f"configure.py: {exc}", file=sys.stderr)
            return 1
    return run_interactive(storage, explicit_storage=args.storage is not None)


if __name__ == "__main__":
    sys.exit(main())
