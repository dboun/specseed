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

FROM = "0.19.0"
TO = "0.20.0"

# Old engine-generated guardrail bodies began with this heading. Files still starting
# with it are default-shaped (no user edits) and safe to retire.
_OLD_GUARDRAIL_PREFIX = "# specseed agent rules ("
_OLD_GUARDRAIL_FILES = (
    "AGENTS_INSTRUCTIONS_IMPL.md",
    "AGENTS_INSTRUCTIONS_SPEC.md",
    "AGENTS_INSTRUCTIONS_REVIEW.md",
)
_SEED_MARKER = "seed_state.json"


def run(storage: str | Path, specseed_dir: str | Path) -> None:
    """Retire stale guardrail bodies + force a label re-seed.

    Operates only on the data root (``storage``). The router-block strip from the
    target's CLAUDE.md/AGENTS.md moved to ``relocate.py`` in 0.21 (only relocation
    knows the target); stub seeding moved to the 0.21 hop + startup scaffold (the new
    ``instructions/<route>/`` layout). Pre-0.21 data reaches here FLAT (post-
    relocation), so old guardrail stubs + the seed marker sit directly under storage.
    """
    storage = Path(storage)

    # Retire old engine-generated guardrail bodies (now flat in the data root). The
    # 0.21 reshape moves any remaining (user-owned) stubs into instructions/<route>/.
    for fname in _OLD_GUARDRAIL_FILES:
        p = storage / fname
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if body.lstrip().startswith(_OLD_GUARDRAIL_PREFIX):
            try:
                p.unlink()
            except OSError:
                pass

    # Re-seed labels on next startup so the new `ask` label lands (question -> ask).
    marker = storage / _SEED_MARKER
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass
