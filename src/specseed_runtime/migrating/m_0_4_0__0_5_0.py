"""
m_0_4_0__0_5_0.py - force a re-seed so new label vocabulary lands on old targets.

0.5.0 adds ``type:<kind>`` (feature/bug/chore/spike/qa) and ``difficulty:<level>``
(easy/hard) to the tracker label set (``supported_values`` / ``populate_defaults``).
A target that was already seeded skips re-seeding on later launches (the
``storage/seed_state.json`` marker keyed to (kind, repo)), so without this hop its
remote would never gain the new labels. Deleting the marker makes the next startup
re-run ``ensure_remote_seeded``, which ensures the full label set (prune=False, so no
user labels are removed).

Idempotent: a missing marker is a no-op; ``storage/`` data and ``spec/`` are untouched.
Only Python stdlib is used.
"""

from __future__ import annotations

from pathlib import Path

FROM = "0.4.0"
TO = "0.5.0"

_SEED_MARKER = "seed_state.json"


def run(storage: str | Path, specseed_dir: str | Path) -> list[Path]:
    """Apply the hop. Returns the marker path if it was deleted, else []."""
    marker = Path(storage) / _SEED_MARKER
    if marker.exists():
        marker.unlink()
        return [marker]
    return []
