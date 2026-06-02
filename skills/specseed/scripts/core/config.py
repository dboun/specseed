"""
config.py — load/validate `.specseed/memory/config.json`, the PORTABLE
"how-you-work" config, and render its agent-facing contract for CLAUDE.md (see
routes/configure.md + templates/CLAUDE_template.md "Operating policy").

config.json is the one file a user can copy from repo to repo: it holds ONLY
process ("how I work"), never project-specific data. Blocks:
  - hitl.categories : action-class gates the *implementation* agent honors at
    runtime. Each fixed CATEGORY maps to a level: "block" (halt + write an
    approval request, then move on) / "surface" (do it, but announce) / "auto"
    (silent).
  - git            : the git-workflow contract (branch model, push, PR, merge).
  - backend        : the work-tracking choice — local-only vs a github/gitlab
    mirror. `enabled` + `provider`, plus portable mirror options like whether to
    project user-facing entity templates into the git host's issue-template dir.
    The per-repo `repo`/credentials/issue map live in `.specseed/memory/remote.json`
    (state, NOT portable).
  - runner         : how `agents_runner.py` drives the coding-agent CLIs. Global
    knobs (interval, turn cap, allowed tools, retry cooldown) PLUS `agents` — a
    matrix of FUNCTION (implement/review/qa) → DIFFICULTY (easy/hard) → an ordered
    fallback chain of {provider, config_dir, model, effort} specs. provider is
    claude or codex; merges keep running inside the coding (implement) agent.
  - review         : the code-review phase — scope (which issues) + the
    confidence/difficulty auto-approve gate. Read by review_gate.py.
  - qa             : end-of-ticket QA — whether to emit a terminal `type: qa`
    issue per ticket (suggest / all / off).

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
  python .specseed/scripts/core/config.py list-models claude|codex [config_dir]  # selectable models
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

# --------------------------------------------------------------------------- #
# multi-agent runner taxonomy.
#   FUNCTIONS        — the REQUIRED runner jobs (every config must define these).
#   OPTIONAL_FUNCTIONS — extra jobs that may be absent (back-compat); validated
#     only when present, filled from default_agents() otherwise. `respec` (the CR
#     conductor) lives here so an old config with no `respec` key still loads.
#   ALL_FUNCTIONS    — the full set of known function keys (required + optional).
#   DIFFICULTIES — each function splits by issue difficulty (easy/hard).
#   AGENT_PROVIDERS — the coding-agent CLIs the runner can drive.
# A "spec" = {provider, config_dir, model, effort}; a list of specs is an ordered
# fallback chain (first = main, rest tried on failure).
#
# `respec` (CR conductor) has NO real easy/hard split — a spec-change request is
# not difficulty-graded. To reuse the same matrix machinery (and the same
# `agent_chain` accessor + validator), both buckets hold the SAME single chain;
# the runner always reads the `hard` bucket (`agent_chain(cfg, "respec", "hard")`).
# --------------------------------------------------------------------------- #
FUNCTIONS = ("implement", "review", "qa")
OPTIONAL_FUNCTIONS = ("respec",)
ALL_FUNCTIONS = FUNCTIONS + OPTIONAL_FUNCTIONS
DIFFICULTIES = ("easy", "hard")
AGENT_PROVIDERS = ("claude", "codex")
CLAUDE_MODEL_ALIASES = ("opus", "sonnet", "haiku")
# per-provider default config dir (when a spec's config_dir is null).
PROVIDER_CONFIG_ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
PROVIDER_DEFAULT_HOME = {"claude": "~/.claude", "codex": "~/.codex"}

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
    "ignore_labels": [
        # Unknown remote issues carrying any of these labels are treated as drafts /
        # non-actionable notes and are NOT ingested as work.
        "draft", "ignore", "specseed:ignore", "changes-requested",
        "needs-more-info", "needs-triage",
    ],
    "entity_templates": {
        "enabled": False,               # true = also write bug/feature/CR templates into the host issue-template dir
    },
}

# runner knobs — how agents_runner.py drives the coding-agent CLIs.
# `agents` maps each FUNCTION → each DIFFICULTY → an ordered fallback chain of
# specs. The runner peeks the next issue's type+difficulty, picks the matching
# chain, and tries each spec until one succeeds. `max_turns`/`allowed_tools` are
# Claude-only (ignored for codex specs).
def default_agents():
    """Default agent matrix: all-Claude, opus/high for hard, sonnet/medium for easy."""
    def spec(model, effort):
        return {"provider": "claude", "config_dir": None, "model": model, "effort": effort}
    hard = lambda m="opus": [spec(m, "high")]
    easy = lambda m="sonnet": [spec(m, "medium")]
    # respec: no easy/hard split — both buckets hold the SAME opus/high chain.
    respec = lambda: [spec("opus", "high")]
    return {
        "implement": {"easy": easy(), "hard": hard()},
        "review":    {"easy": easy(), "hard": hard()},
        "qa":        {"easy": easy(), "hard": [spec("sonnet", "high")]},
        "respec":    {"easy": respec(), "hard": respec()},
    }


DEFAULT_RUNNER = {
    "interval": 45,                     # seconds between loop passes
    "max_turns": 400,                   # Claude only
    "allowed_tools": ["Read", "Edit", "Bash"],  # Claude only
    "retry_delay_minutes": 30,          # after a failed run (e.g. session limit), wait this long before retrying the chain
    "agents": default_agents(),
}

# code-review phase. Default ON at `hard` (configure asks + suggests this).
# The reviewer writes issues/<id>/review.json; review_gate.py reads it + this block
# + the issue's `difficulty` to decide whether to auto-advance or land in awaiting_approval.
DEFAULT_REVIEW = {
    "enabled": True,
    "scope": "hard",                    # which issues get reviewed: hard | easy | both | none
    "auto_approve": {
        "min_confidence": 90,           # reviewer confidence (0-100) needed to skip the human gate
        "difficulty": ["easy"],         # difficulties allowed to auto-advance; `hard` never auto-advances
    },
}
REVIEW_SCOPES = ("hard", "easy", "both", "none")

# end-of-ticket QA. A terminal `type: qa` issue inside a ticket (depends_on all its
# siblings). mode: suggest = work-breakdown proposes QA per ticket (smart, effort-based);
# all = force on every ticket; off = never. `enabled:false` also disables.
DEFAULT_QA = {
    "enabled": True,
    "mode": "suggest",                  # suggest | all | off
    "effort_threshold_hours": 4.0,      # a ticket at/above this effort is a QA candidate (suggest mode)
}
QA_MODES = ("suggest", "all", "off")

# spec-change requests (CR-NNNN). OFF by default — the whole feature (runner respec
# mode + remote `change-request` intake) is inert unless `enabled:true`. `label` is the
# remote issue label that marks a CR; `branch_prefix` names the isolated respec branch
# (`cr/CR-0001`). See routes/change-request.md + references/work-breakdown.md.
DEFAULT_CR = {
    "enabled": False,
    "label": "change-request",
    "branch_prefix": "cr/",
}


def default_runner():
    """Fresh runner block (no shared nested mutables)."""
    return {
        "interval": DEFAULT_RUNNER["interval"],
        "max_turns": DEFAULT_RUNNER["max_turns"],
        "allowed_tools": list(DEFAULT_RUNNER["allowed_tools"]),
        "retry_delay_minutes": DEFAULT_RUNNER["retry_delay_minutes"],
        "agents": default_agents(),
    }


def default_config():
    return {
        "configured": True,
        "hitl": {"categories": dict(DEFAULT_CATEGORIES)},
        "git": dict(DEFAULT_GIT),
        "backend": default_backend(),
        "runner": default_runner(),
        "review": _deep_copy_review(),
        "qa": dict(DEFAULT_QA),
        "cr": dict(DEFAULT_CR),
    }


def _deep_copy_review():
    r = dict(DEFAULT_REVIEW)
    r["auto_approve"] = dict(DEFAULT_REVIEW["auto_approve"])
    r["auto_approve"]["difficulty"] = list(DEFAULT_REVIEW["auto_approve"]["difficulty"])
    return r


def default_backend():
    b = {k: v for k, v in DEFAULT_BACKEND.items()
         if k not in ("entity_templates", "ignore_labels")}
    b["ignore_labels"] = list(DEFAULT_BACKEND["ignore_labels"])
    b["entity_templates"] = dict(DEFAULT_BACKEND["entity_templates"])
    return b


# --------------------------------------------------------------------------- #
# typed accessors (back-compat: absent optional block → defaults)
# --------------------------------------------------------------------------- #
def review_config(cfg):
    """The `review` block, with defaults filled for any missing key."""
    base = _deep_copy_review()
    r = (cfg or {}).get("review") or {}
    base.update({k: v for k, v in r.items() if k != "auto_approve"})
    aa = r.get("auto_approve") or {}
    base["auto_approve"].update(aa)
    return base


def qa_config(cfg):
    base = dict(DEFAULT_QA)
    base.update((cfg or {}).get("qa") or {})
    return base


def cr_config(cfg):
    """The `cr` block, with defaults filled for any missing key (back-compat: an
    old config.json with no `cr` block loads + gets `enabled:false`)."""
    base = dict(DEFAULT_CR)
    base.update((cfg or {}).get("cr") or {})
    return base


def backend_config(cfg):
    """The `backend` block, with nested defaults filled (back-compat: old configs
    had only enabled/provider)."""
    base = default_backend()
    b = (cfg or {}).get("backend") or {}
    base.update({k: v for k, v in b.items() if k != "entity_templates"})
    et = b.get("entity_templates") or {}
    if isinstance(et, dict):
        base["entity_templates"].update(et)
    return base


def entity_templates_config(cfg):
    return backend_config(cfg)["entity_templates"]


def agent_chain(cfg, function, difficulty):
    """Ordered fallback chain of agent specs for a (function, difficulty), filling
    from default_agents() when the block is absent/partial. `difficulty` is bucketed
    to 'easy'/'hard' (anything but 'easy' → 'hard', conservative)."""
    bucket = "easy" if difficulty == "easy" else "hard"
    agents = ((cfg or {}).get("runner") or {}).get("agents") or {}
    fn = agents.get(function) or {}
    chain = fn.get(bucket)
    if isinstance(chain, list) and chain:
        return chain
    return default_agents()[function][bucket]


def agent_main(cfg, function, difficulty):
    """The primary (first) spec for a (function, difficulty)."""
    return agent_chain(cfg, function, difficulty)[0]


# --------------------------------------------------------------------------- #
# model enumeration (for the configure flow + tests)
# --------------------------------------------------------------------------- #
def _codex_cache_path(config_dir=None):
    import os
    base = config_dir or os.environ.get("CODEX_HOME") or "~/.codex"
    return Path(base).expanduser() / "models_cache.json"


def _load_codex_cache(config_dir=None):
    try:
        with _codex_cache_path(config_dir).open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def list_codex_models(config_dir=None):
    """Visible Codex model slugs from `<config_dir|$CODEX_HOME|~/.codex>/models_cache.json`
    (visibility == 'list'), de-duped in cache order. [] if the cache is missing/unreadable."""
    payload = _load_codex_cache(config_dir)
    models = (payload or {}).get("models")
    if not isinstance(models, list):
        return []
    slugs, seen = [], set()
    for model in models:
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        slug = str(model.get("slug") or "").strip()
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    return slugs


def codex_reasoning_levels(slug, config_dir=None):
    """Supported reasoning (effort) levels for a Codex model slug, [] if unknown."""
    payload = _load_codex_cache(config_dir)
    for model in (payload or {}).get("models") or []:
        if isinstance(model, dict) and model.get("slug") == slug:
            levels = model.get("supported_reasoning_levels")
            return [str(x) for x in levels] if isinstance(levels, list) else []
    return []


def list_models(provider, config_dir=None):
    """Selectable models for a provider: Claude = the fixed aliases; Codex = cache slugs."""
    if provider == "claude":
        return list(CLAUDE_MODEL_ALIASES)
    if provider == "codex":
        return list_codex_models(config_dir)
    return []


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
def _validate_agents(agents):
    """Validate runner.agents: every FUNCTION → every DIFFICULTY → non-empty list
    of {provider, config_dir, model, effort} specs."""
    errs = []
    if not isinstance(agents, dict):
        return ["runner.agents missing or not an object"]
    # Required functions must be present; optional ones (e.g. `respec`) are validated
    # only when present (absent → filled from default_agents() by agent_chain).
    for fn in FUNCTIONS:
        if fn not in agents:
            errs.append(f"runner.agents missing function '{fn}'")
    for fn in agents:
        if fn not in ALL_FUNCTIONS:
            errs.append(f"runner.agents has unknown function '{fn}' "
                        f"(expected {' | '.join(ALL_FUNCTIONS)})")
            continue
        buckets = agents.get(fn)
        if not isinstance(buckets, dict):
            errs.append(f"runner.agents['{fn}'] must be an object")
            continue
        for diff in DIFFICULTIES:
            chain = buckets.get(diff)
            if not isinstance(chain, list) or not chain:
                errs.append(f"runner.agents['{fn}']['{diff}'] must be a non-empty list")
                continue
            for i, spec in enumerate(chain):
                where = f"runner.agents['{fn}']['{diff}'][{i}]"
                if not isinstance(spec, dict):
                    errs.append(f"{where} must be an object")
                    continue
                if spec.get("provider") not in AGENT_PROVIDERS:
                    errs.append(f"{where}.provider must be one of {AGENT_PROVIDERS}")
                for key in ("model", "effort"):
                    if not isinstance(spec.get(key), str) or not spec.get(key):
                        errs.append(f"{where}.{key} must be a non-empty string")
                cd = spec.get("config_dir", None)
                if cd is not None and not isinstance(cd, str):
                    errs.append(f"{where}.config_dir must be null or a string")
    return errs


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
        ignore_labels = backend.get("ignore_labels")
        if ignore_labels is not None:
            if not isinstance(ignore_labels, list) or \
                    not all(isinstance(x, str) and x.strip() for x in ignore_labels):
                errs.append("backend.ignore_labels must be a list of non-empty strings")
        et = backend.get("entity_templates")
        if et is not None:
            if not isinstance(et, dict):
                errs.append("backend.entity_templates must be an object")
            elif not isinstance(et.get("enabled", False), bool):
                errs.append("backend.entity_templates.enabled must be a boolean")

    runner = cfg.get("runner")
    if not isinstance(runner, dict):
        errs.append("runner block missing or not an object")
    else:
        for key in ("interval", "max_turns", "retry_delay_minutes"):
            v = runner.get(key)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                errs.append(f"runner.{key} must be a positive integer")
        tools = runner.get("allowed_tools")
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            errs.append("runner.allowed_tools must be a list of strings")
        errs.extend(_validate_agents(runner.get("agents")))

    # review / qa are OPTIONAL blocks — absent = use defaults (back-compat with
    # configs written before they existed). Validate only when present.
    review = cfg.get("review")
    if review is not None:
        if not isinstance(review, dict):
            errs.append("review block must be an object")
        else:
            if not isinstance(review.get("enabled", False), bool):
                errs.append("review.enabled must be a boolean")
            if review.get("scope", "hard") not in REVIEW_SCOPES:
                errs.append(f"review.scope must be one of {REVIEW_SCOPES}")
            aa = review.get("auto_approve", {})
            if not isinstance(aa, dict):
                errs.append("review.auto_approve must be an object")
            else:
                mc = aa.get("min_confidence", 0)
                if not isinstance(mc, (int, float)) or isinstance(mc, bool) \
                        or not (0 <= mc <= 100):
                    errs.append("review.auto_approve.min_confidence must be a number 0-100")
                diff = aa.get("difficulty", [])
                if not isinstance(diff, list) or not all(d in ("easy", "hard") for d in diff):
                    errs.append("review.auto_approve.difficulty must be a list of "
                                "'easy'/'hard'")

    qa = cfg.get("qa")
    if qa is not None:
        if not isinstance(qa, dict):
            errs.append("qa block must be an object")
        else:
            if not isinstance(qa.get("enabled", False), bool):
                errs.append("qa.enabled must be a boolean")
            if qa.get("mode", "suggest") not in QA_MODES:
                errs.append(f"qa.mode must be one of {QA_MODES}")
            th = qa.get("effort_threshold_hours", 0)
            if not isinstance(th, (int, float)) or isinstance(th, bool) or th < 0:
                errs.append("qa.effort_threshold_hours must be a non-negative number")

    # cr is an OPTIONAL block — absent = use defaults (back-compat with configs
    # written before spec-change requests existed). Validate only when present.
    cr = cfg.get("cr")
    if cr is not None:
        if not isinstance(cr, dict):
            errs.append("cr block must be an object")
        else:
            if not isinstance(cr.get("enabled", False), bool):
                errs.append("cr.enabled must be a boolean")
            label = cr.get("label", DEFAULT_CR["label"])
            if not isinstance(label, str) or not label:
                errs.append("cr.label must be a non-empty string")
            prefix = cr.get("branch_prefix", DEFAULT_CR["branch_prefix"])
            if not isinstance(prefix, str) or not prefix or not prefix.endswith("/"):
                errs.append("cr.branch_prefix must be a non-empty string ending with '/'")
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
    L.append(_render_completion_gates(cfg))
    if cr_config(cfg).get("enabled"):
        L.append("### Spec-change requests")
        L.append("")
        L.append("Spec-change requests (`CR-NNNN`) are handled by the runner via the "
                 "change-request route. Do NOT action one yourself: if you spot a request to "
                 "change the spec, surface it (`/specseed adapt`) rather than editing settled "
                 "docs or filing work.")
        L.append("")
    L.append(_render_git(git))
    return "\n".join(L) + "\n"


def _render_completion_gates(cfg):
    review = review_config(cfg)
    qa = qa_config(cfg)
    L = []
    L.append("### Completion gates (review / QA)")
    L.append("")
    L.append("Separate from the per-issue `review_required` / `approval_required` flags in "
             "the issue frontmatter, which you must NEVER advance past yourself (the one hard "
             "rule). Beyond those:")
    if review.get("enabled") and review.get("scope") != "none":
        scope = review.get("scope")
        L.append(f"- **Code review is ON** (scope: `{scope}` issues). After you finish such an "
                 "issue, leave it in `in_review` (do NOT mark it `done`). A separate reviewer "
                 "writes `issues/<id>/review.json`; `review_gate.py` then advances it (auto if "
                 "confidence clears the bar, else `awaiting_approval` for a human). If the "
                 "reviewer requests changes the issue returns to `in_progress` — address the "
                 "findings, don't re-review your own work.")
    else:
        L.append("- **Code review is OFF.**")
    if qa.get("enabled") and qa.get("mode") != "off":
        L.append("- **QA issues** (`type: qa`) may exist as the terminal issue of a ticket "
                 "(they `depends_on` all siblings). When you claim one, run its checklist "
                 "(smoke + regression over the touched paths; scratch work in `/tmp`). File any "
                 "problem as a NEW `bug` issue under the SAME ticket (high priority if it blocks "
                 "the ticket's value) — do NOT silently fix and do NOT expand the QA scope.")
    L.append("")
    return "\n".join(L)


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
    if cmd == "list-models":
        provider = argv[1] if len(argv) > 1 else ""
        config_dir = argv[2] if len(argv) > 2 else None
        if provider not in AGENT_PROVIDERS:
            print(f"usage: config.py list-models <{' | '.join(AGENT_PROVIDERS)}> "
                  "[config_dir]", file=sys.stderr)
            return 2
        models = list_models(provider, config_dir)
        if provider == "codex" and not models:
            print("WARNING: no Codex models found "
                  "(no models_cache.json, or none visible)", file=sys.stderr)
        for m in models:
            print(m)
        return 0
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
    print(f"unknown: {cmd}  (show | validate | render-claude | init | list-models)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
