"""
configure.py — interactive technical setup for specseed. PURE PYTHON: stdlib only,
NO agent calls, no tokens. The human runs this once on a repo (and any time they want
to change settings); it writes the PORTABLE `.specseed/memory/config.json` (always)
and, for a github/gitlab mirror, the per-repo `.specseed/memory/remote.json` (state).

It owns ONLY the config files. Everything else (provisioning the scripts tree,
stamping version.txt, entity templates, README, the CLAUDE.md operating-policy block)
is done by the agent's configure route when you go back to it. So: run this, answer
the questions, then return to your agent.

The questions are simple + yes/no heavy; blast Enter to take all-local defaults.
Re-running on an already-configured repo loads your current values as the defaults.

Usage:
  python3 <skill>/scripts/configure.py                 # interactive (default)
  python3 <skill>/scripts/configure.py --root PATH      # target a repo other than cwd
  python3 <skill>/scripts/configure.py --show           # print resolved config, exit
  python3 <skill>/scripts/configure.py --defaults        # write defaults, no prompts
  python3 <skill>/scripts/configure.py --set git.push=auto --set hitl.categories.network=block
                                                        # scripted overrides, no prompts

`--set KEY=VALUE` takes a dotted path into config.json; VALUE is coerced
(true/false → bool, digits → int, JSON for lists/objects, else string). Overrides
start from the existing config if present, else the defaults. Every write is validated
first (invalid → nothing written, exit 2).

Stdlib only. Human-run (not part of the agent loop).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))
import config as cfgmod  # noqa: E402


# --------------------------------------------------------------------------- #
# remote default-state (for a mirror). Reuse remote_config if present; else inline.
# --------------------------------------------------------------------------- #
def _remote_default_state(repo=None, allowlist=None):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "remote"))
        import remote_config  # noqa: E402
        return remote_config.default_state(repo, allowlist)
    except Exception:
        return {
            "repo": repo,
            "allowlist": allowlist or [],
            "permanent": {"roadmap": None, "timeline": None, "control": None, "sprint": None},
            "map": {}, "cli_cursor": None, "cli_cursor_ids": [], "pull_cursor": None,
            "labels_seeded": False, "scaffolded": False, "initialized": False,
        }


# --------------------------------------------------------------------------- #
# paths / io (write directly — config.save_config needs an existing .specseed/)
# --------------------------------------------------------------------------- #
def target_root(arg=None):
    return Path(arg or Path.cwd()).resolve()


def config_file(root):
    return target_root(root) / ".specseed" / "memory" / "config.json"


def remote_file(root):
    return target_root(root) / ".specseed" / "memory" / "remote.json"


def load_existing(root):
    p = config_file(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def load_remote(root):
    p = remote_file(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def write_config(cfg, root):
    p = config_file(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return p


def write_remote(state, root):
    p = remote_file(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# value coercion + dotted-key set (the non-interactive surface)
# --------------------------------------------------------------------------- #
def coerce(value_str):
    """Coerce a CLI string into a JSON-ish value: bool / int / float / json / str."""
    s = value_str.strip()
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None
    if s and (s[0] in "[{" or (s[0] == '"' and s[-1] == '"')):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def apply_set(cfg, dotted_key, value):
    """Set cfg[a][b][c] = value for a 'a.b.c' key, creating intermediate dicts.
    Raises ValueError if an intermediate node exists but is not a dict."""
    parts = [p for p in dotted_key.split(".") if p != ""]
    if not parts:
        raise ValueError("empty key")
    node = cfg
    for key in parts[:-1]:
        nxt = node.get(key)
        if nxt is None:
            nxt = {}
            node[key] = nxt
        elif not isinstance(nxt, dict):
            raise ValueError(f"cannot descend into non-object at '{key}' in '{dotted_key}'")
        node = nxt
    node[parts[-1]] = value
    return cfg


def apply_sets(cfg, pairs):
    """Apply a list of 'key=value' strings onto cfg (value coerced)."""
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--set expects KEY=VALUE, got {pair!r}")
        key, raw = pair.split("=", 1)
        apply_set(cfg, key.strip(), coerce(raw))
    return cfg


# --------------------------------------------------------------------------- #
# human-readable summary
# --------------------------------------------------------------------------- #
def summary_lines(cfg):
    L = []
    backend = cfg.get("backend") or {}
    if backend.get("enabled"):
        L.append(f"backend: {backend.get('provider')} mirror")
    else:
        L.append("backend: local only")
    git = cfg.get("git") or {}
    if not git.get("automation", True):
        L.append("git: automation OFF (agent never touches git)")
    else:
        integ = git.get("integration_branch") or "current branch"
        L.append(f"git: branch={integ}, push={git.get('push')}, "
                 f"auto_merge={git.get('auto_merge')}, pr={git.get('pull_request')}")
    cats = (cfg.get("hitl") or {}).get("categories") or {}
    overrides = {k: v for k, v in cats.items()
                 if v != cfgmod.DEFAULT_CATEGORIES.get(k)}
    L.append("gates: all default" if not overrides
             else "gates: " + ", ".join(f"{k}={v}" for k, v in overrides.items()))
    review = cfg.get("review") or {}
    if review.get("enabled") and review.get("scope") != "none":
        L.append(f"review: on (scope={review.get('scope')}, "
                 f"min_confidence={(review.get('auto_approve') or {}).get('min_confidence')})")
    else:
        L.append("review: off")
    qa = cfg.get("qa") or {}
    L.append(f"qa: {'off' if not qa.get('enabled') else qa.get('mode')}")
    impl_hard = cfgmod.agent_main(cfg, "implement", "hard")
    impl_easy = cfgmod.agent_main(cfg, "implement", "easy")
    L.append(f"agents (implement): hard={impl_hard.get('provider')}/{impl_hard.get('model')}, "
             f"easy={impl_easy.get('provider')}/{impl_easy.get('model')}")
    cr = cfg.get("cr") or {}
    L.append(f"spec-change requests: {'on' if cr.get('enabled') else 'off'}")
    return L


def print_summary(cfg):
    print("\n--- config summary ---")
    for line in summary_lines(cfg):
        print("  " + line)
    print("----------------------")


# --------------------------------------------------------------------------- #
# interactive prompt primitives (parse-until-ok). EOF = accept default.
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
    shown = f" [{default}] " if default != "" else " "
    try:
        ans = _input(prompt + shown).strip()
    except EOFError:
        return default
    return ans if ans != "" else default


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


# --------------------------------------------------------------------------- #
# interactive sections — each mutates cfg in place
# --------------------------------------------------------------------------- #
def section_backend(cfg, root):
    """Returns remote_state dict (or None for local-only)."""
    backend = cfg["backend"]
    local = ask_yn("Track work locally only (nothing external)?",
                   default=not backend.get("enabled"))
    if local:
        backend["enabled"] = False
        backend["provider"] = None
        return None
    provider = ask_choice("Mirror to which host?", ("github", "gitlab"),
                          backend.get("provider") or "github")
    backend["enabled"] = True
    backend["provider"] = provider
    # detect repo from origin
    detected = ""
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"],
                             cwd=str(target_root(root)), capture_output=True, text=True)
        if out.returncode == 0:
            detected = out.stdout.strip()
    except Exception:
        pass
    existing = load_remote(root) or {}
    repo = ask_str("Repo (owner/name, URL, or self-hosted host)",
                   existing.get("repo") or detected or "")
    state = _remote_default_state(repo=repo or None,
                                  allowlist=existing.get("allowlist") or [])
    print("  NOTE: set GITHUB_PAT / GITLAB_PAT in your env or .env; the agent verifies "
          "it (remote_config.py ping) when you return. No Anthropic key needed.")
    return state


def section_git(cfg):
    git = cfg["git"]
    keep = ask_yn(
        "Keep default git workflow (a 'dev' integration branch, one branch per issue, "
        "auto-merge clean issues, you push)?", default=True)
    if keep:
        return
    if not ask_yn("Let the agent touch git at all (branch/commit/merge)?",
                  default=git.get("automation", True)):
        git["automation"] = False
        return
    git["automation"] = True
    integ = ask_str("Integration branch name (blank = work on current branch)",
                    git.get("integration_branch") or "dev")
    git["integration_branch"] = integ or None
    git["push"] = "auto" if ask_yn("Should the agent push its branch automatically "
                                   "(else you push)?", default=git.get("push") == "auto") \
        else "user"
    git["auto_merge"] = "clean_close" if ask_yn(
        "Auto-merge a clean issue back into the integration branch?",
        default=git.get("auto_merge") == "clean_close") else "never"
    git["pull_request"] = "on_merge_ready" if ask_yn(
        "Open a PR/MR per issue?", default=git.get("pull_request") == "on_merge_ready") \
        else "never"


def section_gates(cfg):
    cats = cfg["hitl"]["categories"]
    print("\nAction gates (block = halt+ask · surface = do it but tell you · auto = silent):")
    for name, desc in cfgmod.CATEGORIES.items():
        print(f"  {name:18} {cats.get(name):8} — {desc}")
    if ask_yn("Accept these gate levels?", default=True):
        return
    print("Name a category to change (blank when done).")
    while True:
        name = ask_str("  category", "")
        if name == "":
            break
        if name not in cfgmod.CATEGORIES:
            print(f"    unknown category. one of: {', '.join(cfgmod.CATEGORIES)}")
            continue
        cats[name] = ask_choice(f"  level for {name}", cfgmod.LEVELS, cats[name])


def section_review(cfg):
    review = cfg["review"]
    on = ask_yn("Enable automated code review after an issue is finished?",
                default=review.get("enabled") and review.get("scope") != "none")
    if not on:
        review["enabled"] = False
        review["scope"] = "none"
        return
    review["enabled"] = True
    review["scope"] = ask_choice(
        "Review which issues? (hard = risky only, both = all, easy = trivial only)",
        ("hard", "both", "easy"), review.get("scope") if review.get("scope") in
        ("hard", "both", "easy") else "hard")
    aa = review.setdefault("auto_approve", {})
    aa["min_confidence"] = ask_int(
        "Auto-approve below-the-bar needs a human; reviewer confidence to skip that",
        aa.get("min_confidence", 90))


def section_qa(cfg):
    qa = cfg["qa"]
    mode = ask_choice(
        "End-of-ticket QA pass? (suggest = on bigger tickets, all = every ticket, off)",
        ("suggest", "all", "off"), qa.get("mode") if qa.get("mode") in
        ("suggest", "all", "off") else "suggest")
    if mode == "off":
        qa["enabled"] = False
        qa["mode"] = "off"
    else:
        qa["enabled"] = True
        qa["mode"] = mode


def _ask_model(label, provider, default=None):
    if provider == "claude":
        d = default if default in cfgmod.CLAUDE_MODEL_ALIASES else "sonnet"
        return ask_choice(label + "model", cfgmod.CLAUDE_MODEL_ALIASES, d)
    # codex: enumerate from the local cache when available, else free-type the slug
    models = cfgmod.list_codex_models()
    if models:
        d = default if default in models else models[0]
        return ask_choice(label + "codex model", models, d)
    return ask_str(label + "codex model slug", default or "gpt-5.5")


def _ask_effort(label, provider, model, default="high"):
    if provider == "codex":
        levels = cfgmod.codex_reasoning_levels(model)
        if levels:
            return ask_choice(label + "effort", levels,
                              default if default in levels else levels[0])
    choices = ("low", "medium", "high")
    return ask_choice(label + "effort", choices, default if default in choices else "high")


def _ask_spec(label, providers, default_provider="claude", default_model=None,
              default_effort="high"):
    """Prompt for one {provider, config_dir, model, effort} spec. When only one
    provider is allowed, don't bother asking for it."""
    if len(providers) == 1:
        provider = providers[0]
    else:
        dp = default_provider if default_provider in providers else providers[0]
        provider = ask_choice(label + "provider", providers, dp)
    model = _ask_model(label, provider, default_model)
    effort = _ask_effort(label, provider, model, default_effort)
    return {"provider": provider, "config_dir": None, "model": model, "effort": effort}


def _fill_single_provider(cfg, provider):
    """Codex-only or Claude-only (after declining the Claude default): one model for
    everything, or a per-function/difficulty pick. No fallbacks (single provider)."""
    if ask_yn(f"  Use one {provider} model for every job?", default=True):
        spec = _ask_spec("  ", (provider,))
        for fn in cfgmod.ALL_FUNCTIONS:
            cfg["runner"]["agents"][fn] = {"easy": [dict(spec)], "hard": [dict(spec)]}
        return
    for fn in ("implement", "review", "qa"):
        for diff in ("easy", "hard"):
            cur = cfgmod.agent_main(cfg, fn, diff)
            spec = _ask_spec(f"    {fn}/{diff} ", (provider,),
                             default_model=cur.get("model"),
                             default_effort=cur.get("effort", "high"))
            cfg["runner"]["agents"][fn][diff] = [spec]
    # keep respec in sync with the chosen provider (don't strand a Claude default)
    impl_hard = cfg["runner"]["agents"]["implement"]["hard"][0]
    cfg["runner"]["agents"]["respec"] = {"easy": [dict(impl_hard)], "hard": [dict(impl_hard)]}


def _fill_multi_provider(cfg):
    """Both providers in play: per-function/difficulty primary pick + optional
    ordered fallback chain (tried when the primary is unavailable)."""
    print("  Pick the agent per function + difficulty; skip a function to keep its "
          "Claude default. You can add fallbacks for each.")
    providers = cfgmod.AGENT_PROVIDERS
    for fn in ("implement", "review", "qa"):
        if not ask_yn(f"  configure agents for '{fn}'?", default=False):
            continue
        for diff in ("easy", "hard"):
            cur = cfgmod.agent_main(cfg, fn, diff)
            chain = [_ask_spec(f"    {fn}/{diff} ", providers,
                               default_provider=cur.get("provider", "claude"),
                               default_model=cur.get("model"),
                               default_effort=cur.get("effort", "high"))]
            while ask_yn(f"    add a fallback for {fn}/{diff} "
                         "(tried if the one above is unavailable)?", default=False):
                chain.append(_ask_spec(f"    {fn}/{diff} fallback#{len(chain)} ", providers))
            cfg["runner"]["agents"][fn][diff] = chain


