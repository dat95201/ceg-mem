"""Isolate the tests from each other's monkeypatching.

Several test modules replace attributes of the project's modules in place
(`vo.check_usable = ...`, `loop.propose = ...`) and never restore them - fine
for a file run on its own (`python3 tests/test_x.py`), but under one pytest
process a later test then sees the previous test's stand-ins and fails on an
unrelated line. This fixture snapshots the globals of every project module
before each test and restores them afterwards, so each test starts from the
modules as imported.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

# The modules the tests patch, imported HERE - before any test module is
# collected - so the pristine state is captured. tests/test_free_guarded_rounds.py
# patches src.loop at import time (its checks run at module level), which is
# after conftest but before the first test; a snapshot taken at test start would
# already hold its fakes.
import scripts.validate_oracle  # noqa: E402,F401
import src.adapter              # noqa: E402,F401
import src.llm                  # noqa: E402,F401
import src.loop                 # noqa: E402,F401
import src.memory               # noqa: E402,F401
import src.oracle               # noqa: E402,F401
import src.proposer             # noqa: E402,F401


def _project_modules() -> list:
    out = []
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            path = pathlib.Path(f).resolve()
        except OSError:
            continue
        if ROOT in path.parents and ".venv" not in path.parts and "tests" not in path.parts:
            out.append(mod)
    return out


# module -> its globals as first seen (at conftest import for the modules above,
# at first test start for anything imported later).
_PRISTINE: dict[int, tuple] = {id(m): (m, dict(m.__dict__)) for m in _project_modules()}


def _restore(snapshot: dict) -> None:
    for mod, saved in snapshot.values():
        current = mod.__dict__
        for key in list(current):
            if key not in saved:
                del current[key]
        current.update(saved)


@pytest.fixture(autouse=True)
def _restore_project_module_globals():
    for m in _project_modules():
        _PRISTINE.setdefault(id(m), (m, dict(m.__dict__)))
    _restore(_PRISTINE)      # every test starts from the modules as imported
    yield
    _restore(_PRISTINE)
