"""0.20.0: drop the target-root CLAUDE.md/AGENTS.md router block + reshape guardrails.

0.20.0 stops depending on a target-root CLAUDE.md/AGENTS.md. The skill bundle
(``generate_prompt_from_skill``) now tells the agent which
``<specseed_dir>/AGENTS_INSTRUCTIONS_<ROUTE>.md`` / ``CUSTOM_INSTRUCTIONS_<ROUTE>.md`` to
read. So on an already-configured target:

1. Remove the specseed router block (the ``<!-- specseed:router:start -->`` ...
   ``<!-- specseed:router:end -->`` span) from repo-root CLAUDE.md AND AGENTS.md. The
   FILES and any surrounding user content stay - we delete only our block.
2. The per-route ``AGENTS_INSTRUCTIONS_*`` guardrails are now EMPTY user-owned stubs (the
   spec-read order etc. moved into the skill). Retire any file still holding the old
   engine-generated body, then reseed empty stubs and add the new ASK-route stubs (both
   guardrail + custom), create-if-absent so user edits survive.
3. The ``question`` label became ``ask`` (the read-only Q&A route). Delete
   ``seed_state.json`` so the next startup re-seeds labels and the new ``ask`` label lands
   on already-seeded targets (prune=False; the old ``question`` label is left as-is - it
   was never wired to anything).

Idempotent: a second run finds no router markers and no old-shaped guardrails, the stubs
already exist, and a missing seed marker is a no-op. Only Python stdlib + scaffold helpers.
"""

from __future__ import annotations

from pathlib import Path

from specseed_runtime.configuring import scaffold

FROM = "0.19.0"
TO = "0.20.0"

# Historical markers - frozen here; scaffold no longer defines them.
_ROUTER_START = "<!-- specseed:router:start -->"
_ROUTER_END = "<!-- specseed:router:end -->"

# Old engine-generated guardrail bodies began with this heading. Files still starting
# with it are default-shaped (no user edits) and safe to retire.
_OLD_GUARDRAIL_PREFIX = "# specseed agent rules ("
_OLD_GUARDRAIL_FILES = (
    "AGENTS_INSTRUCTIONS_IMPL.md",
    "AGENTS_INSTRUCTIONS_SPEC.md",
    "AGENTS_INSTRUCTIONS_REVIEW.md",
)
_SEED_MARKER = "seed_state.json"


def _strip_router_block(text: str) -> str:
    """Remove the delimited specseed router block and tidy the gap. Idempotent."""
    if _ROUTER_START not in text or _ROUTER_END not in text:
        return text
    head, _, rest = text.partition(_ROUTER_START)
    _, _, tail = rest.partition(_ROUTER_END)
    trailing = "\n" if text.endswith("\n") else ""
    head = head.rstrip("\n")
    tail = tail.lstrip("\n")
    if head and tail:
        return head + "\n\n" + tail + trailing
    return (head or tail) + trailing


def run(storage: str | Path, specseed_dir: str | Path) -> None:
    storage = Path(storage)
    specseed_dir = Path(specseed_dir)
    repo_root = specseed_dir.parent

    # 1. strip the router block from repo-root CLAUDE.md / AGENTS.md (keep the files)
    for fname in ("CLAUDE.md", "AGENTS.md"):
        p = repo_root / fname
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        new = _strip_router_block(text)
        if new != text:
            try:
                p.write_text(new, encoding="utf-8")
            except OSError:
                pass

    # 2. retire old engine-generated guardrail bodies (reseeded empty below)
    for fname in _OLD_GUARDRAIL_FILES:
        p = specseed_dir / fname
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if body.lstrip().startswith(_OLD_GUARDRAIL_PREFIX):
            try:
                p.unlink()
            except OSError:
                pass

    # seed any missing guardrail + custom stubs (incl. the new ASK route), create-if-absent
    scaffold.write_instruction_files(repo_root, specseed_dir)
    scaffold.write_custom_instruction_stubs(repo_root, specseed_dir)

    # 3. re-seed labels on next startup so the new `ask` label lands (question -> ask)
    marker = storage / _SEED_MARKER
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass
