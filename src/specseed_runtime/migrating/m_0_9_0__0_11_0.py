"""
m_0_9_0__0_11_0.py - force a re-seed so the ``awaiting_input`` label lands on old targets.

0.11.0 splits the spec-change parked state: ``spec-change:status:awaiting_input``
(worker asked a clarification question) vs ``spec-change:status:awaiting_approval``
(APR-NNNN plan gate). A target that was already seeded skips re-seeding on later
launches (the ``storage/seed_state.json`` marker), so without this hop its remote
would never gain the new label. Deleting the marker makes the next startup re-run
``ensure_remote_seeded`` (prune=False, so no user labels are removed).

Requests already parked ``awaiting_approval`` by an old question round need no data
migration: that status still wakes on a comment.

Idempotent: a missing marker is a no-op; ``storage/`` data and ``spec/`` are untouched.
Only Python stdlib is used.
"""

from __future__ import annotations

from pathlib import Path

FROM = "0.9.0"
TO = "0.11.0"

_SEED_MARKER = "seed_state.json"


def run(storage: str | Path, specseed_dir: str | Path) -> list[Path]:
    """Apply the hop. Returns the marker path if it was deleted, else []."""
    marker = Path(storage) / _SEED_MARKER
    if marker.exists():
        marker.unlink()
        return [marker]
    return []
