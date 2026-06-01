"""
config.py — load/validate `.specseed/memory/config.json`, the PORTABLE
"how-you-work" config, and render its agent-facing contract for CLAUDE.md (see
routes/configure.md + templates/CLAUDE_template.md "Operating policy").

config.json is the one file a user can copy from repo to repo: it holds ONLY
process ("how I work"), never project-specific data. Four blocks:
  - hitl.categories : action-class gates the *implementation* agent honors at
    runtime. Each fixed CATEGORY maps to a level: "block" (halt + write an
    approval request, then move on) / "surface" (do it, but announce) / "auto"
    (silent).
  - git            : the git-workflow contract (branch model, push, PR, merge).
  - backend        : the work-tracking choice — local-only vs a github/gitlab
    mirror. Just `enabled` + `provider`; the per-repo `repo`/credentials/issue
    map live in `.specseed/memory/remote.json` (state, NOT portable).
  - runner         : how `agents_runner.py` drives the local Claude CLI (model,
    effort, loop interval, turn cap, allowed tools, retry cooldown).

This file is config, NOT a spec — a bare config.json does not change mode routing.
The skill writes it in configure mode and re-renders the CLAUDE.md block from it.
The hitl/git enforcement is a CONTRACT honored by the impl agent reading CLAUDE.md
(like the settled-doc freeze); the runner enforces backend/runner directly.

Stdlib only.

CLI:
  python .specseed/scripts/core/config.py show              # print resolved config
  python .specseed/scripts/core/config.py validate          # exit 1 on schema errors
  python .specseed/scripts/core/config.py render-claude      # emit the CLAUDE.md block
  python .specseed/scripts/core/config.py init               # write a default config.json (won't clobber)
"""

import json
import sys
from pathlib import Path

# Fixed action-gate taxonomy. Push is NOT here — it is a git-workflow setting.
CATEGORIES = {
    "container":        "docker/podman build, run, push, pull",
    "heavy_compute":    "GPU / training / running experiment scripts / long jobs",
    "network":          "outbound non-localhost calls (downloads, external APIs)",
    "deps":             "add/remove a dependency, or major-version bump",
    "data_destructive": "delete data, drop/rewrite schema, destructive migration",
    "external_publish": "deploy, submission, upload — anything leaving the repo",
    "outside_repo":     "writes outside the repo root",
    "secrets":          "reading/writing credentials or secret material",
}
LEVELS = ("block", "surface", "auto")
PROVIDERS = (None, "github", "gitlab")

DEFAULT_CATEGORIES = {
    "container":        "block",
    "heavy_compute":    "block",
    "network":          "surface",
    "deps":             "block",
    "data_destructive": "block",
    "external_publish": "block",
    "outside_repo":     "block",
    "secrets":          "surface",
}

# git-workflow contract defaults.
DEFAULT_GIT = {
    "automation": True,                 # false = agent never touches git
    "integration_branch": "dev",        # branch agents fork from + merge into; null = current branch
    "base_branch": None,                # autodetected main/master (parent of integration_branch); null = detect
    "branch_naming": "{issue_id}-{slug}",
    "push": "user",                     # "auto" = agent pushes | "user" = the human pushes themselves
    "pull_request": "never",            # "never" | "on_merge_ready" (open PR/MR when an issue clean-closes)
    "auto_merge": "clean_close",        # "never" | "clean_close" (tests pass + no spec_concern + no conflict)
    "refresh_on_merge": True,           # after a merge to the integration branch, merge it into other live branches
}

# backend (work-tracking) choice. Portable; the per-repo `repo` + issue map are
# NOT here — they live in remote.json (state). `enabled:false` = local-only.
DEFAULT_BACKEND = {
    "enabled": False,                   # true = mirror the work onto a github/gitlab repo
    "provider": None,                   # "github" | "gitlab" (required when enabled)
}

# runner knobs — how agents_runner.py drives the local Claude Code CLI.
DEFAULT_RUNNER = {
    "model": "opus",
    "effort": "high",
    "interval": 45,                     # seconds between loop passes
    "max_turns": 400,
    "allowed_tools": ["Read", "Edit", "Bash"],
    "retry_delay_minutes": 30,          # after a failed claude run (e.g. session limit), wait this long before retrying
}


def default_config():
    return {
        "configured": True,
        "hitl": {"categories": dict(DEFAULT_CATEGORIES)},
        "git": dict(DEFAULT_GIT),
        "backend": dict(DEFAULT_BACKEND),
        "runner": dict(DEFAULT_RUNNER),
    }


# --------------------------------------------------------------------------- #
# locate / io
# --------------------------------------------------------------------------- #
def find_root(start=None):
    start = Path(start or Path.cwd()).resolve()
    for parent in (start, *start.parents):
        if (parent / ".specseed").is_dir():
            return parent
    raise FileNotFoundError("no .specseed/ found from " + str(start))


def config_path(root=None):
    return find_root(root) / ".specseed" / "memory" / "config.json"


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


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate(cfg):
    """Return a list of error strings ([] == valid)."""
    errs = []
    if not isinstance(cfg, dict):
        return ["config.json is not a JSON object"]

    hitl = cfg.get("hitl") or {}
    cats = hitl.get("categories")
    if not isinstance(cats, dict):
        errs.append("hitl.categories missing or not an object")
    else:
        for name in CATEGORIES:
            if name not in cats:
                errs.append(f"hitl.categories missing category '{name}'")
        for name, lvl in cats.items():
            if name not in CATEGORIES:
                errs.append(f"hitl.categories has unknown category '{name}'")
            if lvl not in LEVELS:
                errs.append(f"hitl.categories['{name}'] = {lvl!r}, not one of {LEVELS}")

    git = cfg.get("git")
    if not isinstance(git, dict):
        errs.append("git block missing or not an object")
    else:
        if not isinstance(git.get("automation", True), bool):
            errs.append("git.automation must be a boolean")
        if git.get("push") not in ("auto", "user"):
            errs.append("git.push must be 'auto' or 'user'")
        if git.get("pull_request") not in ("never", "on_merge_ready"):
            errs.append("git.pull_request must be 'never' or 'on_merge_ready'")
        if git.get("auto_merge") not in ("never", "clean_close"):
            errs.append("git.auto_merge must be 'never' or 'clean_close'")
        if not isinstance(git.get("refresh_on_merge", True), bool):
            errs.append("git.refresh_on_merge must be a boolean")

    backend = cfg.get("backend")
    if not isinstance(backend, dict):
        errs.append("backend block missing or not an object")
    else:
        if not isinstance(backend.get("enabled", False), bool):
            errs.append("backend.enabled must be a boolean")
        if backend.get("provider") not in PROVIDERS:
            errs.append(f"backend.provider must be one of {PROVIDERS}")
        if backend.get("enabled") and not backend.get("provider"):
            errs.append("backend.enabled is true but backend.provider is not set")

    runner = cfg.get("runner")
    if not isinstance(runner, dict):
        errs.append("runner block missing or not an object")
    else:
        for key in ("model", "effort"):
            if not isinstance(runner.get(key, ""), str) or not runner.get(key):
                errs.append(f"runner.{key} must be a non-empty string")
        for key in ("interval", "max_turns", "retry_delay_minutes"):
            v = runner.get(key)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                errs.append(f"runner.{key} must be a positive integer")
        tools = runner.get("allowed_tools")
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            errs.append("runner.allowed_tools must be a list of strings")
    return errs


# --------------------------------------------------------------------------- #
# render the CLAUDE.md operating-policy block
# --------------------------------------------------------------------------- #
def render_claude(cfg):
    """Markdown for the READ-FIRST block in CLAUDE.md. Deterministic from config."""
    cats = (cfg.get("hitl") or {}).get("categories") or {}
    git = cfg.get("git") or {}
    L = []
    L.append("## ⚠️ Operating policy — READ FIRST, ALWAYS")
    L.append("")
    L.append("Hard constraints. They override convenience and any instinct to just get "
             "the work done. Honor them on EVERY invocation, including non-interactive "
             "`/loop` / runner runs.")
    L.append("")
    L.append("### Action gates")
    L.append("")
    L.append("Before an action in any class below, obey its level:")
    L.append("- **block** → do NOT do it. Write an approval request (see *Park-and-continue*) "
             "and move to other work.")
    L.append("- **surface** → do it, but announce it (note in the step report; on the mirror, "
             "it shows in the done/blocked comment) so the human can see it happened.")
    L.append("- **auto** → just do it, silently.")
    L.append("")
    L.append("| Action class | Covers | Level |")
    L.append("|---|---|---|")
    for name, desc in CATEGORIES.items():
        L.append(f"| `{name}` | {desc} | **{cats.get(name, '—')}** |")
    L.append("")
    L.append("These fire **mid-work, regardless of which issue is active** — they are separate "
             "from the per-issue `approval_required` / `review_required` completion gates below. "
             "When unsure whether an action falls in a class, treat it as the stricter case.")
    L.append("")
    L.append("### Park-and-continue (when a `block` gate fires)")
    L.append("")
    L.append("1. Do NOT perform the action.")
    L.append("2. Append an entry to `.specseed/project_management/issues/<issue_id>/approval.md` "
             "(create if absent) — see the template below.")
    L.append("3. Set the issue `status: \"awaiting_approval\"` in `issues.json` (KEEP your claim "
             "fields — work is in flight).")
    L.append("4. Run `python .specseed/scripts/core/approvals_render.py` to refresh the pending index.")
    L.append("5. **Move on** to the next ready non-gated issue (`claim_issue.py`). Do not block the "
             "loop waiting. The parked issue resumes when a human resolves the request.")
    L.append("")
    L.append("Resolution: the human runs `/specseed approve` (interactively, or by spinning up an "
             "agent and saying \"approve <ID>\" / \"next thing needing approval\"), or — if the "
             "mirror is on — comments `approve <ID>` / `reject <ID> <note>` on the github CONTROL "
             "issue. Either writes a `## Resolved` marker and flips the issue back to `todo` "
             "(approved) or `wont_do`/`blocked` (rejected/hold).")
    L.append("")
    L.append("**approval.md entry template:**")
    L.append("")
    L.append("```markdown")
    L.append("## A<N> — <one-line summary>")
    L.append("- **Opened:** <ISO date>")
    L.append("- **Kind:** gate:<category> | run-action | git-conflict | entity-approval")
    L.append("- **Status:** open")
    L.append("- **What I need / am about to do:** <one paragraph>")
    L.append("- **Why it's gated:** <category + reason>")
    L.append("- **Risks / blast radius:** <what could go wrong, what it touches>")
    L.append("- **Links / details:** <paths, URLs, expected cost/runtime>")
    L.append("- **Options:** A) <option> (recommended) · B) <option>")
    L.append("- **How to run it yourself (if applicable):** <exact commands + expected runtime/output>")
    L.append("  <!-- include when a human can/should run it directly — sometimes the ONLY path, "
             "e.g. prod deploy. Use judgement. -->")
    L.append("- **Resolve:** `/specseed approve <issue_id> A` (local), or comment "
             "`approve <issue_id> A` on the CONTROL issue (remote).")
    L.append("```")
    L.append("")
    L.append("For a **run-action** (a gated thing only a human can execute), the agent does NOT run "
             "it — it writes the request WITH comprehensive self-run instructions, then parks. "
             "After the human runs it, the agent transcribes results into a step report. No source "
             "edits while waiting on a run-action.")
    L.append("")
    L.append(_render_git(git))
    return "\n".join(L) + "\n"


