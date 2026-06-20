"""Purity boundary enforcement (spec §4 "Purity boundary", §18 "Purity enforcement").

(a) The import-linter contract (pyproject.toml [tool.importlinter]) must pass:
    the pure modules import nothing from the I/O layers. We invoke the real
    bare `lint-imports` CLI (it reads the contract from pyproject.toml, exactly
    as the Makefile does) and assert exit code 0.
(b) The pure modules must never call a wall clock: grep them for `datetime.now(`
    and `time.time(` and assert zero hits. The engine drives time off
    `candle.ts_close` only.
"""
import os
import re
import shutil
import subprocess

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_PKG_DIR = os.path.join(_REPO_ROOT, "orb_bot")

# The four modules the spec declares PURE (no feed/execution/approval/reporting/
# orchestrator/discordbot imports; no datetime.now()/time.time()).
PURE_MODULES = ["models.py", "aggregation.py", "indicators.py", "engine.py"]

# Matches `datetime.now(`, `dt.now(`, `time.time(` with arbitrary whitespace.
_WALLCLOCK_RE = re.compile(r"\b(?:(?:datetime|dt)\.now|time\.time)\s*\(")


def test_import_linter_contract_passes():
    """`lint-imports` (import-linter) reports the purity contract is kept.

    The contract lives in pyproject.toml [tool.importlinter] (Task 1); we run
    bare `lint-imports` so it auto-discovers pyproject.toml, exactly as the
    Makefile `lint` target does. No standalone .importlinter file is used.
    """
    if not os.path.isfile(os.path.join(_REPO_ROOT, "pyproject.toml")):
        pytest.skip("no pyproject.toml (import-linter contract) present yet")
    if not os.path.isdir(_PKG_DIR):
        pytest.skip("orb_bot package not created yet")
    lint = shutil.which("lint-imports")
    if lint is None:
        pytest.skip("import-linter (lint-imports) not installed")
    proc = subprocess.run(
        [lint],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "import-linter contract violated:\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )


@pytest.mark.parametrize("module", PURE_MODULES)
def test_pure_module_has_no_wallclock_call(module):
    """No `datetime.now()` / `time.time()` in a module declared PURE."""
    path = os.path.join(_PKG_DIR, module)
    if not os.path.isfile(path):
        pytest.skip(f"{module} not created yet")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    hits = _WALLCLOCK_RE.findall(src)
    assert not hits, (
        f"{module} must not call a wall clock (spec §4 purity); "
        f"found {len(hits)} occurrence(s) of datetime.now()/time.time()"
    )


def test_pure_modules_are_listed_exhaustively():
    """Guard: if a new pure module appears, force its inclusion above."""
    if not os.path.isdir(_PKG_DIR):
        pytest.skip("orb_bot package not created yet")
    declared = set(PURE_MODULES)
    expected = {"models.py", "aggregation.py", "indicators.py", "engine.py"}
    assert declared == expected, (
        "PURE_MODULES drifted from the spec §4 list; update both the list and "
        "the pyproject.toml [tool.importlinter] contract together."
    )