def section_agents(cfg):
    use_claude = ask_yn("Use Claude for coding jobs?", default=True)
    use_codex = ask_yn("Use Codex (OpenAI) for coding jobs?", default=False)
    if not use_claude and not use_codex:
        print("  No provider chosen — keeping the all-Claude default "
              "(opus for hard work, sonnet for easy).")
        return
    if use_claude and not use_codex:
        if ask_yn("  Use the Claude default (opus for hard work, sonnet for easy)?",
                  default=True):
            return
        _fill_single_provider(cfg, "claude")
        return
    if use_codex and not use_claude:
        _fill_single_provider(cfg, "codex")
        return
    _fill_multi_provider(cfg)


def section_cr(cfg, fresh):
    cr = cfg["cr"]
    # fresh setup leans ON (the feature is inert until the runner sees a CR anyway);
    # a reconfigure keeps the user's current value as the default.
    default = True if fresh else cr.get("enabled", True)
    cr["enabled"] = ask_yn(
        "Enable spec-change requests (file a request to CHANGE the spec; the runner "
        "pauses, asks, plans, regenerates on approval)?", default=default)


def section_advanced(cfg):
    if not ask_yn("Tune advanced runner knobs (loop interval, retry cooldown)?",
                  default=False):
        return
    runner = cfg["runner"]
    runner["interval"] = ask_int("  loop interval (seconds)", runner.get("interval", 45))
    runner["retry_delay_minutes"] = ask_int(
        "  retry cooldown after a failed run (minutes)",
        runner.get("retry_delay_minutes", 30))


# --------------------------------------------------------------------------- #
# interactive driver
# --------------------------------------------------------------------------- #
def run_interactive(root):
    existing = load_existing(root)
    fresh = existing is None
    if existing is not None:
        cfg = existing
        # backfill any missing blocks from defaults so sections have something to edit
        base = cfgmod.default_config()
        for k, v in base.items():
            cfg.setdefault(k, v)
        print(f"Reconfiguring {config_file(root)} (your current values are the defaults).")
    else:
        cfg = cfgmod.default_config()
        print(f"Configuring a fresh repo at {target_root(root)}.")
    print("Press Enter to accept the shown default at any prompt.\n")

    while True:
        remote_state = section_backend(cfg, root)
        section_git(cfg)
        section_gates(cfg)
        section_review(cfg)
        section_qa(cfg)
        section_agents(cfg)
        section_cr(cfg, fresh)
        section_advanced(cfg)

        print_summary(cfg)
        errs = cfgmod.validate(cfg)
        if errs:
            print("\nConfig is not valid yet:")
            for e in errs:
                print("  ERROR: " + e)
            if ask_yn("Walk through the questions again?", default=True):
                continue
            print("Aborted — nothing written.")
            return 1
        if ask_yn("\nWrite this config?", default=True):
            break
        if not ask_yn("Walk through the questions again (n = abort)?", default=True):
            print("Aborted — nothing written.")
            return 1

    p = write_config(cfg, root)
    print(f"\nWrote {p}")
    if remote_state is not None:
        rp = write_remote(remote_state, root)
        print(f"Wrote {rp} (mirror state)")
    print("\nDone. Go back to your agent and continue — it finishes the technical setup "
          "(scripts, templates, CLAUDE.md) from here.")
    return 0


# --------------------------------------------------------------------------- #
# non-interactive driver (--defaults / --set / --show)
# --------------------------------------------------------------------------- #
def run_noninteractive(root, use_defaults, sets, show):
    cfg = load_existing(root)
    if show:
        resolved = cfg if cfg is not None else cfgmod.default_config()
        print(json.dumps(resolved, indent=2))
        return 0
    if use_defaults or cfg is None:
        cfg = cfgmod.default_config()
    try:
        apply_sets(cfg, sets)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    errs = cfgmod.validate(cfg)
    if errs:
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2
    p = write_config(cfg, root)
    print(f"wrote {p}")
    backend = cfg.get("backend") or {}
    if backend.get("enabled"):
        if load_remote(root) is None:
            existing_repo = None
            rp = write_remote(_remote_default_state(repo=existing_repo), root)
            print(f"wrote {rp} (mirror state — set the repo with the interactive run or "
                  f"edit remote.json)")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        description="Interactive technical setup for specseed (writes config.json).")
    ap.add_argument("--root", default=None,
                    help="target repo root (default: current directory)")
    ap.add_argument("--show", action="store_true",
                    help="print the resolved config and exit (no prompts, no write)")
    ap.add_argument("--defaults", action="store_true",
                    help="write the default config with no prompts")
    ap.add_argument("--set", dest="sets", action="append", default=[],
                    metavar="KEY=VALUE",
                    help="dotted-key override (repeatable); implies non-interactive")
    args = ap.parse_args(argv)

    if args.show or args.defaults or args.sets:
        return run_noninteractive(args.root, args.defaults, args.sets, args.show)
    return run_interactive(args.root)


if __name__ == "__main__":
    sys.exit(main())
