"""pytest bootstrap: put the engine's ``src/`` on ``sys.path``.

Tests import the engine as ``specseed_runtime.<...>``. The package lives at
``<repo>/src/specseed_runtime``, so ``<repo>/src`` goes on the path here (this
file sits at the repo root, where pytest's rootdir is).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
