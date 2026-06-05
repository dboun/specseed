"""specseed runtime package.

The spec+work engine. Lives at ``<repo>/src/specseed_runtime`` and runs against a
target repo passed in - it is never copied into the target. Entry points put
``<repo>/src`` on ``sys.path`` (the launcher, the per-script bootstrap, tests'
conftest), so submodules import as ``specseed_runtime.<...>``.

Only Python stdlib is used.
"""

from __future__ import annotations
