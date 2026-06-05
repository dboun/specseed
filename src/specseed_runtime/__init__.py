"""Specseed target-runtime package with dev/install import compatibility.

In target repos, these files live as ``<repo>/.specseed/specseed_target_src`` and
import each other as ``specseed_target_src``. In this development repo, tests and
tools may still import the same files as ``src.target_facing.specseed_target_src``.
Both spellings must resolve to the same module objects, otherwise classes loaded
from each spelling stop matching each other.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from pathlib import Path


CANONICAL = "specseed_target_src"
DEV_PREFIX = "src.target_facing.specseed_target_src"


def _ensure_package_parent_on_path() -> None:
    package_parent = Path(__file__).resolve().parent.parent
    if str(package_parent) not in sys.path:
        sys.path.insert(0, str(package_parent))


class _DevImportAlias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Redirect dev-path submodules to the canonical installed-path modules."""

    marker = "_specseed_dev_import_alias"

    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001 - import hook API
        if fullname == DEV_PREFIX or not fullname.startswith(DEV_PREFIX + "."):
            return None
        canonical_name = CANONICAL + fullname[len(DEV_PREFIX):]
        canonical_spec = importlib.util.find_spec(canonical_name)
        if canonical_spec is None:
            return None
        return importlib.machinery.ModuleSpec(
            fullname,
            self,
            is_package=canonical_spec.submodule_search_locations is not None,
        )

    def create_module(self, spec):  # noqa: ANN001 - import hook API
        canonical_name = CANONICAL + spec.name[len(DEV_PREFIX):]
        module = importlib.import_module(canonical_name)
        sys.modules[spec.name] = module
        return module

    def exec_module(self, module) -> None:  # noqa: ANN001 - import hook API
        return None


def _install_dev_alias_hook() -> None:
    if any(getattr(finder, _DevImportAlias.marker, False) for finder in sys.meta_path):
        return
    finder = _DevImportAlias()
    setattr(finder, _DevImportAlias.marker, True)
    sys.meta_path.insert(0, finder)


_ensure_package_parent_on_path()
_install_dev_alias_hook()

if __name__ == CANONICAL:
    sys.modules.setdefault(DEV_PREFIX, sys.modules[__name__])
else:
    canonical = importlib.import_module(CANONICAL)
    sys.modules[__name__] = canonical