def _render_git(git):
    L = []
    L.append("### Git workflow")
    L.append("")
    if not git.get("automation", True):
        L.append("**Git automation is OFF.** Do NOT run any git command (no commit, branch, "
                 "merge, or push). Make your edits; the human handles all git operations. Tell "
                 "them what changed.")
        return "\n".join(L)

    integ = git.get("integration_branch")
    naming = git.get("branch_naming", "{issue_id}-{slug}")
    push = git.get("push", "user")
    pr = git.get("pull_request", "never")
    automerge = git.get("auto_merge", "clean_close")
    base = git.get("base_branch") or "the repo's default branch (autodetect main/master)"

    if integ:
        L.append(f"- **Integration branch:** `{integ}` (created off {base} if absent). All work "
                 f"merges here; the default branch stays release-grade.")
        L.append(f"- **Per issue:** branch off `{integ}` named `{naming}` BEFORE editing. One issue → "
                 "one branch. Resume a parked issue on its existing branch (don't fork a second).")
    else:
        L.append("- **No integration branch:** work directly on the current branch (commit only; "
                 "no feature branches).")
    L.append(f"- **Refresh:** {'after each merge into the integration branch, and on resuming a parked branch, merge the latest integration branch into other live feature branches. Resolve trivial conflicts silently; only a genuinely unsafe/semantic conflict you cannot settle becomes a `git-conflict` approval request.' if git.get('refresh_on_merge', True) and integ else 'no automatic cross-branch refresh.'}")
    if push == "auto":
        L.append("- **Push:** agent pushes its branch to the remote automatically.")
    else:
        L.append("- **Push:** the agent does NOT push. Tell the human what to push; they push "
                 "themselves.")
    if pr == "on_merge_ready" and integ:
        L.append("- **PR/MR:** when an issue clean-closes, open a PR/MR into the integration branch.")
    else:
        L.append("- **PR/MR:** none (local workflow).")
    if automerge == "clean_close" and integ:
        L.append(f"- **Auto-merge:** on a clean close (all plan steps done, tests pass, any required "
                 f"gates cleared, no `spec_concern`, no unresolved conflict), squash-merge the branch "
                 f"into `{integ}` and delete it. Otherwise do NOT merge — surface to the human.")
    else:
        L.append("- **Auto-merge:** never. The human merges.")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv):
    cmd = argv[0] if argv else "show"
    if cmd == "init":
        try:
            p = config_path()
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if p.exists():
            print(f"config.json already exists at {p} — not overwriting")
            return 0
        save_config(default_config())
        print(f"wrote default config to {p}")
        return 0

    cfg = load_config()
    if cfg is None:
        if cmd == "show":
            print("no config.json (run configure mode, or `config.py init`)")
            return 0
        print("ERROR: no config.json found", file=sys.stderr)
        return 2

    if cmd == "show":
        print(json.dumps(cfg, indent=2))
        return 0
    if cmd == "validate":
        errs = validate(cfg)
        if errs:
            for e in errs:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print("OK: config.json valid")
        return 0
    if cmd in ("render-claude", "render"):
        sys.stdout.write(render_claude(cfg))
        return 0
    print(f"unknown: {cmd}  (show | validate | render-claude | init)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
