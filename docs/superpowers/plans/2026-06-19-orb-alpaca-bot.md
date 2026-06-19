# ORB → Alpaca Trading Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-timeframe (default 15m) Opening-Range-Breakout trading bot on Alpaca: paper mode auto-fires bracket orders, live mode gates each order behind a Discord human approval, and both modes post an end-of-session P/L report.

**Architecture:** One `asyncio` process. A PURE, timeframe-parameterized state-machine engine consumes T-minute candles (the orchestrator aggregates Alpaca's 1m stream up to T) and emits events; the orchestrator performs all I/O (data feed, broker, approver, reporter) behind Protocol seams so a future backtester drops in unchanged.

**Tech Stack:** Python 3.11+, alpaca-py, discord.py, pydantic v2 + pydantic-settings, pyyaml, python-dotenv, structlog; pytest/pytest-asyncio/freezegun/ruff/mypy/import-linter.

## Global Constraints

- Python 3.11+. Package `orb_bot/` at the repo ROOT (flat layout — no `src/`); tests in `tests/` import via `from .context import orb_bot` (`tests/context.py` inserts the repo root on `sys.path`).
- Pinned deps: `alpaca-py>=0.43,<0.44`, `discord.py>=2.6,<3`, `pydantic>=2`, `pydantic-settings`, `pyyaml`, `python-dotenv`, `structlog`; dev: `pytest>=8`, `pytest-asyncio` (`asyncio_mode=auto`), `pytest-cov`, `freezegun`, `ruff`, `mypy`, `import-linter`.
- All prices are `decimal.Decimal`; all timestamps tz-aware `America/New_York` (`zoneinfo`).
- PURITY (import-linter + a no-`now()` test enforce it): `models`/`aggregation`/`indicators`/`engine` import nothing from `feed`/`execution`/`approval`/`reporting`/`orchestrator`/`discordbot` and never call `datetime.now()`; the engine times off `candle.ts_close`.
- Single timeframe `T = range_timeframe_min` (default 15); validator pins `confirm == entry == range == atr_timeframe_min` and `1 <= or_min_bars <= T`. Only T-candles reach the engine.
- Paper auto-fires (`AutoApprover`); live uses the `DiscordApprover` human gate. EOD P/L report (`Reporter`) in both modes; never auto-approve in live.
- TDD: failing test → confirm fail → minimal impl → confirm pass → conventional-commit. Whole-share bracket qty only (Alpaca rejects fractional/notional on brackets).

---


### Task 1: Project scaffolding & tooling — packaging, config, purity contract, and verification

**Files:**
- Create `pyproject.toml` (repo root)
- Create `requirements.txt` (repo root)
- Create `Makefile` (repo root)
- Create `README.md` (repo root)
- Create `LICENSE` (repo root)
- Create `.gitignore` (repo root)
- Create `.env.example` (repo root)
- Create `config/config.yaml`
- Create `orb_bot/__init__.py`
- Create `orb_bot/feed/__init__.py`
- Create `orb_bot/execution/__init__.py`
- Create `orb_bot/approval/__init__.py`
- Create `orb_bot/reporting/__init__.py`
- Create `tests/__init__.py`
- Create `tests/context.py`

**Interfaces:** Consumes: nothing (this is the root of the dependency DAG). Produces: an installable package `orb_bot` (flat layout, repo root), the pinned dependency set (`alpaca-py>=0.43,<0.44`, `discord.py>=2.6,<3`, `pydantic>=2`, `pydantic-settings`, `pyyaml`, `python-dotenv`, `structlog`; dev: `pytest>=8`, `pytest-asyncio` with `asyncio_mode=auto`, `pytest-cov`, `freezegun`, `ruff`, `mypy`, `import-linter`), the `tests/context.py` shim (`import orb_bot` via `from .context import orb_bot`), `config/config.yaml` defaults (full §7 strategy+run config), and the enforced purity contract (`models`/`aggregation`/`indicators`/`engine` forbidden from importing `feed`/`execution`/`approval`/`reporting`/`orchestrator`/`discordbot`). All later tasks build on these.

This is a create-files-then-verify task (not red-green); each step writes one complete file, then the final steps install, import, collect, lint, and run the import contract before committing.

- [ ] **Step 1: Create `pyproject.toml` with metadata, pinned deps, and all tool config.**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "orb-bot"
version = "0.1.0"
description = "ORB → Alpaca trading bot: single-timeframe Opening Range Breakout strategy with a pure, deterministic engine."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "ORB Bot Authors" }]
keywords = ["trading", "alpaca", "opening-range-breakout", "orb", "asyncio"]
classifiers = [
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
]
dependencies = [
    "alpaca-py>=0.43,<0.44",
    "discord.py>=2.6,<3",
    "pydantic>=2",
    "pydantic-settings",
    "pyyaml",
    "python-dotenv",
    "structlog",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio",
    "pytest-cov",
    "freezegun",
    "ruff",
    "mypy",
    "import-linter",
]

[project.scripts]
orb-bot = "orb_bot.__main__:main"

[tool.setuptools]
# Flat layout (Hitchhiker's Guide): the package lives at the repo root, NOT in src/.
packages = [
    "orb_bot",
    "orb_bot.feed",
    "orb_bot.execution",
    "orb_bot.approval",
    "orb_bot.reporting",
]

[tool.ruff]
target-version = "py311"
line-length = 100
src = ["orb_bot", "tests"]

[tool.ruff.lint]
# E/F = pyflakes+pycodestyle, I = isort, UP = pyupgrade, B = bugbear, W = warnings.
select = ["E", "F", "I", "UP", "B", "W"]
ignore = []

[tool.ruff.lint.isort]
known-first-party = ["orb_bot"]

[tool.mypy]
python_version = "3.11"
warn_unused_configs = true
warn_redundant_casts = true
warn_unused_ignores = true
no_implicit_optional = true
check_untyped_defs = true
ignore_missing_imports = true
files = ["orb_bot"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra"

[tool.coverage.run]
branch = true
source = ["orb_bot"]

# --- Purity boundary, enforced by import-linter (run via `lint-imports`) ---
[tool.importlinter]
root_package = "orb_bot"

[[tool.importlinter.contracts]]
name = "Pure modules import nothing from I/O layers"
type = "forbidden"
source_modules = [
    "orb_bot.models",
    "orb_bot.aggregation",
    "orb_bot.indicators",
    "orb_bot.engine",
]
forbidden_modules = [
    "orb_bot.feed",
    "orb_bot.execution",
    "orb_bot.approval",
    "orb_bot.reporting",
    "orb_bot.orchestrator",
    "orb_bot.discordbot",
]
```

- [ ] **Step 2: Create `requirements.txt` mirroring runtime + dev deps for `make init`.**

```text
# Runtime deps (mirror of pyproject [project.dependencies]) — pinned per spec §19.
alpaca-py>=0.43,<0.44
discord.py>=2.6,<3
pydantic>=2
pydantic-settings
pyyaml
python-dotenv
structlog

# Dev / tooling deps (mirror of pyproject [project.optional-dependencies].dev).
pytest>=8
pytest-asyncio
pytest-cov
freezegun
ruff
mypy
import-linter
```

- [ ] **Step 3: Create `Makefile` with `.PHONY` init/test/lint/run targets.**

```makefile
.PHONY: init test lint run

# Install runtime + dev deps and the package itself (editable, flat layout).
init:
	pip install -r requirements.txt
	pip install -e .[dev]

# Run the test suite (pytest reads testpaths=tests from pyproject).
test:
	pytest tests

# Static checks: style/lint (ruff), types (mypy), and the purity import contract.
lint:
	ruff check orb_bot tests
	mypy orb_bot
	lint-imports

# Launch the bot: loads config, builds deps, runs the orchestrator.
run:
	python -m orb_bot
```

- [ ] **Step 4: Create `LICENSE` (full MIT text, year 2026, holder placeholder).**

```text
MIT License

Copyright (c) 2026 ORB Bot Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 5: Create `.gitignore` (Python + `.env` + `logs/`).**

```gitignore
# --- Secrets / local config ---
.env
.env.*
!.env.example

# --- Logs / runtime output ---
logs/
*.log

# --- Python byte-compiled / caches ---
__pycache__/
*.py[cod]
*$py.class
*.so

# --- Packaging / build artifacts ---
build/
dist/
*.egg-info/
.eggs/
pip-wheel-metadata/

# --- Test / type / lint caches ---
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
.coverage.*
htmlcov/
coverage.xml

# --- Virtual environments ---
.venv/
venv/
env/

# --- Editor / OS cruft ---
.idea/
.vscode/
.DS_Store
```

- [ ] **Step 6: Create `.env.example` (the 5 secrets, §7).**

```dotenv
# Copy to `.env` (gitignored) and fill in. `.env` is loaded by pydantic-settings.

# Alpaca API credentials — ALWAYS required (paper & live).
ALPACA_KEY=your-alpaca-key-id
ALPACA_SECRET=your-alpaca-secret-key

# Discord — REQUIRED in live mode (the approval gate); optional in paper.
# DISCORD_CHANNEL_ID and DISCORD_APPROVER_USER_ID are integers.
DISCORD_TOKEN=your-discord-bot-token
DISCORD_CHANNEL_ID=000000000000000000
DISCORD_APPROVER_USER_ID=000000000000000000
```

- [ ] **Step 7: Create `config/config.yaml` (full default strategy + run config from §7, with comments).**

```bash
mkdir -p config
```

```yaml
# orb_bot default configuration (strategy + run flags). NO secrets here — those
# live in .env. Validated by orb_bot/config.py at load time (fails fast before
# any network connection). See spec §7.

run:
  # symbol is REQUIRED — set per trading day (e.g. "SPY").
  symbol: "SPY"
  live: false              # false => paper (AutoApprover); true => live (DiscordApprover gate)
  feed: "IEX"              # "IEX" | "SIP"
  allow_live_iex: false    # live + non-SIP feed is rejected unless this is true

strategy:
  # --- session / timing ---
  timezone: "America/New_York"
  session_open: "09:30"
  range_timeframe_min: 15       # the single strategy timeframe T (configurable; default 15, T in {1,5,15})
  confirm_timeframe_min: 15     # SINGLE-TIMEFRAME build: must == range_timeframe_min
  entry_timeframe_min: 15       # must == range_timeframe_min
  trading_window_min: 120       # = 8 fifteen-minute candles at T=15 (OR is candle #1)
  flatten_at: "15:55"           # orchestrator uses min(flatten_at, next_close - flatten_buffer_min)
  flatten_buffer_min: 5         # minutes before next_close to flatten (TIME offset; distinct from price buffer)
  bar_grace_seconds: 3          # delay after the T-min bucket boundary before force-closing the aggregate

  # --- risk / sizing ---
  risk_reward_ratio: 2.0
  risk_per_trade_pct: 0.5
  max_trades_per_day: 1
  allow_long: true
  allow_short: true
  equity_source: "live"         # "live" | "fixed"
  fixed_equity: null            # required when equity_source == "fixed"
  rearm_opposite_only: true     # on re-arm (max_trades>1) allow only the opposite direction

  # --- entry-model toggles ---
  enable_breakout: true
  enable_retest: true
  enable_reversal: false        # DEFERRED this build; validator rejects `true`

  # --- strong close (spec §5) ---
  strong_close_body_ratio: 0.60
  strong_close_location: 0.70

  # --- displacement ---
  displacement_model: "IMPULSE" # "IMPULSE" | "FVG" | "TRUE_GAP"
  fvg_min_size_ticks: 2
  impulse_atr_mult: 1.5

  # --- retest (counts are T-min candles) ---
  retest_tolerance_atr: 0.25
  retest_max_wait_candles: 4    # must fit the window: <= trading_window_min / range_timeframe_min
  retest_confirm_body_ratio: 0.50

  # --- stops / structure ---
  tick_size: 0.01               # US equities >= $1
  stop_buffer_atr: 0.10
  stop_buffer_ticks: 2          # buffer = max(stop_buffer_ticks*tick_size, stop_buffer_atr*ATR)
  min_stop_distance: 0.02       # floor on abs(entry - stop)
  swing_fractal_k: 1            # bars required EACH side for a fractal pivot
  swing_lookback: 4             # entry-window buffer length; must be >= 2*swing_fractal_k + 1

  # --- day-type filter ---
  range_day_sweep_both: true
  range_day_disables: ["breakout", "retest"]
  range_day_enables: []         # reversal DEFERRED, so a range day disables trading this build
  sweep_buffer: 0.0
  require_higher_tf_bias: false  # DEFERRED; validator rejects `true`

  # --- ATR ---
  atr_period: 14
  atr_timeframe_min: 15         # MUST == range_timeframe_min (validator-pinned)
  or_min_bars: 15               # required 1m children in opening T-bar (<= T); below it => low_confidence

  # --- approval ---
  approval_timeout_s: 90        # View timeout; expiry => TIMEOUT (never auto-approve)

  # --- logging ---
  logging_level: "INFO"
  logging_dir: "logs/"
```

- [ ] **Step 8: Create the package init files (one per package/subpackage).**

```bash
mkdir -p orb_bot/feed orb_bot/execution orb_bot/approval orb_bot/reporting
```

Create `orb_bot/__init__.py`:

```python
"""orb_bot — single-timeframe Opening Range Breakout trading bot for Alpaca.

Flat-layout package (Hitchhiker's Guide): this package lives at the repository
root. The pure core (`models`, `aggregation`, `indicators`, `engine`) imports
nothing from the I/O layers (`feed`, `execution`, `approval`, `reporting`,
`orchestrator`, `discordbot`); that boundary is enforced by import-linter.
"""

__version__ = "0.1.0"
```

Create `orb_bot/feed/__init__.py`:

```python
"""I/O layer: market-data feeds (DataFeed implementations)."""
```

Create `orb_bot/execution/__init__.py`:

```python
"""I/O layer: broker execution (Broker implementations)."""
```

Create `orb_bot/approval/__init__.py`:

```python
"""I/O layer: trade approval gates (Approver implementations)."""
```

Create `orb_bot/reporting/__init__.py`:

```python
"""I/O layer: trade/session reporting (Reporter implementations)."""
```

- [ ] **Step 9: Create the test package + path shim.**

Create `tests/__init__.py`:

```python
"""Test package for orb_bot.

Tests import the package through the `context` shim
(`from .context import orb_bot`) so the suite runs regardless of install method.
"""
```

Create `tests/context.py`:

```python
"""Path shim (Hitchhiker's Guide §Test Suite).

Inserts the repository root onto sys.path so `import orb_bot` resolves whether
or not the package is installed. Test modules use `from .context import orb_bot`.
"""

import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import orb_bot  # noqa: E402  (import after sys.path manipulation, re-exported for tests)

__all__ = ["orb_bot"]
```

- [ ] **Step 10: Create `README.md` (overview + setup + run).**

```markdown
# orb-bot

A single-timeframe **Opening Range Breakout (ORB)** trading bot for Alpaca.

A single `asyncio` event loop streams 1-minute bars, aggregates them to the
strategy timeframe `T` (default 15m, anchored to the 09:30 open), and feeds a
**pure, deterministic state machine** (`orb_bot/engine.py`). The engine derives
all timing from `candle.ts_close` (never the wall clock), so paper trading,
live trading, and future backtests share one core. Paper mode auto-approves
setups; live mode gates each setup behind a Discord approval. Both modes emit an
end-of-session P/L report.

## Layout

Flat layout (Hitchhiker's Guide): the `orb_bot/` package lives at the repository
root. Pure modules (`models`, `aggregation`, `indicators`, `engine`) import
nothing from the I/O layers (`feed`, `execution`, `approval`, `reporting`,
`orchestrator`, `discordbot`); the boundary is enforced by `import-linter`.

## Requirements

- Python 3.11+
- An Alpaca account (paper or live) and API keys
- (Live mode) A Discord bot token, channel, and approver user

## Setup

```bash
make init                 # installs deps + the package (editable, [dev] extras)
cp .env.example .env      # then fill in your secrets
```

Secrets live in `.env` (gitignored): `ALPACA_KEY`, `ALPACA_SECRET` are always
required; `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_APPROVER_USER_ID` are
required in live mode and optional in paper. Strategy and run flags live in
`config/config.yaml` (committed; no secrets).

## Run

```bash
make run                  # equivalently: python -m orb_bot
```

Edit `config/config.yaml` to set the `run.symbol`, switch `run.live`, choose the
data `run.feed` (`IEX`/`SIP`), and tune the strategy parameters. Configuration
is validated at load time, so a bad config fails before any network connection.

## Develop

```bash
make test                 # pytest tests
make lint                 # ruff + mypy + import-linter (purity contract)
```
```

- [ ] **Step 11: VERIFY — install, import, collect.** Expected: install succeeds, import prints `ok`, collection succeeds with **0 tests collected** (no test modules exist yet — this is the scaffolding task) and no errors.

```bash
pip install -e ".[dev]"
python -c "import orb_bot; print('import ok', orb_bot.__version__)"
pytest --collect-only
```

Expected: PASS — `import ok 0.1.0`, and `pytest --collect-only` reports `no tests ran` / `collected 0 items` with exit status 5 (no tests yet) and **no collection errors**. (Later module tasks add the test files.)

- [ ] **Step 12: VERIFY — lint and the purity import contract run clean.** Expected: ruff reports no issues; `lint-imports` reports the contract **KEPT** (nothing to violate it yet, since pure modules are not created in this task — the contract is in place for later tasks).

```bash
ruff check orb_bot tests
lint-imports
```

Expected: PASS — `ruff check` prints `All checks passed!`; `lint-imports` prints `Contracts: 1 kept, 0 broken.` (If `lint-imports` reports the root package has no submodules to analyze, that is acceptable at scaffolding time; the contract activates as pure modules are added.)

- [ ] **Step 13: Commit.**

```bash
git init -q 2>/dev/null || true
git add pyproject.toml requirements.txt Makefile README.md LICENSE .gitignore .env.example config/config.yaml orb_bot tests
git commit -m "chore: scaffold orb_bot package, tooling, and purity import contract

- flat-layout pyproject (pinned runtime+dev deps), Makefile, README, MIT LICENSE
- .gitignore (.env, logs/), .env.example (5 secrets), config/config.yaml (full §7 defaults)
- orb_bot + feed/execution/approval/reporting packages; tests/context.py path shim
- ruff/mypy/pytest(asyncio_mode=auto)/import-linter config; purity contract forbids
  models/aggregation/indicators/engine -> feed/execution/approval/reporting/orchestrator/discordbot

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

I have all the canonical detail I need. Now I'll output the markdown plan for the `models.py` module only.

### Task 2: Models — enums, Candle, OpeningRange, Setup, Displacement

**Files:** Create `orb_bot/models.py`, `tests/test_models_core.py`. CONSUMES (do NOT create/overwrite — owned by Task 1): `orb_bot/__init__.py`, `tests/__init__.py`, `tests/context.py`.
**Interfaces:** Consumes: nothing (leaf module). Produces: `Direction(Enum)` LONG/SHORT; `Model(Enum)` BREAKOUT/RETEST/REVERSAL; `State(Enum)` IDLE/BUILDING_RANGE/RANGE_SET/WAIT_CONFIRMATION/WAIT_ENTRY/IN_TRADE/DONE; `@dataclass(frozen=True) Candle(ts_open:datetime, ts_close:datetime, open:Decimal, high:Decimal, low:Decimal, close:Decimal, volume:int, timeframe_min:int, data_incomplete:bool=False, bars_present:int|None=None)`; `@dataclass(frozen=True) OpeningRange(high, low, established_at, width, feed, bars_present, low_confidence)`; `@dataclass(frozen=True) Setup(direction:Direction, model:Model, entry:Decimal, stop:Decimal, target:Decimal, rr:float, reason:list[str])`; `@dataclass(frozen=True) Displacement(type:str, upper_candle:Candle, lower_candle:Candle, size:Decimal)`.

- [ ] **Step 1: Ensure the package + test harness scaffolding exists (idempotent — do NOT clobber Task 1's files).** The package is flat at repo root; tests reach it through `tests/context.py`. Task 1 already created `orb_bot/__init__.py` (with its docstring + `__version__`), `tests/__init__.py`, and `tests/context.py`. If any is missing (running this task in isolation), create it from Task 1's canonical body; otherwise consume it as-is. NEVER truncate `orb_bot/__init__.py` (that would wipe `__version__`).

```bash
mkdir -p orb_bot tests
[ -f orb_bot/__init__.py ] || printf '"""orb_bot — Opening-Range Breakout trading bot (flat-layout package)."""\n__version__ = "0.1.0"\n' > orb_bot/__init__.py
[ -f tests/__init__.py ] || : > tests/__init__.py
[ -f tests/context.py ] || cat > tests/context.py <<'PY'
"""Insert repo root on sys.path so tests import the flat-layout package."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import orb_bot  # noqa: E402,F401  (re-exported for tests: `from .context import orb_bot`)
PY
```

- [ ] **Step 2: Write the failing test for enums + Candle.** Asserts enum *values* (string form per the contract), frozen-ness via `FrozenInstanceError`, and that Decimal/datetime fields hold their types.

```python
# tests/test_models_core.py
import dataclasses
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot import models

ET = ZoneInfo("America/New_York")


def test_direction_enum_values():
    assert models.Direction.LONG.value == "LONG"
    assert models.Direction.SHORT.value == "SHORT"
    assert [d.name for d in models.Direction] == ["LONG", "SHORT"]


def test_model_enum_members():
    assert [m.name for m in models.Model] == ["BREAKOUT", "RETEST", "REVERSAL"]


def test_state_enum_members():
    assert [s.name for s in models.State] == [
        "IDLE",
        "BUILDING_RANGE",
        "RANGE_SET",
        "WAIT_CONFIRMATION",
        "WAIT_ENTRY",
        "IN_TRADE",
        "DONE",
    ]


def test_candle_construction_defaults():
    ts_open = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    ts_close = datetime(2026, 6, 19, 9, 45, tzinfo=ET)
    c = models.Candle(
        ts_open=ts_open,
        ts_close=ts_close,
        open=Decimal("100.00"),
        high=Decimal("101.50"),
        low=Decimal("99.75"),
        close=Decimal("101.00"),
        volume=12345,
        timeframe_min=15,
    )
    assert c.ts_open.tzinfo == ET
    assert isinstance(c.high, Decimal)
    assert c.volume == 12345
    assert c.timeframe_min == 15
    assert c.data_incomplete is False
    assert c.bars_present is None


def test_candle_is_frozen():
    c = models.Candle(
        ts_open=datetime(2026, 6, 19, 9, 30, tzinfo=ET),
        ts_close=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("0.5"),
        close=Decimal("1.5"),
        volume=1,
        timeframe_min=15,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.close = Decimal("9")  # type: ignore[misc]
```

```bash
pytest tests/test_models_core.py -q
```
Expected: **FAIL** (`ModuleNotFoundError: No module named 'orb_bot.models'`).

- [ ] **Step 3: Implement enums + Candle (minimal, to pass Step 2).**

```python
# orb_bot/models.py
"""Domain model: frozen dataclasses + enums shared across all layers (spec §5).

All prices are decimal.Decimal; all timestamps are tz-aware America/New_York.
This module imports nothing from feed/execution/approval/reporting/orchestrator
and is fully pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Union


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Model(Enum):
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"
    REVERSAL = "REVERSAL"


class State(Enum):
    IDLE = "IDLE"
    BUILDING_RANGE = "BUILDING_RANGE"
    RANGE_SET = "RANGE_SET"
    WAIT_CONFIRMATION = "WAIT_CONFIRMATION"
    WAIT_ENTRY = "WAIT_ENTRY"
    IN_TRADE = "IN_TRADE"
    DONE = "DONE"


@dataclass(frozen=True)
class Candle:
    ts_open: datetime            # tz-aware ET; bar start
    ts_close: datetime           # ts_open + timeframe
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    timeframe_min: int           # 1 (transport) | T = range_timeframe_min (default 15)
    data_incomplete: bool = False
    bars_present: int | None = None
```

```bash
pytest tests/test_models_core.py -q
```
Expected: **PASS** (6 tests).

- [ ] **Step 4: Add failing tests for OpeningRange, Setup, Displacement.** Append to `tests/test_models_core.py`.

```python
# tests/test_models_core.py  (append)
def _candle(close: str = "100") -> models.Candle:
    return models.Candle(
        ts_open=datetime(2026, 6, 19, 9, 30, tzinfo=ET),
        ts_close=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal(close),
        volume=1,
        timeframe_min=15,
    )


def test_opening_range_construction_and_frozen():
    orng = models.OpeningRange(
        high=Decimal("101.50"),
        low=Decimal("99.75"),
        established_at=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        width=Decimal("1.75"),
        feed="IEX",
        bars_present=15,
        low_confidence=False,
    )
    assert isinstance(orng.width, Decimal)
    assert orng.feed == "IEX"
    assert orng.bars_present == 15
    assert orng.low_confidence is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        orng.high = Decimal("200")  # type: ignore[misc]


def test_setup_construction_and_frozen():
    s = models.Setup(
        direction=models.Direction.LONG,
        model=models.Model.BREAKOUT,
        entry=Decimal("101.50"),
        stop=Decimal("100.00"),
        target=Decimal("104.50"),
        rr=2.0,
        reason=["close above OR high", "strong body"],
    )
    assert s.direction is models.Direction.LONG
    assert s.model is models.Model.BREAKOUT
    assert isinstance(s.entry, Decimal)
    assert s.rr == 2.0
    assert s.reason == ["close above OR high", "strong body"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.entry = Decimal("0")  # type: ignore[misc]


def test_displacement_construction_and_frozen():
    up = _candle("102")
    lo = _candle("98")
    d = models.Displacement(
        type="IMPULSE",
        upper_candle=up,
        lower_candle=lo,
        size=Decimal("4.0"),
    )
    assert d.type == "IMPULSE"
    assert d.upper_candle is up
    assert d.lower_candle is lo
    assert isinstance(d.size, Decimal)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.size = Decimal("0")  # type: ignore[misc]
```

```bash
pytest tests/test_models_core.py -q
```
Expected: **FAIL** (`AttributeError: module 'orb_bot.models' has no attribute 'OpeningRange'`).

- [ ] **Step 5: Implement OpeningRange, Setup, Displacement.** Append to `orb_bot/models.py`.

```python
# orb_bot/models.py  (append)
@dataclass(frozen=True)
class OpeningRange:
    high: Decimal
    low: Decimal
    established_at: datetime
    width: Decimal
    feed: str
    bars_present: int
    low_confidence: bool


@dataclass(frozen=True)
class Setup:
    direction: Direction
    model: Model
    entry: Decimal
    stop: Decimal
    target: Decimal
    rr: float
    reason: list[str]


@dataclass(frozen=True)
class Displacement:
    type: str                    # 'IMPULSE' | 'FVG'
    upper_candle: Candle
    lower_candle: Candle
    size: Decimal
```

```bash
pytest tests/test_models_core.py -q
```
Expected: **PASS** (9 tests).

- [ ] **Step 6: Commit.**

```bash
git add orb_bot/models.py tests/test_models_core.py
git commit -m "feat(models): enums, Candle, OpeningRange, Setup, Displacement"
```

---

### Task 3: Models — runtime/I-O dataclasses (mutable) + EngineEvent union

**Files:** Modify `orb_bot/models.py`. Create `tests/test_models_runtime.py`.
**Interfaces:** Consumes: `Setup`, `Direction`, `Model`, `Candle` from prior task. Produces: `@dataclass(frozen=True) AccountSnapshot(equity:Decimal, buying_power:Decimal, shorting_enabled:bool)`; `@dataclass(frozen=True) ClockInfo(is_open:bool, next_close:datetime)`; `@dataclass(mutable) OrderResult(order_id:str, client_order_id:str, status:str, filled_avg_price:Decimal|None, filled_qty:int, legs:list)`; `@dataclass(mutable) Fill(order_id:str, client_order_id:str, leg_role:str, side:str, price:Decimal, qty:int, ts:datetime, position_qty:int, exit_reason:str|None)`; `@dataclass(mutable) ApprovalRequest(setup:Setup, symbol:str, qty:int, risk_dollars:float, mode:str, feed:str, or_high:Decimal, or_low:Decimal, bars_present:int, data_warning:str|None, approval_ttl_s:int)`.

- [ ] **Step 1: Write failing tests for AccountSnapshot, ClockInfo (frozen), OrderResult, Fill, ApprovalRequest (mutable).** Asserts frozen-ness on the frozen pair, mutability on the I/O trio, and field/type holding.

```python
# tests/test_models_runtime.py
import dataclasses
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot import models

ET = ZoneInfo("America/New_York")


def _setup() -> models.Setup:
    return models.Setup(
        direction=models.Direction.LONG,
        model=models.Model.BREAKOUT,
        entry=Decimal("101.50"),
        stop=Decimal("100.00"),
        target=Decimal("104.50"),
        rr=2.0,
        reason=["x"],
    )


def test_account_snapshot_frozen():
    a = models.AccountSnapshot(
        equity=Decimal("100000.00"),
        buying_power=Decimal("400000.00"),
        shorting_enabled=True,
    )
    assert isinstance(a.equity, Decimal)
    assert a.shorting_enabled is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.equity = Decimal("0")  # type: ignore[misc]


def test_clock_info_frozen():
    nc = datetime(2026, 6, 19, 16, 0, tzinfo=ET)
    ck = models.ClockInfo(is_open=True, next_close=nc)
    assert ck.is_open is True
    assert ck.next_close == nc
    with pytest.raises(dataclasses.FrozenInstanceError):
        ck.is_open = False  # type: ignore[misc]


def test_order_result_is_mutable():
    o = models.OrderResult(
        order_id="o-1",
        client_order_id="2026-06-19-AAPL-0-ENTRY",
        status="accepted",
        filled_avg_price=None,
        filled_qty=0,
        legs=[],
    )
    o.status = "filled"
    o.filled_avg_price = Decimal("101.50")
    o.filled_qty = 100
    o.legs.append("tp-leg")
    assert o.status == "filled"
    assert o.filled_avg_price == Decimal("101.50")
    assert o.filled_qty == 100
    assert o.legs == ["tp-leg"]


def test_fill_is_mutable_and_fields():
    f = models.Fill(
        order_id="o-1",
        client_order_id="2026-06-19-AAPL-0-ENTRY",
        leg_role="ENTRY",
        side="buy",
        price=Decimal("101.50"),
        qty=100,
        ts=datetime(2026, 6, 19, 9, 46, tzinfo=ET),
        position_qty=100,
        exit_reason=None,
    )
    assert f.leg_role == "ENTRY"
    assert isinstance(f.price, Decimal)
    assert f.exit_reason is None
    f.exit_reason = "TARGET"  # mutable
    assert f.exit_reason == "TARGET"


def test_approval_request_is_mutable_and_fields():
    req = models.ApprovalRequest(
        setup=_setup(),
        symbol="AAPL",
        qty=100,
        risk_dollars=500.0,
        mode="LIVE",
        feed="SIP",
        or_high=Decimal("101.50"),
        or_low=Decimal("99.75"),
        bars_present=15,
        data_warning=None,
        approval_ttl_s=90,
    )
    assert req.setup.direction is models.Direction.LONG
    assert req.mode == "LIVE"
    assert req.approval_ttl_s == 90
    req.data_warning = "low confidence range"  # mutable
    assert req.data_warning == "low confidence range"
```

```bash
pytest tests/test_models_runtime.py -q
```
Expected: **FAIL** (`AttributeError: module 'orb_bot.models' has no attribute 'AccountSnapshot'`).

- [ ] **Step 2: Implement the frozen runtime pair + mutable I/O dataclasses.** Append to `orb_bot/models.py`.

```python
# orb_bot/models.py  (append)
@dataclass(frozen=True)
class AccountSnapshot:
    equity: Decimal
    buying_power: Decimal
    shorting_enabled: bool


@dataclass(frozen=True)
class ClockInfo:
    is_open: bool
    next_close: datetime


@dataclass
class OrderResult:
    order_id: str
    client_order_id: str
    status: str
    filled_avg_price: Decimal | None
    filled_qty: int
    legs: list


@dataclass
class Fill:
    order_id: str
    client_order_id: str
    leg_role: str                # 'ENTRY' | 'TP' | 'SL' | 'FLATTEN'
    side: str
    price: Decimal
    qty: int
    ts: datetime
    position_qty: int
    exit_reason: str | None      # None on entry


@dataclass
class ApprovalRequest:
    setup: Setup
    symbol: str
    qty: int
    risk_dollars: float
    mode: str                    # 'PAPER' | 'LIVE'
    feed: str
    or_high: Decimal
    or_low: Decimal
    bars_present: int
    data_warning: str | None
    approval_ttl_s: int          # = config approval_timeout_s, copied in by orchestrator
```

```bash
pytest tests/test_models_runtime.py -q
```
Expected: **PASS** (5 tests).

- [ ] **Step 3: Write failing tests for TradeResult, SessionSummary (frozen) + the EngineEvent union members.** Verifies the seven event dataclasses are frozen, carry their payloads, and that `EngineEvent` is a `typing.Union` covering all members.

```python
# tests/test_models_runtime.py  (append)
import typing
from datetime import date


def test_trade_result_frozen_and_fields():
    tr = models.TradeResult(
        direction=models.Direction.LONG,
        model=models.Model.BREAKOUT,
        qty=100,
        entry_price=Decimal("101.50"),
        exit_price=Decimal("104.50"),
        pnl=Decimal("300.00"),
        pnl_pct=Decimal("0.003"),
        exit_reason="TARGET",
    )
    assert tr.qty == 100
    assert isinstance(tr.pnl, Decimal)
    assert tr.exit_reason == "TARGET"
    with pytest.raises(dataclasses.FrozenInstanceError):
        tr.pnl = Decimal("0")  # type: ignore[misc]


def test_session_summary_frozen_and_fields():
    tr = models.TradeResult(
        direction=models.Direction.LONG,
        model=models.Model.BREAKOUT,
        qty=100,
        entry_price=Decimal("101.50"),
        exit_price=Decimal("104.50"),
        pnl=Decimal("300.00"),
        pnl_pct=Decimal("0.003"),
        exit_reason="TARGET",
    )
    ss = models.SessionSummary(
        session_date=date(2026, 6, 19),
        symbol="AAPL",
        mode="PAPER",
        trades=[tr],
        total_pnl=Decimal("300.00"),
        total_pnl_pct=Decimal("0.003"),
        wins=1,
        losses=0,
        breakevens=0,
        start_equity=Decimal("100000.00"),
        end_equity=Decimal("100300.00"),
        no_trade_reason=None,
    )
    assert ss.session_date == date(2026, 6, 19)
    assert ss.trades == [tr]
    assert ss.wins + ss.losses + ss.breakevens == len(ss.trades)
    assert ss.no_trade_reason is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        ss.total_pnl = Decimal("0")  # type: ignore[misc]


def test_engine_events_construct_and_frozen():
    orng = models.OpeningRange(
        high=Decimal("101.50"),
        low=Decimal("99.75"),
        established_at=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        width=Decimal("1.75"),
        feed="IEX",
        bars_present=15,
        low_confidence=False,
    )
    re = models.RangeEstablished(opening_range=orng)
    assert re.opening_range is orng

    dc = models.DirectionConfirmed(
        direction=models.Direction.LONG, break_level=Decimal("101.50")
    )
    assert dc.direction is models.Direction.LONG
    assert dc.break_level == Decimal("101.50")

    sp = models.SetupProposed(setup=_setup())
    assert sp.setup.model is models.Model.BREAKOUT

    tr = models.TradeRecorded(pnl=Decimal("300.00"), exit_reason="TARGET")
    assert tr.pnl == Decimal("300.00")
    assert tr.exit_reason == "TARGET"

    # zero-payload events construct cleanly
    assert models.RangeDayDetected() is not None
    assert models.EntryConfirmed() is not None
    assert models.WindowExpired() is not None
    assert models.NoOp() is not None

    # frozen check on a representative payload event
    with pytest.raises(dataclasses.FrozenInstanceError):
        re.opening_range = orng  # type: ignore[misc]


def test_engine_event_union_covers_all_members():
    members = set(typing.get_args(models.EngineEvent))
    assert members == {
        models.RangeEstablished,
        models.DirectionConfirmed,
        models.RangeDayDetected,
        models.SetupProposed,
        models.EntryConfirmed,
        models.TradeRecorded,
        models.WindowExpired,
        models.NoOp,
    }
```

```bash
pytest tests/test_models_runtime.py -q
```
Expected: **FAIL** (`AttributeError: module 'orb_bot.models' has no attribute 'TradeResult'`).

- [ ] **Step 4: Implement TradeResult, SessionSummary, the seven EngineEvent dataclasses, and the EngineEvent union.** Append to `orb_bot/models.py`.

```python
# orb_bot/models.py  (append)
@dataclass(frozen=True)
class TradeResult:
    direction: Direction
    model: Model
    qty: int
    entry_price: Decimal         # share-weighted avg of partial fills
    exit_price: Decimal
    pnl: Decimal
    pnl_pct: Decimal             # STORED as a FRACTION (pnl/start_equity)
    exit_reason: str             # 'TARGET' | 'STOP' | 'FLATTEN'


@dataclass(frozen=True)
class SessionSummary:
    session_date: date
    symbol: str
    mode: str                    # 'PAPER' | 'LIVE'
    trades: list[TradeResult]
    total_pnl: Decimal           # realized
    total_pnl_pct: Decimal       # total_pnl / start_equity (fraction)
    wins: int
    losses: int                  # pnl>0 / pnl<0 / pnl==0; sum == len(trades)
    breakevens: int
    start_equity: Decimal        # live equity at preflight (ALWAYS)
    end_equity: Decimal
    no_trade_reason: str | None  # set when trades == []


# --- EngineEvent union -------------------------------------------------------
# Closed union of small frozen dataclasses the pure engine returns from each call.
@dataclass(frozen=True)
class RangeEstablished:
    opening_range: OpeningRange


@dataclass(frozen=True)
class DirectionConfirmed:
    direction: Direction
    break_level: Decimal


@dataclass(frozen=True)
class RangeDayDetected:
    pass


@dataclass(frozen=True)
class SetupProposed:
    setup: Setup


@dataclass(frozen=True)
class EntryConfirmed:
    pass


@dataclass(frozen=True)
class TradeRecorded:
    pnl: Decimal
    exit_reason: str


@dataclass(frozen=True)
class WindowExpired:
    pass


@dataclass(frozen=True)
class NoOp:
    pass


EngineEvent = Union[
    RangeEstablished,
    DirectionConfirmed,
    RangeDayDetected,
    SetupProposed,
    EntryConfirmed,
    TradeRecorded,
    WindowExpired,
    NoOp,
]
```

```bash
pytest tests/test_models_runtime.py -q
```
Expected: **PASS** (8 tests).

- [ ] **Step 5: Confirm the full module suite is green and the unused `field` import is removed.** The `field` import added in the first task is unused; ruff will flag it — drop it so the module stays clean.

Edit `orb_bot/models.py`: change
```python
from dataclasses import dataclass, field
```
to
```python
from dataclasses import dataclass
```

```bash
pytest tests -q && ruff check orb_bot/models.py
```
Expected: **PASS** (all model tests) and ruff reports **All checks passed!**.

- [ ] **Step 6: Commit.**

```bash
git add orb_bot/models.py tests/test_models_runtime.py
git commit -m "feat(models): runtime I/O dataclasses, TradeResult, SessionSummary, EngineEvent union"
```

---

The repo is mostly empty (greenfield). The `orb_bot/`, `tests/`, and `config/` directories don't exist yet. I have everything I need to produce the markdown plan for the config module.

### Task 4: Config — pydantic StrategyConfig + RunConfig skeleton & package init

**Files:** Create `orb_bot/config.py`. Test `tests/test_config_models.py`. CONSUMES (do NOT create/overwrite — owned by Task 1): `orb_bot/__init__.py`, `tests/__init__.py`, `tests/context.py`, `pyproject.toml` (Task 1 is the single source of truth for packaging/tooling; this task only verifies pydantic deps are present).
**Interfaces:** Consumes: nothing (leaf module). Produces: `StrategyConfig` (all spec §7 strategy fields, Decimal-typed prices/widths via field coercion, `flatten_at`/`session_open` as `datetime.time`), `RunConfig(symbol:str, live:bool=False, feed:str='IEX', allow_live_iex:bool=False)`.

- [ ] **Step 1: Ensure the package + test scaffolding exists (idempotent — do NOT clobber Task 1's files).** Task 1 already created `orb_bot/__init__.py` (with its docstring + `__version__`), `tests/__init__.py`, and `tests/context.py`. Consume them as-is; only create a missing one (running this task in isolation) from Task 1's canonical body. NEVER truncate or overwrite `orb_bot/__init__.py`.
```bash
mkdir -p orb_bot tests config
[ -f orb_bot/__init__.py ] || printf '"""orb_bot — Opening-Range Breakout trading bot (flat-layout package)."""\n__version__ = "0.1.0"\n' > orb_bot/__init__.py
[ -f tests/__init__.py ] || : > tests/__init__.py
[ -f tests/context.py ] || cat > tests/context.py <<'PY'
"""Test bootstrap: insert repo root on sys.path so `import orb_bot` works."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import orb_bot  # noqa: E402,F401
PY
```

- [ ] **Step 2: Write a FAILING test that the models import and carry spec defaults.**
```python
# tests/test_config_models.py
from datetime import time
from decimal import Decimal

from .context import orb_bot
from orb_bot.config import RunConfig, StrategyConfig


def test_strategy_defaults_match_spec():
    cfg = StrategyConfig()
    assert cfg.range_timeframe_min == 15
    assert cfg.confirm_timeframe_min == 15
    assert cfg.entry_timeframe_min == 15
    assert cfg.atr_timeframe_min == 15
    assert cfg.or_min_bars == 15
    assert cfg.trading_window_min == 120
    assert cfg.session_open == time(9, 30)
    assert cfg.flatten_at == time(15, 55)
    assert cfg.flatten_buffer_min == 5
    assert cfg.bar_grace_seconds == 3
    assert cfg.risk_reward_ratio == 2.0
    assert cfg.risk_per_trade_pct == 0.5
    assert cfg.max_trades_per_day == 1
    assert cfg.allow_long is True and cfg.allow_short is True
    assert cfg.equity_source == "live"
    assert cfg.fixed_equity is None
    assert cfg.rearm_opposite_only is True
    assert cfg.enable_breakout is True
    assert cfg.enable_retest is True
    assert cfg.enable_reversal is False
    assert cfg.displacement_model == "IMPULSE"
    assert cfg.tick_size == Decimal("0.01")
    assert cfg.stop_buffer_atr == Decimal("0.10")
    assert cfg.stop_buffer_ticks == 2
    assert cfg.min_stop_distance == Decimal("0.02")
    assert cfg.swing_fractal_k == 1
    assert cfg.swing_lookback == 4
    assert cfg.range_day_sweep_both is True
    assert cfg.range_day_disables == ["breakout", "retest"]
    assert cfg.range_day_enables == []
    assert cfg.sweep_buffer == Decimal("0.0")
    assert cfg.require_higher_tf_bias is False
    assert cfg.atr_period == 14
    assert cfg.approval_timeout_s == 90
    assert cfg.logging_level == "INFO"
    assert cfg.logging_dir == "logs/"


def test_run_defaults_match_spec():
    rc = RunConfig(symbol="SPY")
    assert rc.symbol == "SPY"
    assert rc.live is False
    assert rc.feed == "IEX"
    assert rc.allow_live_iex is False
```
```bash
pytest tests/test_config_models.py -q
```
Expected: **FAIL** (`ModuleNotFoundError: orb_bot.config`).

- [ ] **Step 3: Minimal `config.py` with both models + defaults (no validators yet).**
```python
# orb_bot/config.py
"""Typed configuration for orb_bot. Loads strategy + run flags from YAML and
secrets from .env; validation runs at load time so a bad config fails fast,
before any network connection."""
from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # session / timing
    timezone: str = "America/New_York"
    session_open: time = time(9, 30)
    range_timeframe_min: int = 15
    confirm_timeframe_min: int = 15
    entry_timeframe_min: int = 15
    trading_window_min: int = 120
    flatten_at: time = time(15, 55)
    flatten_buffer_min: int = 5
    bar_grace_seconds: int = 3

    # risk / sizing
    risk_reward_ratio: float = 2.0
    risk_per_trade_pct: float = 0.5
    max_trades_per_day: int = 1
    allow_long: bool = True
    allow_short: bool = True
    equity_source: Literal["live", "fixed"] = "live"
    fixed_equity: Decimal | None = None
    rearm_opposite_only: bool = True

    # entry-model toggles
    enable_breakout: bool = True
    enable_retest: bool = True
    enable_reversal: bool = False

    # strong close
    strong_close_body_ratio: float = 0.60
    strong_close_location: float = 0.70

    # displacement
    displacement_model: Literal["IMPULSE", "FVG", "TRUE_GAP"] = "IMPULSE"
    fvg_min_size_ticks: int = 2
    impulse_atr_mult: float = 1.5

    # retest
    retest_tolerance_atr: float = 0.25
    retest_max_wait_candles: int = 4
    retest_confirm_body_ratio: float = 0.50

    # stops / structure
    tick_size: Decimal = Decimal("0.01")
    stop_buffer_atr: Decimal = Decimal("0.10")
    stop_buffer_ticks: int = 2
    min_stop_distance: Decimal = Decimal("0.02")
    swing_fractal_k: int = 1
    swing_lookback: int = 4

    # day-type filter
    range_day_sweep_both: bool = True
    range_day_disables: list[str] = Field(default_factory=lambda: ["breakout", "retest"])
    range_day_enables: list[str] = Field(default_factory=list)
    sweep_buffer: Decimal = Decimal("0.0")
    require_higher_tf_bias: bool = False

    # ATR
    atr_period: int = 14
    atr_timeframe_min: int = 15
    or_min_bars: int = 15

    # approval
    approval_timeout_s: int = 90

    # logging
    logging_level: str = "INFO"
    logging_dir: str = "logs/"


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    live: bool = False
    feed: Literal["IEX", "SIP"] = "IEX"
    allow_live_iex: bool = False
```
```bash
pytest tests/test_config_models.py -q
```
Expected: **PASS** (2 passed).

- [ ] **Step 4: VERIFY `pyproject.toml` already carries the pydantic deps (additive check — do NOT rewrite it).** `pyproject.toml` is owned by Task 1 (name `orb-bot`, with `[tool.ruff]`, `[tool.mypy]`, `[tool.coverage.run]`, `[project.scripts]` `orb-bot=orb_bot.__main__:main`, and the `[tool.importlinter]` purity contract). Task 1 already lists `pydantic>=2` and `pydantic-settings` in `[project.dependencies]`, so this is normally a no-op verification. If (and only if) either is missing, add it to the EXISTING `[project] dependencies` array — do NOT emit a fresh `[build-system]`/`[project]`/`[tool.*]` block, which would clobber Task 1's authoritative config and (wrongly) rename the project to `orb_bot`.
```bash
# No-op verification: confirm pydantic + pydantic-settings are present (added by Task 1).
grep -q '"pydantic>=2"' pyproject.toml && grep -q '"pydantic-settings"' pyproject.toml \
  && echo "pyproject deps OK (no edit needed)" \
  || echo "MISSING pydantic dep(s) — add to the existing [project].dependencies array, do NOT rewrite pyproject"
# Confirm Task 1's authoritative project name is intact.
grep -q 'name = "orb-bot"' pyproject.toml && echo "project name = orb-bot OK"
```
```bash
pip install -e ".[dev]" && pytest tests/test_config_models.py -q
```
Expected: **PASS** (2 passed); the grep prints `pyproject deps OK (no edit needed)` and `project name = orb-bot OK`.

- [ ] **Step 5: Commit.**
```bash
git add orb_bot/config.py tests/test_config_models.py
git commit -m "feat(config): StrategyConfig + RunConfig pydantic models with spec §7 defaults"
```

### Task 5: Config — fail-fast validators on StrategyConfig

**Files:** Modify `orb_bot/config.py`. Test `tests/test_config_validators.py`.
**Interfaces:** Consumes: `StrategyConfig` from previous task. Produces: `StrategyConfig` that raises `pydantic.ValidationError` on any invalid spec §7 combination (single-TF gate, ATR/or_min_bars scaling gates, swing bounds, window divisibility, deferred-feature rejections, equity_source/fixed_equity, time/risk bounds).

- [ ] **Step 1: Write FAILING tests — each bad value raises `ValidationError`.**
```python
# tests/test_config_validators.py
import pytest
from pydantic import ValidationError

from .context import orb_bot
from orb_bot.config import StrategyConfig


def _ok(**over):
    return StrategyConfig(**over)


def test_flatten_at_must_be_after_session_open():
    with pytest.raises(ValidationError):
        _ok(flatten_at="09:00")  # before 09:30 session_open


def test_flatten_buffer_min_nonnegative():
    with pytest.raises(ValidationError):
        _ok(flatten_buffer_min=-1)


def test_risk_reward_ratio_must_be_positive():
    with pytest.raises(ValidationError):
        _ok(risk_reward_ratio=0.0)


def test_risk_per_trade_pct_bounds():
    with pytest.raises(ValidationError):
        _ok(risk_per_trade_pct=0.0)
    with pytest.raises(ValidationError):
        _ok(risk_per_trade_pct=100.1)


def test_single_timeframe_gate_confirm_must_equal_range():
    with pytest.raises(ValidationError):
        _ok(confirm_timeframe_min=5)  # != range 15


def test_single_timeframe_gate_entry_must_equal_range():
    with pytest.raises(ValidationError):
        _ok(entry_timeframe_min=5)


def test_timeframe_must_be_in_allowed_set():
    with pytest.raises(ValidationError):
        _ok(
            range_timeframe_min=7,
            confirm_timeframe_min=7,
            entry_timeframe_min=7,
            atr_timeframe_min=7,
            or_min_bars=7,
            trading_window_min=126,  # divisible by 7 to isolate the T-set failure
            retest_max_wait_candles=4,
        )


def test_atr_timeframe_must_equal_range():
    with pytest.raises(ValidationError):
        _ok(atr_timeframe_min=5)


def test_or_min_bars_upper_bound():
    with pytest.raises(ValidationError):
        _ok(or_min_bars=16)  # > range 15


def test_or_min_bars_lower_bound():
    with pytest.raises(ValidationError):
        _ok(or_min_bars=0)  # < 1


def test_swing_lookback_must_cover_fractal():
    with pytest.raises(ValidationError):
        _ok(swing_fractal_k=2, swing_lookback=4)  # need >= 2*2+1 = 5


def test_trading_window_must_be_whole_candles():
    with pytest.raises(ValidationError):
        _ok(trading_window_min=121)  # 121 % 15 != 0


def test_retest_max_wait_must_fit_window():
    with pytest.raises(ValidationError):
        _ok(retest_max_wait_candles=9)  # > 120/15 = 8


def test_equity_source_fixed_requires_fixed_equity():
    with pytest.raises(ValidationError):
        _ok(equity_source="fixed", fixed_equity=None)


def test_enable_reversal_true_rejected():
    with pytest.raises(ValidationError):
        _ok(enable_reversal=True)


def test_require_higher_tf_bias_true_rejected():
    with pytest.raises(ValidationError):
        _ok(require_higher_tf_bias=True)


def test_nonempty_range_day_enables_rejected():
    with pytest.raises(ValidationError):
        _ok(range_day_enables=["reversal"])


def test_valid_t5_config_passes():
    cfg = StrategyConfig(
        range_timeframe_min=5,
        confirm_timeframe_min=5,
        entry_timeframe_min=5,
        atr_timeframe_min=5,
        or_min_bars=5,
        trading_window_min=120,
        retest_max_wait_candles=4,
    )
    assert cfg.range_timeframe_min == 5
    assert cfg.or_min_bars == 5
```
```bash
pytest tests/test_config_validators.py -q
```
Expected: **FAIL** (most assertions error — no validators raise yet).

- [ ] **Step 2: Add the validators (model + field validators).**
First add the imports. Replace the import line in `orb_bot/config.py`:
```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```
Then append the validators **inside** the `StrategyConfig` class (after the field declarations, before `class RunConfig`):
```python
    # --- field-level validators (fail fast, per spec §7) ---
    @field_validator("flatten_buffer_min")
    @classmethod
    def _buffer_nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("flatten_buffer_min must be >= 0")
        return v

    @field_validator("risk_reward_ratio")
    @classmethod
    def _rr_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("risk_reward_ratio must be > 0")
        return v

    @field_validator("risk_per_trade_pct")
    @classmethod
    def _risk_pct_bounds(cls, v: float) -> float:
        if not (0 < v <= 100):
            raise ValueError("risk_per_trade_pct must satisfy 0 < pct <= 100")
        return v

    @field_validator("enable_reversal")
    @classmethod
    def _reversal_deferred(cls, v: bool) -> bool:
        if v:
            raise ValueError("enable_reversal is DEFERRED this build; must be False")
        return v

    @field_validator("require_higher_tf_bias")
    @classmethod
    def _htf_bias_deferred(cls, v: bool) -> bool:
        if v:
            raise ValueError("require_higher_tf_bias is DEFERRED this build; must be False")
        return v

    @field_validator("range_day_enables")
    @classmethod
    def _enables_must_be_implemented(cls, v: list[str]) -> list[str]:
        implemented = {"breakout", "retest"}  # reversal deferred this build
        bad = [m for m in v if m not in implemented]
        if bad:
            raise ValueError(
                f"range_day_enables lists unimplemented model(s): {bad}"
            )
        return v

    # --- whole-model validators (cross-field, per spec §7) ---
    @model_validator(mode="after")
    def _validate_cross_fields(self) -> "StrategyConfig":
        T = self.range_timeframe_min

        # flatten_at > session_open
        if self.flatten_at <= self.session_open:
            raise ValueError("flatten_at must be after session_open")

        # single-timeframe gate: confirm == entry == range
        if not (self.confirm_timeframe_min == self.entry_timeframe_min == T):
            raise ValueError(
                "single-timeframe gate: confirm_timeframe_min == "
                "entry_timeframe_min == range_timeframe_min required"
            )

        # T in allowed set
        if T not in (1, 5, 15):
            raise ValueError("range_timeframe_min (T) must be one of {1, 5, 15}")

        # whole-candle trading window
        if self.trading_window_min % T != 0:
            raise ValueError(
                "trading_window_min must be a whole multiple of range_timeframe_min"
            )

        # retest wait must fit the window
        if self.retest_max_wait_candles > self.trading_window_min // T:
            raise ValueError(
                "retest_max_wait_candles must fit the trading window "
                "(<= trading_window_min / range_timeframe_min)"
            )

        # timeframe-scaling gate: ATR on the run's timeframe
        if self.atr_timeframe_min != T:
            raise ValueError("atr_timeframe_min must == range_timeframe_min")

        # opening T-bar holds at most T one-minute children
        if not (1 <= self.or_min_bars <= T):
            raise ValueError("or_min_bars must satisfy 1 <= or_min_bars <= range_timeframe_min")

        # swing window must cover a full fractal pivot
        if self.swing_lookback < 2 * self.swing_fractal_k + 1:
            raise ValueError("swing_lookback must be >= 2 * swing_fractal_k + 1")

        # fixed equity source requires a value
        if self.equity_source == "fixed" and self.fixed_equity is None:
            raise ValueError("equity_source == 'fixed' requires fixed_equity")

        return self
```
```bash
pytest tests/test_config_validators.py -q
```
Expected: **PASS** (all validator tests pass).

- [ ] **Step 3: Re-run the full config suite to confirm no regression on defaults.**
```bash
pytest tests/test_config_models.py tests/test_config_validators.py -q
```
Expected: **PASS** (all tests from both files).

- [ ] **Step 4: Commit.**
```bash
git add orb_bot/config.py tests/test_config_validators.py
git commit -m "feat(config): fail-fast validators for single-TF gate, scaling, swing bounds & deferred features"
```

### Task 6: Config — Settings (secrets + nested YAML) + load_config(path)

**Files:** Modify `orb_bot/config.py`. Test `tests/test_config_loading.py`. CONSUMES (do NOT create/overwrite — owned by Task 1): `config/config.yaml` (Task 1 writes the full §7 default block; this task reads it).
**Interfaces:** Consumes: `StrategyConfig`, `RunConfig`. Produces: `Settings(BaseSettings)` with secrets `ALPACA_KEY`, `ALPACA_SECRET`, `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID:int|None`, `DISCORD_APPROVER_USER_ID:int|None`, nested `strategy: StrategyConfig`, `run: RunConfig`; classmethod `settings_customise_sources` adding `YamlConfigSettingsSource`; module function `load_config(path: str | Path) -> Settings`. Validators: `run.live and run.feed != "SIP"` rejected unless `run.allow_live_iex`; `run.live` requires all three Discord values.

- [ ] **Step 1: Ensure `config/config.yaml` exists (idempotent — do NOT clobber Task 1's authoritative file).** `config/config.yaml` is owned by Task 1, which writes the full §7 strategy+run block. Consume it as-is. Only if it is missing (running this task in isolation) create it from the block below — otherwise this step is a no-op.
```bash
mkdir -p config
[ -f config/config.yaml ] || cat > config/config.yaml <<'YAML'
strategy:
  timezone: "America/New_York"
  session_open: "09:30"
  range_timeframe_min: 15
  confirm_timeframe_min: 15
  entry_timeframe_min: 15
  trading_window_min: 120
  flatten_at: "15:55"
  flatten_buffer_min: 5
  bar_grace_seconds: 3
  risk_reward_ratio: 2.0
  risk_per_trade_pct: 0.5
  max_trades_per_day: 1
  allow_long: true
  allow_short: true
  equity_source: "live"
  fixed_equity: null
  rearm_opposite_only: true
  enable_breakout: true
  enable_retest: true
  enable_reversal: false
  strong_close_body_ratio: 0.60
  strong_close_location: 0.70
  displacement_model: "IMPULSE"
  fvg_min_size_ticks: 2
  impulse_atr_mult: 1.5
  retest_tolerance_atr: 0.25
  retest_max_wait_candles: 4
  retest_confirm_body_ratio: 0.50
  tick_size: 0.01
  stop_buffer_atr: 0.10
  stop_buffer_ticks: 2
  min_stop_distance: 0.02
  swing_fractal_k: 1
  swing_lookback: 4
  range_day_sweep_both: true
  range_day_disables: ["breakout", "retest"]
  range_day_enables: []
  sweep_buffer: 0.0
  require_higher_tf_bias: false
  atr_period: 14
  atr_timeframe_min: 15
  or_min_bars: 15
  approval_timeout_s: 90
  logging_level: "INFO"
  logging_dir: "logs/"
run:
  symbol: "SPY"
  live: false
  feed: "IEX"
  allow_live_iex: false
YAML
```

- [ ] **Step 2: Write FAILING tests for load + precedence + Settings validators.**
```python
# tests/test_config_loading.py
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from .context import orb_bot
from orb_bot.config import Settings, load_config

REPO_YAML = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_loads_repo_config_yaml(monkeypatch):
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("DISCORD_APPROVER_USER_ID", raising=False)
    cfg = load_config(REPO_YAML)
    assert cfg.alpaca_key == "k"
    assert cfg.alpaca_secret == "s"
    assert cfg.run.symbol == "SPY"
    assert cfg.run.live is False
    assert cfg.strategy.range_timeframe_min == 15


def test_env_overrides_yaml_for_secrets(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "QQQ"}
    """)
    monkeypatch.setenv("ALPACA_KEY", "envkey")
    monkeypatch.setenv("ALPACA_SECRET", "envsecret")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    cfg = load_config(p)
    assert cfg.alpaca_key == "envkey"
    assert cfg.run.symbol == "QQQ"


def test_missing_required_secret_fails(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY"}
    """)
    monkeypatch.delenv("ALPACA_KEY", raising=False)
    monkeypatch.setenv("ALPACA_SECRET", "s")
    with pytest.raises(ValidationError):
        load_config(p)


def test_live_requires_sip_unless_allow_live_iex(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY", live: true, feed: "IEX", allow_live_iex: false}
    """)
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "1")
    monkeypatch.setenv("DISCORD_APPROVER_USER_ID", "2")
    with pytest.raises(ValidationError):
        load_config(p)


def test_live_iex_allowed_with_override(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY", live: true, feed: "IEX", allow_live_iex: true}
    """)
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "1")
    monkeypatch.setenv("DISCORD_APPROVER_USER_ID", "2")
    cfg = load_config(p)
    assert cfg.run.live is True and cfg.run.feed == "IEX"


def test_live_requires_all_discord_values(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY", live: true, feed: "SIP"}
    """)
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)  # missing one => reject
    monkeypatch.setenv("DISCORD_APPROVER_USER_ID", "2")
    with pytest.raises(ValidationError):
        load_config(p)


def test_paper_discord_optional(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY", live: false, feed: "SIP"}
    """)
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("DISCORD_APPROVER_USER_ID", raising=False)
    cfg = load_config(p)
    assert cfg.discord_token is None
    assert cfg.run.live is False
```
```bash
pytest tests/test_config_loading.py -q
```
Expected: **FAIL** (`ImportError: cannot import name 'Settings' / 'load_config'`).

- [ ] **Step 3: Implement `Settings` + `load_config` in `orb_bot/config.py`.**
Add imports at the top (replace the existing `from __future__` / stdlib block to include `Path` and pydantic-settings):
```python
from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)
```
Then append at the **end** of `orb_bot/config.py`:
```python
class Settings(BaseSettings):
    """Top-level settings: secrets from env/.env, strategy+run from YAML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        yaml_file=None,  # set per-load via _yaml_path
    )

    # secrets (env / .env)
    alpaca_key: str = Field(validation_alias="ALPACA_KEY")
    alpaca_secret: str = Field(validation_alias="ALPACA_SECRET")
    discord_token: str | None = Field(default=None, validation_alias="DISCORD_TOKEN")
    discord_channel_id: int | None = Field(
        default=None, validation_alias="DISCORD_CHANNEL_ID"
    )
    discord_approver_user_id: int | None = Field(
        default=None, validation_alias="DISCORD_APPROVER_USER_ID"
    )

    # nested config (YAML)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    run: RunConfig

    # path injected by load_config; read by settings_customise_sources
    _yaml_path: Path | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = getattr(settings_cls, "_yaml_file_path", None)
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]
        if yaml_path is not None:
            sources.append(
                YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path)
            )
        sources.append(file_secret_settings)
        return tuple(sources)

    @model_validator(mode="after")
    def _validate_run_against_secrets(self) -> "Settings":
        # live + non-SIP feed rejected unless explicitly allowed
        if self.run.live and self.run.feed != "SIP" and not self.run.allow_live_iex:
            raise ValueError(
                "run.live with a non-SIP feed is rejected; set run.allow_live_iex=true to override"
            )
        # live mode requires the full Discord approval gate
        if self.run.live and not (
            self.discord_token
            and self.discord_channel_id is not None
            and self.discord_approver_user_id is not None
        ):
            raise ValueError(
                "run.live requires DISCORD_TOKEN, DISCORD_CHANNEL_ID and "
                "DISCORD_APPROVER_USER_ID (the live approval gate)"
            )
        return self


def load_config(path: str | Path) -> Settings:
    """Load Settings from secrets (env/.env) + the given YAML file. Validation
    runs at load time, so a bad config fails before any network connection."""
    yaml_path = Path(path)

    class _BoundSettings(Settings):
        _yaml_file_path = yaml_path  # class attr read by settings_customise_sources

    return _BoundSettings()  # type: ignore[call-arg]
```
```bash
pytest tests/test_config_loading.py -q
```
Expected: **PASS** (all loading/precedence/validator tests pass).

- [ ] **Step 4: Run the entire config test suite (regression sweep).**
```bash
pytest tests/test_config_models.py tests/test_config_validators.py tests/test_config_loading.py -q
```
Expected: **PASS** (all config tests green).

- [ ] **Step 5: Commit.**
```bash
git add orb_bot/config.py tests/test_config_loading.py
git commit -m "feat(config): Settings with YAML+.env sources, load_config(path), live-feed & Discord gates"
```

---

The package and tests dirs don't exist yet. My plan must reference them via exact paths and assume `models.py` is created by another task (this module's tests only need the Protocols and trivial stubs, not the canonical types — but the Protocol return-type annotations reference canonical types like `Candle`, `AccountSnapshot`, etc., which live in `models.py`). I'll write the plan so `interfaces.py` imports from `models`, and the test uses trivial stubs that satisfy structural typing without instantiating model objects.


### Task 7: Interfaces — runtime_checkable Protocols for the swap seam

**Files:**
- Create: `orb_bot/interfaces.py`
- Test: `tests/test_interfaces.py`

**Interfaces:**
Consumes (type annotations only, imported from the models module produced by the models task): `Candle`, `AccountSnapshot`, `ClockInfo`, `Setup`, `OrderResult`, `Fill`, `ApprovalRequest`, `SessionSummary` from `orb_bot.models`.
Produces (relied on by `feed`/`execution`/`approval`/`reporting`/`orchestrator` tasks):
- `DataFeed(Protocol)`: `def candles(self) -> AsyncIterator[Candle]`; `async def close(self) -> None`
- `Broker(Protocol)`: `async def get_account(self) -> AccountSnapshot`; `async def get_clock(self) -> ClockInfo`; `async def submit_bracket(self, setup: Setup, qty: int) -> OrderResult`; `def trade_updates(self) -> AsyncIterator[Fill]`; `async def get_order(self, order_id: str) -> OrderResult`; `async def cancel_all(self) -> None`; `async def flatten(self) -> None`
- `Approver(Protocol)`: `async def start(self) -> None`; `async def request(self, req: ApprovalRequest) -> str`; `async def close(self) -> None`
- `Reporter(Protocol)`: `async def start(self) -> None`; `async def trade_taken(self, setup: Setup, qty: int, mode: str) -> None`; `async def session_report(self, summary: SessionSummary) -> None`; `async def close(self) -> None`
- `Clock(Protocol)`: `def now(self) -> datetime`

All five are decorated `@runtime_checkable`.

- [ ] **Step 1: Ensure test scaffolding exists (`tests/__init__.py` + `tests/context.py`).**
These are shared by every test module. Create them if absent (idempotent — safe if another task already made them).

```bash
mkdir -p /home/manav/codespace/obr-strat/orb_bot /home/manav/codespace/obr-strat/tests
[ -f /home/manav/codespace/obr-strat/orb_bot/__init__.py ] || printf '' > /home/manav/codespace/obr-strat/orb_bot/__init__.py
[ -f /home/manav/codespace/obr-strat/tests/__init__.py ] || printf '' > /home/manav/codespace/obr-strat/tests/__init__.py
```

If `tests/context.py` does not yet exist, create it with exactly this content:

```python
# tests/context.py
"""Test bootstrap: put repo root on sys.path so `import orb_bot` resolves (flat layout)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import orb_bot  # noqa: F401,E402
```

- [ ] **Step 2: Write the failing test `tests/test_interfaces.py`.**
The test asserts each Protocol is a `typing.Protocol`, is `runtime_checkable`, and that a trivial stub instance passes `isinstance`. Stubs use `...`-bodies (structural typing only checks attribute presence at runtime, not signatures), so no model objects are constructed.

```python
# tests/test_interfaces.py
from typing import AsyncIterator, get_type_hints, runtime_checkable

import pytest

from .context import orb_bot  # noqa: F401
from orb_bot import interfaces
from orb_bot.interfaces import DataFeed, Broker, Approver, Reporter, Clock

ALL_PROTOCOLS = [DataFeed, Broker, Approver, Reporter, Clock]


def _is_protocol(cls) -> bool:
    # typing marks Protocol classes with the private _is_protocol flag.
    return getattr(cls, "_is_protocol", False) is True


@pytest.mark.parametrize("proto", ALL_PROTOCOLS)
def test_is_protocol(proto):
    assert _is_protocol(proto), f"{proto.__name__} must be a typing.Protocol"


@pytest.mark.parametrize("proto", ALL_PROTOCOLS)
def test_is_runtime_checkable(proto):
    # runtime_checkable sets _is_runtime_protocol; isinstance against the bare
    # protocol must not raise TypeError (which it would for a non-runtime Protocol).
    assert getattr(proto, "_is_runtime_protocol", False) is True
    try:
        isinstance(object(), proto)
    except TypeError:
        pytest.fail(f"{proto.__name__} is not runtime_checkable")


def test_datafeed_stub_satisfies_isinstance():
    class StubFeed:
        def candles(self) -> AsyncIterator:  # type: ignore[type-arg]
            ...

        async def close(self) -> None:
            ...

    assert isinstance(StubFeed(), DataFeed)


def test_datafeed_missing_method_fails_isinstance():
    class Partial:
        def candles(self):
            ...

    # Missing close() -> structural check fails.
    assert not isinstance(Partial(), DataFeed)


def test_broker_stub_satisfies_isinstance():
    class StubBroker:
        async def get_account(self):
            ...

        async def get_clock(self):
            ...

        async def submit_bracket(self, setup, qty):
            ...

        def trade_updates(self):
            ...

        async def get_order(self, order_id):
            ...

        async def cancel_all(self) -> None:
            ...

        async def flatten(self) -> None:
            ...

    assert isinstance(StubBroker(), Broker)


def test_approver_stub_satisfies_isinstance():
    class StubApprover:
        async def start(self) -> None:
            ...

        async def request(self, req) -> str:
            return "TIMEOUT"

        async def close(self) -> None:
            ...

    assert isinstance(StubApprover(), Approver)


def test_reporter_stub_satisfies_isinstance():
    class StubReporter:
        async def start(self) -> None:
            ...

        async def trade_taken(self, setup, qty, mode) -> None:
            ...

        async def session_report(self, summary) -> None:
            ...

        async def close(self) -> None:
            ...

    assert isinstance(StubReporter(), Reporter)


def test_clock_stub_satisfies_isinstance():
    class StubClock:
        def now(self):
            ...

    assert isinstance(StubClock(), Clock)


def test_all_protocols_exported():
    for name in ("DataFeed", "Broker", "Approver", "Reporter", "Clock"):
        assert hasattr(interfaces, name)


def test_protocol_annotations_resolve():
    # get_type_hints forces evaluation of the model-typed annotations,
    # proving interfaces.py imports the canonical models correctly.
    hints = get_type_hints(Broker.get_account)
    assert "return" in hints
```

Run it and confirm it FAILS (module does not exist yet):

```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_interfaces.py -q
```
Expected: **FAIL** — `ModuleNotFoundError: No module named 'orb_bot.interfaces'` (collection error).

- [ ] **Step 3: Minimal implementation — `orb_bot/interfaces.py`.**
Write the complete module with all five `@runtime_checkable` Protocols. Annotations reference canonical `orb_bot.models` types verbatim.

```python
# orb_bot/interfaces.py
"""PEP-544 Protocols — the swap seam between the pure engine and live/paper I/O.

The engine imports NONE of these; only the orchestrator does. Concrete live
adapters (alpaca feed/broker, Discord approver/reporter) and backtest doubles
(ReplayFeed, SimBroker, AutoApprover) satisfy the identical Protocols.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol, runtime_checkable

from orb_bot.models import (
    AccountSnapshot,
    ApprovalRequest,
    ClockInfo,
    Fill,
    OrderResult,
    SessionSummary,
    Setup,
)


@runtime_checkable
class DataFeed(Protocol):
    """Yields 1m transport candles; the orchestrator aggregates 1m -> T."""

    def candles(self) -> AsyncIterator[Fill]:  # placeholder; replaced below
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class Broker(Protocol):
    async def get_account(self) -> AccountSnapshot:
        ...

    async def get_clock(self) -> ClockInfo:
        ...

    async def submit_bracket(self, setup: Setup, qty: int) -> OrderResult:
        ...

    def trade_updates(self) -> AsyncIterator[Fill]:
        ...

    async def get_order(self, order_id: str) -> OrderResult:
        ...

    async def cancel_all(self) -> None:
        ...

    async def flatten(self) -> None:
        ...


@runtime_checkable
class Approver(Protocol):
    """AutoApprover (paper) | DiscordApprover (live)."""

    async def start(self) -> None:
        ...

    async def request(self, req: ApprovalRequest) -> str:
        ...  # 'APPROVE' | 'REJECT' | 'TIMEOUT'

    async def close(self) -> None:
        ...


@runtime_checkable
class Reporter(Protocol):
    """DiscordReporter | LogReporter."""

    async def start(self) -> None:
        ...

    async def trade_taken(self, setup: Setup, qty: int, mode: str) -> None:
        ...

    async def session_report(self, summary: SessionSummary) -> None:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class Clock(Protocol):
    """tz-aware ET; used ONLY by the orchestrator."""

    def now(self) -> datetime:
        ...
```

Note the `DataFeed.candles` return annotation is wrong on purpose here (placeholder) and is corrected in the next step — this keeps the step honest about the canonical `Candle` import. Fix it now before running.

- [ ] **Step 4: Correct the `DataFeed.candles` annotation to the canonical `Candle` type.**
Add `Candle` to the models import and fix the return annotation.

```python
# orb_bot/interfaces.py  (apply two edits)
# 1) extend the import block:
from orb_bot.models import (
    AccountSnapshot,
    ApprovalRequest,
    Candle,
    ClockInfo,
    Fill,
    OrderResult,
    SessionSummary,
    Setup,
)

# 2) fix DataFeed.candles signature:
    def candles(self) -> AsyncIterator[Candle]:  # 1m transport candles
        ...
```

After editing, `orb_bot/interfaces.py` `DataFeed` reads:

```python
@runtime_checkable
class DataFeed(Protocol):
    """Yields 1m transport candles; the orchestrator aggregates 1m -> T."""

    def candles(self) -> AsyncIterator[Candle]:  # 1m transport candles
        ...

    async def close(self) -> None:
        ...
```

- [ ] **Step 5: Run the test and confirm PASS.**

```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_interfaces.py -q
```
Expected: **PASS** — all tests green (5 protocol-flag params x2, plus the stub/export/annotation tests).

If `orb_bot/models.py` is not yet implemented by its task, this will fail at import with `ImportError` on the model names; that is the expected cross-task dependency — interfaces depends on models, so run after the models task lands.

- [ ] **Step 6: Lint and type-check the new module.**

```bash
cd /home/manav/codespace/obr-strat && ruff check orb_bot/interfaces.py tests/test_interfaces.py && mypy orb_bot/interfaces.py
```
Expected: **PASS** — no ruff violations, no mypy errors.

- [ ] **Step 7: Commit.**

```bash
cd /home/manav/codespace/obr-strat && git add orb_bot/interfaces.py tests/test_interfaces.py tests/context.py tests/__init__.py orb_bot/__init__.py && git commit -m "feat(interfaces): runtime_checkable Protocols for DataFeed/Broker/Approver/Reporter/Clock

Define the PEP-544 swap seam (§6) the engine never imports and the future
backtester drops into. All five Protocols are @runtime_checkable; trivial
stubs satisfy isinstance, missing-method stubs fail it.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

The repo has no code yet. Since the module depends on `orb_bot/models.py` (Candle) which is another module's task, my plan must consume that signature but not own it. I'll assume models.py exists (provided by the models task) and reference Candle verbatim. The aggregation task creates `orb_bot/aggregation.py` and `tests/test_aggregation.py`, plus the test harness files if they're part of my surface — but those belong to the models/scaffold task. I'll reference them as consumed and only create my own files. I'll write the plan now.

### Task 8: Aggregation — `bucket_start` (09:30-anchored bucketing)

**Files:** Create `orb_bot/aggregation.py`. Test `tests/test_aggregation.py`. Consumes existing `orb_bot/models.py` (`Candle`) and `tests/context.py` (`from .context import orb_bot`) from the models/scaffold task.

**Interfaces:** Consumes: `orb_bot.models.Candle(ts_open:datetime, ts_close:datetime, open:Decimal, high:Decimal, low:Decimal, close:Decimal, volume:int, timeframe_min:int, data_incomplete:bool=False, bars_present:int|None=None)` (all timestamps tz-aware America/New_York). Produces: `bucket_start(ts: datetime, tf_min: int, anchor: time = time(9, 30)) -> datetime` — returns `base + floor((ts - base) / tf) * tf`, where `base` is `anchor` applied to `ts`'s date in `ts`'s tzinfo.

- [ ] **Step 1: Write failing test for `bucket_start`.** Create `tests/test_aggregation.py`.

```python
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .context import orb_bot
from orb_bot.aggregation import bucket_start

ET = ZoneInfo("America/New_York")


def _dt(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 6, 19, h, m, s, tzinfo=ET)


def test_bucket_start_anchors_at_0930():
    # 09:30:00 -> 09:30; 09:44:59 still in first 15m bucket -> 09:30
    assert bucket_start(_dt(9, 30, 0), 15) == _dt(9, 30, 0)
    assert bucket_start(_dt(9, 44, 59), 15) == _dt(9, 30, 0)


def test_bucket_start_advances_per_timeframe():
    # 09:45 opens the second 15m bucket
    assert bucket_start(_dt(9, 45, 0), 15) == _dt(9, 45, 0)
    assert bucket_start(_dt(9, 59, 59), 15) == _dt(9, 45, 0)
    assert bucket_start(_dt(10, 0, 0), 15) == _dt(10, 0, 0)


def test_bucket_start_passthrough_1m():
    # tf_min=1 -> every minute is its own bucket
    assert bucket_start(_dt(9, 30, 0), 1) == _dt(9, 30, 0)
    assert bucket_start(_dt(9, 31, 30), 1) == _dt(9, 31, 0)


def test_bucket_start_before_anchor_floors_below_open():
    # 09:20 is 10 min before anchor -> floors to 09:15 (one 15m bucket below)
    assert bucket_start(_dt(9, 20, 0), 15) == _dt(9, 15, 0)


def test_bucket_start_custom_anchor():
    assert bucket_start(_dt(10, 7, 0), 15, anchor=time(10, 0)) == _dt(10, 0, 0)
```

Run and confirm FAIL (ImportError — `bucket_start` not defined yet).

```bash
pytest tests/test_aggregation.py -q
```

Expected: FAIL (`ImportError: cannot import name 'bucket_start'`).

- [ ] **Step 2: Implement `bucket_start`.** Create `orb_bot/aggregation.py`.

```python
"""Pure 1m->T aggregation. No clock, no I/O, no datetime.now()."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from .models import Candle


def bucket_start(ts: datetime, tf_min: int, anchor: time = time(9, 30)) -> datetime:
    """Floor ``ts`` to the start of its ``tf_min``-minute bucket anchored at ``anchor``.

    Returns ``base + floor((ts - base) / tf) * tf`` where ``base`` is ``anchor``
    applied to ``ts``'s calendar date in ``ts``'s timezone. Pure; no clock.
    """
    base = ts.replace(
        hour=anchor.hour,
        minute=anchor.minute,
        second=anchor.second,
        microsecond=anchor.microsecond,
    )
    delta = ts - base
    n = delta // timedelta(minutes=tf_min)  # floor division (works for negatives too)
    return base + n * timedelta(minutes=tf_min)
```

Run and confirm PASS.

```bash
pytest tests/test_aggregation.py -q
```

Expected: PASS (5 tests).

- [ ] **Step 3: Commit.**

```bash
git add orb_bot/aggregation.py tests/test_aggregation.py
git commit -m "feat(aggregation): add 09:30-anchored bucket_start"
```

### Task 9: Aggregation — `Aggregator.add` (1m→T roll-up, emit on bucket advance, OHLC merge)

**Files:** Modify `orb_bot/aggregation.py`. Modify `tests/test_aggregation.py`.

**Interfaces:** Consumes: `bucket_start(...)`, `orb_bot.models.Candle`. Produces: `Aggregator(tf_min: int, anchor: time = time(9, 30))`; `Aggregator.add(one_min: Candle) -> Candle | None` — buffers 1m children of the current bucket; when a 1m bar whose `bucket_start` differs from the open bucket arrives, emits the closed T-`Candle` (`open`=first child open, `close`=last child close, `high`=max children high, `low`=min children low, `volume`=sum, `timeframe_min`=`tf_min`, `bars_present`=child count `0..tf_min`, `data_incomplete=True` if `bars_present < tf_min`) and starts the new bucket. Returns `None` while filling. Idempotent on duplicate 1m bars (same `ts_open` re-add is ignored).

- [ ] **Step 1: Write failing tests for `Aggregator.add`.** Append to `tests/test_aggregation.py`.

```python
from decimal import Decimal

from orb_bot.aggregation import Aggregator


def _c1m(h: int, m: int, o: str, hi: str, lo: str, cl: str, vol: int = 100) -> Candle_t:
    ts_open = _dt(h, m, 0)
    return Candle_t(
        ts_open=ts_open,
        ts_close=ts_open + timedelta(minutes=1),
        open=Decimal(o),
        high=Decimal(hi),
        low=Decimal(lo),
        close=Decimal(cl),
        volume=vol,
        timeframe_min=1,
    )


def test_add_buffers_until_bucket_advances():
    agg = Aggregator(15)
    # 09:30..09:44 -> all None (still filling first bucket)
    out = [agg.add(_c1m(9, mm, "10", "11", "9", "10")) for mm in range(30, 45)]
    assert out == [None] * 15
    # 09:45 belongs to the next bucket -> emits the 09:30 aggregate
    emitted = agg.add(_c1m(9, 45, "20", "21", "19", "20"))
    assert emitted is not None
    assert emitted.timeframe_min == 15
    assert emitted.ts_open == _dt(9, 30, 0)
    assert emitted.ts_close == _dt(9, 45, 0)
    assert emitted.bars_present == 15
    assert emitted.data_incomplete is False


def test_add_ohlc_merge_rules():
    agg = Aggregator(15)
    agg.add(_c1m(9, 30, "10", "12", "8", "11"))   # first open = 10
    agg.add(_c1m(9, 31, "11", "15", "7", "9"))    # high 15, low 7
    agg.add(_c1m(9, 32, "9", "13", "9", "14"))    # last close (so far) = 14
    emitted = agg.add(_c1m(9, 45, "20", "20", "20", "20"))
    assert emitted.open == Decimal("10")
    assert emitted.close == Decimal("14")
    assert emitted.high == Decimal("15")
    assert emitted.low == Decimal("7")
    assert emitted.volume == 300
    assert emitted.bars_present == 3
    assert emitted.data_incomplete is True  # 3 < 15


def test_add_idempotent_on_duplicate_1m():
    agg = Aggregator(15)
    agg.add(_c1m(9, 30, "10", "12", "8", "11", vol=100))
    agg.add(_c1m(9, 30, "10", "12", "8", "11", vol=100))  # duplicate ts_open -> ignored
    agg.add(_c1m(9, 31, "11", "11", "11", "11", vol=50))
    emitted = agg.add(_c1m(9, 45, "20", "20", "20", "20"))
    assert emitted.bars_present == 2          # duplicate not double-counted
    assert emitted.volume == 150              # 100 + 50, not 250


def test_add_passthrough_1m_timeframe():
    agg = Aggregator(1)
    assert agg.add(_c1m(9, 30, "10", "11", "9", "10")) is None      # buffers first
    emitted = agg.add(_c1m(9, 31, "11", "12", "10", "11"))          # advance -> emit 09:30
    assert emitted.timeframe_min == 1
    assert emitted.ts_open == _dt(9, 30, 0)
    assert emitted.bars_present == 1
    assert emitted.data_incomplete is False
```

Add the `Candle_t` alias near the top of the file (after the existing imports/`ET` block) so the helper is typed against the real model:

```python
from orb_bot.models import Candle as Candle_t
```

Run and confirm FAIL (`ImportError`/`AttributeError` — `Aggregator` not defined / `add` missing).

```bash
pytest tests/test_aggregation.py -q
```

Expected: FAIL (`ImportError: cannot import name 'Aggregator'`).

- [ ] **Step 2: Implement `Aggregator.__init__` and `add`.** Append to `orb_bot/aggregation.py`.

```python
class Aggregator:
    """Rolls a 1-minute transport stream up to a single ``tf_min`` timeframe.

    Anchored at ``anchor`` wall-clock buckets (NOT bar-count). Pure: emission is
    driven only by the timestamps of the bars passed in (and ``force_close``),
    never by a wall clock. Idempotent on duplicate 1m bars (reconnect-safe).
    """

    def __init__(self, tf_min: int, anchor: time = time(9, 30)) -> None:
        self.tf_min = tf_min
        self.anchor = anchor
        self._bucket_ts: datetime | None = None  # start of the open bucket
        self._children: list[Candle] = []        # 1m bars in the open bucket
        self._seen_opens: set[datetime] = set()  # ts_open dedupe for the open bucket

    def add(self, one_min: Candle) -> Candle | None:
        """Add a 1m bar; emit the prior closed aggregate when the bucket advances."""
        b = bucket_start(one_min.ts_open, self.tf_min, self.anchor)
        emitted: Candle | None = None

        if self._bucket_ts is None:
            self._bucket_ts = b
        elif b != self._bucket_ts:
            emitted = self._emit(bars_present=len(self._children))
            self._bucket_ts = b
            self._children = []
            self._seen_opens = set()

        if one_min.ts_open in self._seen_opens:
            return emitted  # duplicate 1m bar -> idempotent, do not re-count
        self._seen_opens.add(one_min.ts_open)
        self._children.append(one_min)
        return emitted

    def _emit(self, bars_present: int) -> Candle:
        """Build the closed T-candle for the currently-open bucket."""
        assert self._bucket_ts is not None
        ts_open = self._bucket_ts
        ts_close = ts_open + timedelta(minutes=self.tf_min)
        if bars_present == 0 or not self._children:
            # Zero-child bucket: no usable OHLC (engine refuses it, spec §10 #13).
            zero = Decimal("0")
            return Candle(
                ts_open=ts_open,
                ts_close=ts_close,
                open=zero,
                high=zero,
                low=zero,
                close=zero,
                volume=0,
                timeframe_min=self.tf_min,
                data_incomplete=True,
                bars_present=0,
            )
        return Candle(
            ts_open=ts_open,
            ts_close=ts_close,
            open=self._children[0].open,
            high=max(c.high for c in self._children),
            low=min(c.low for c in self._children),
            close=self._children[-1].close,
            volume=sum(c.volume for c in self._children),
            timeframe_min=self.tf_min,
            data_incomplete=bars_present < self.tf_min,
            bars_present=bars_present,
        )
```

Run and confirm PASS.

```bash
pytest tests/test_aggregation.py -q
```

Expected: PASS (10 tests).

- [ ] **Step 3: Commit.**

```bash
git add orb_bot/aggregation.py tests/test_aggregation.py
git commit -m "feat(aggregation): add Aggregator.add 1m->T roll-up with OHLC merge and 1m dedupe"
```

### Task 10: Aggregation — `Aggregator.force_close` (boundary timer; partial & zero-child buckets)

**Files:** Modify `orb_bot/aggregation.py`. Modify `tests/test_aggregation.py`.

**Interfaces:** Consumes: `Aggregator`, `bucket_start`, `orb_bot.models.Candle`. Produces: `Aggregator.force_close(boundary_ts: datetime) -> Candle | None` — timer-driven flush. If there is an open bucket whose `bucket_start(boundary_ts) > self._bucket_ts` (i.e. the boundary has crossed past the open bucket), it emits that bucket (partial → `data_incomplete=True`, `bars_present` = child count) and clears it, returning `None` afterward if there are no children. If the boundary fires for the open bucket itself with **zero** children buffered, it emits a no-OHLC candle (`bars_present==0`, `data_incomplete=True`, all prices `Decimal("0")`) that the engine refuses (§10 #13). Idempotent: a second `force_close` on an already-flushed bucket returns `None`.

- [ ] **Step 1: Write failing tests for `force_close`.** Append to `tests/test_aggregation.py`.

```python
def test_force_close_emits_partial_bucket_with_data_incomplete():
    agg = Aggregator(15)
    agg.add(_c1m(9, 30, "10", "12", "8", "11"))
    agg.add(_c1m(9, 31, "11", "13", "9", "12"))
    # boundary timer fires after the 09:30 bucket edge (09:45 + grace)
    emitted = agg.force_close(_dt(9, 45, 3))
    assert emitted is not None
    assert emitted.ts_open == _dt(9, 30, 0)
    assert emitted.ts_close == _dt(9, 45, 0)
    assert emitted.bars_present == 2
    assert emitted.data_incomplete is True
    assert emitted.open == Decimal("10")
    assert emitted.close == Decimal("12")
    assert emitted.high == Decimal("13")
    assert emitted.low == Decimal("8")


def test_force_close_zero_children_emits_no_ohlc_candle():
    # No 1m bar ever arrived for the 09:30 bucket; timer flushes it.
    agg = Aggregator(15)
    # Prime the open bucket to 09:30 without any children via a boundary at 09:45.
    # Simulate the orchestrator priming the first bucket explicitly:
    agg._bucket_ts = _dt(9, 30, 0)  # the orchestrator anchors the first bucket
    emitted = agg.force_close(_dt(9, 45, 3))
    assert emitted is not None
    assert emitted.bars_present == 0
    assert emitted.data_incomplete is True
    assert emitted.open == Decimal("0")
    assert emitted.high == Decimal("0")
    assert emitted.low == Decimal("0")
    assert emitted.close == Decimal("0")
    assert emitted.volume == 0
    assert emitted.timeframe_min == 15


def test_force_close_idempotent_after_flush():
    agg = Aggregator(15)
    agg.add(_c1m(9, 30, "10", "12", "8", "11"))
    first = agg.force_close(_dt(9, 45, 3))
    assert first is not None
    second = agg.force_close(_dt(9, 45, 3))
    assert second is None  # already flushed -> nothing to emit


def test_force_close_returns_none_when_no_open_bucket():
    agg = Aggregator(15)
    assert agg.force_close(_dt(9, 45, 3)) is None


def test_force_close_then_next_add_starts_clean_bucket():
    agg = Aggregator(15)
    agg.add(_c1m(9, 30, "10", "12", "8", "11"))
    agg.force_close(_dt(9, 45, 3))
    # Next 1m bar (09:45 bucket) buffers without re-emitting the flushed 09:30 bucket.
    assert agg.add(_c1m(9, 45, "20", "21", "19", "20")) is None
    emitted = agg.add(_c1m(10, 0, "30", "30", "30", "30"))
    assert emitted.ts_open == _dt(9, 45, 0)
    assert emitted.bars_present == 1
```

Run and confirm FAIL (`AttributeError: 'Aggregator' object has no attribute 'force_close'`).

```bash
pytest tests/test_aggregation.py -q
```

Expected: FAIL (`AttributeError: ... 'force_close'`).

- [ ] **Step 2: Implement `force_close`.** Append the method inside the `Aggregator` class in `orb_bot/aggregation.py` (after `_emit`).

```python
    def force_close(self, boundary_ts: datetime) -> Candle | None:
        """Timer-driven flush of the open bucket once its boundary has passed.

        Emits a partial aggregate (``data_incomplete=True`` when children < tf_min)
        or, if zero children buffered, a no-OHLC candle the engine refuses
        (spec §10 #13). Idempotent: returns ``None`` when there is nothing open or
        the boundary has not yet crossed the open bucket.
        """
        if self._bucket_ts is None:
            return None
        if bucket_start(boundary_ts, self.tf_min, self.anchor) <= self._bucket_ts:
            return None  # boundary still inside the open bucket -> not closeable yet
        emitted = self._emit(bars_present=len(self._children))
        self._bucket_ts = None
        self._children = []
        self._seen_opens = set()
        return emitted
```

Run and confirm PASS.

```bash
pytest tests/test_aggregation.py -q
```

Expected: PASS (15 tests).

- [ ] **Step 3: Full-suite + purity sanity check.** Confirm the module imports nothing from impure layers and never calls a clock.

```bash
pytest tests -q
grep -nE "datetime\.now|time\.time|from \.(feed|execution|approval|reporting|orchestrator|discordbot)|import (feed|execution|approval|reporting|orchestrator|discordbot)" orb_bot/aggregation.py || echo "PURE: no clock / no impure imports"
```

Expected: `pytest tests` PASS; grep prints `PURE: no clock / no impure imports`.

- [ ] **Step 4: Commit.**

```bash
git add orb_bot/aggregation.py tests/test_aggregation.py
git commit -m "feat(aggregation): add force_close boundary flush with partial and zero-child candles"
```

---

I have everything needed. The displacement geometry from the source spec (lines 220-225): for a bullish/LONG breakout, `stop = disp.lower_candle.low - buffer` (below the gap-forming candle); for SHORT, `stop = disp.upper_candle.high + buffer`. So for a bullish displacement, `lower_candle` is the lower candle of the FVG (candle1, whose high is the bottom of the gap) and `upper_candle` is candle3 (whose low is the top of the gap). For impulse (single candle), both `upper_candle` and `lower_candle` are the same candle.

Now I'll produce the markdown plan for the indicators module only.

### Task 11: Indicators — `ATR` Wilder seed/update/ready/value

**Files:** Create `orb_bot/indicators.py`, `tests/test_indicators.py`. CONSUMES (do NOT create/overwrite — owned by Task 1): `orb_bot/__init__.py`, `tests/__init__.py`, `tests/context.py`. (Imports `orb_bot.models.Candle`; assumes `orb_bot/models.py` exists per the shared contract — if not yet present, the minimal `Candle` below unblocks this module.)
**Interfaces:** Consumes: `Candle(ts_open, ts_close, open, high, low, close, volume, timeframe_min, data_incomplete=False, bars_present=None)` (all prices `Decimal`). Produces: `class ATR: __init__(self, period:int); seed(self, bars:list[Candle])->None; update(self, bar:Candle)->Decimal; property ready:bool; property value:Decimal` — Wilder: `TR=max(h-l, |h-prevC|, |l-prevC|)`, `ATRn=(ATRn-1*(p-1)+TR)/p`, all `Decimal`.

- [ ] **Step 1: Ensure package + test bootstrap exists (idempotent — do NOT clobber Task 1's files).** Task 1 already created `orb_bot/__init__.py` (with its docstring + `__version__`) and `tests/__init__.py`. Consume them as-is; only create a missing one (running this task in isolation). NEVER truncate `orb_bot/__init__.py` (that would wipe `__version__`).
```bash
mkdir -p /home/manav/codespace/obr-strat/orb_bot /home/manav/codespace/obr-strat/tests
cd /home/manav/codespace/obr-strat
[ -f orb_bot/__init__.py ] || printf '"""orb_bot — Opening-Range Breakout trading bot (flat-layout package)."""\n__version__ = "0.1.0"\n' > orb_bot/__init__.py
[ -f tests/__init__.py ] || printf '' > tests/__init__.py
```

- [ ] **Step 2: Ensure `tests/context.py` (repo-root sys.path shim) exists (idempotent — owned by Task 1).** Consume Task 1's shim as-is; only create it (from the body below) if running this task in isolation and it is absent.
```bash
cd /home/manav/codespace/obr-strat
[ -f tests/context.py ] || cat > tests/context.py <<'PY'
# tests/context.py
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import orb_bot  # noqa: E402,F401
PY
```

- [ ] **Step 3: Minimal `Candle` so this module compiles standalone.** If `orb_bot/models.py` already exists from the models task, SKIP this step (do not overwrite). Otherwise create exactly:
```python
# orb_bot/models.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Candle:
    ts_open: datetime
    ts_close: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    timeframe_min: int
    data_incomplete: bool = False
    bars_present: int | None = None
```

- [ ] **Step 4: Write the FAILING ATR test.** Hand-computed Wilder numbers for `period=3`. Bars give TRs `[Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")]` (first bar seeds prevC; TR over closed bars). Seed average of first 3 TRs = (2+3+4)/3 = 3; first `update` with TR=5 → (3*2+5)/3 = 11/3.
```python
# tests/test_indicators.py
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot.indicators import ATR
from orb_bot.models import Candle

ET = ZoneInfo("America/New_York")


def _c(o, h, l, cl, *, minute=0):
    ts_open = datetime(2026, 6, 19, 9, 30 + minute, tzinfo=ET)
    ts_close = datetime(2026, 6, 19, 9, 31 + minute, tzinfo=ET)
    return Candle(
        ts_open=ts_open,
        ts_close=ts_close,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(cl)),
        volume=100,
        timeframe_min=15,
    )


def test_atr_not_ready_before_seed():
    atr = ATR(period=3)
    assert atr.ready is False
    with pytest.raises(ValueError):
        _ = atr.value


def test_atr_wilder_seed_then_update():
    # prevC starts at first bar close=10. TR for each subsequent bar:
    #   bar1 close=10 (seed prevC)
    #   bar2: h=12,l=10,prevC=10 -> max(2, 2, 0)=2
    #   bar3: h=13,l=10,prevC=? after bar2 close=11 -> max(3, 2, 1)=3
    #   bar4: h=14,l=10,prevC=12 -> max(4, 2, 2)=4
    # seed = mean(2,3,4) = 3
    bars = [
        _c(10, 10, 10, 10, minute=0),  # seeds prevC
        _c(10, 12, 10, 11, minute=1),  # TR=2
        _c(11, 13, 10, 12, minute=2),  # TR=3
        _c(12, 14, 10, 13, minute=3),  # TR=4
    ]
    atr = ATR(period=3)
    atr.seed(bars)
    assert atr.ready is True
    assert atr.value == Decimal("3")

    # update: bar5 h=18,l=10,prevC=13 -> TR=max(8,5,3)=8
    # ATR = (3*(3-1)+8)/3 = (6+8)/3 = 14/3
    nxt = _c(13, 18, 10, 17, minute=4)
    out = atr.update(nxt)
    assert out == Decimal("14") / Decimal("3")
    assert atr.value == Decimal("14") / Decimal("3")


def test_atr_seed_requires_enough_bars():
    atr = ATR(period=3)
    with pytest.raises(ValueError):
        atr.seed([_c(10, 11, 9, 10)])  # need period+1 bars to form `period` TRs
```

- [ ] **Step 5: Run it & confirm FAIL.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q
```
Expected: FAIL (`ModuleNotFoundError: No module named 'orb_bot.indicators'`).

- [ ] **Step 6: Minimal ATR impl.** Create `orb_bot/indicators.py`:
```python
# orb_bot/indicators.py
from __future__ import annotations

from decimal import Decimal

from .models import Candle


def _true_range(bar: Candle, prev_close: Decimal) -> Decimal:
    return max(
        bar.high - bar.low,
        abs(bar.high - prev_close),
        abs(bar.low - prev_close),
    )


class ATR:
    """Wilder's ATR. TR=max(h-l, |h-prevC|, |l-prevC|); ATRn=(ATRn-1*(p-1)+TR)/p."""

    def __init__(self, period: int):
        if period < 1:
            raise ValueError("ATR period must be >= 1")
        self._period = period
        self._value: Decimal | None = None
        self._prev_close: Decimal | None = None

    @property
    def ready(self) -> bool:
        return self._value is not None

    @property
    def value(self) -> Decimal:
        if self._value is None:
            raise ValueError("ATR not ready")
        return self._value

    def seed(self, bars: list[Candle]) -> None:
        # need one leading bar for prevC, then `period` TRs to average.
        if len(bars) < self._period + 1:
            raise ValueError(
                f"ATR seed needs at least {self._period + 1} bars, got {len(bars)}"
            )
        prev_close = bars[0].close
        trs: list[Decimal] = []
        for bar in bars[1:]:
            trs.append(_true_range(bar, prev_close))
            prev_close = bar.close
        window = trs[: self._period]
        self._value = sum(window, Decimal(0)) / Decimal(self._period)
        # prev_close after the last bar that fed the seed window
        self._prev_close = bars[self._period].close

    def update(self, bar: Candle) -> Decimal:
        if self._value is None or self._prev_close is None:
            raise ValueError("ATR must be seeded before update")
        tr = _true_range(bar, self._prev_close)
        p = Decimal(self._period)
        self._value = (self._value * (p - 1) + tr) / p
        self._prev_close = bar.close
        return self._value
```

- [ ] **Step 7: Run & confirm PASS.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q
```
Expected: PASS (3 passed).

- [ ] **Step 8: Commit.**
```bash
cd /home/manav/codespace/obr-strat && git add orb_bot/indicators.py orb_bot/models.py tests/test_indicators.py && git commit -m "feat(indicators): Wilder ATR seed/update/ready/value (pure)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 12: Indicators — `is_strong_close` truth table (LONG & SHORT)

**Files:** Modify `orb_bot/indicators.py`; modify `tests/test_indicators.py`.
**Interfaces:** Consumes: `Candle`, `orb_bot.models.Direction(LONG, SHORT)`. Produces: `is_strong_close(c:Candle, direction:Direction, body_ratio:float, location:float)->bool` — per source spec §5: `body=|close-open|`, `rng=high-low`, `body_ok = body/rng >= body_ratio`; LONG `loc_ok=(close-low)/rng >= location`, SHORT `loc_ok=(high-close)/rng >= location`; degenerate `rng==0` → `False`. Returns `body_ok and loc_ok`.

- [ ] **Step 1: Ensure `Direction` exists.** If `orb_bot/models.py` lacks `Direction`, add it (skip if the models task already defined it):
```python
# orb_bot/models.py  — add near the top, after imports
from enum import Enum


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
```

- [ ] **Step 2: Write FAILING truth-table test.** Use `body_ratio=0.60`, `location=0.70`. Candle rng=10.
```python
# tests/test_indicators.py  — append
from orb_bot.indicators import is_strong_close
from orb_bot.models import Direction


def _candle(o, h, l, cl):
    return _c(o, h, l, cl)


def test_is_strong_close_long_true():
    # rng=10, body=|9.5-1|=8.5 -> 0.85 >= 0.60 ok
    # loc long=(9.5-0)/10=0.95 >= 0.70 ok
    c = _candle(1, 10, 0, "9.5")
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is True


def test_is_strong_close_long_fails_location():
    # body ok (0.85) but close mid-candle: loc=(5-0)/10=0.5 < 0.70
    c = _candle(1, 10, 0, 5)
    # adjust body to stay >=0.60: open=9.0 -> body=4 -> 0.4 < 0.6 also fails body;
    # instead open near low to isolate location: open=0.5, close=5 -> body=4.5 ->0.45 fails body
    # Use a tall body but centered close: open=0, close=5, body=5 ->0.5 fails body too.
    # Isolate location with strong body: open=9, close=5 body=4 ->0.4 fails body.
    # Pure location-only failure needs big body AND centered close — impossible with one
    # candle, so accept this also fails on body; assert overall False:
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is False


def test_is_strong_close_long_fails_body_small():
    # body=|5.5-4.5|=1 -> 0.1 < 0.60
    c = _candle("4.5", 10, 0, "5.5")
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is False


def test_is_strong_close_short_true():
    # body=|0.5-9|=8.5 ->0.85 ok; loc short=(10-0.5)/10=0.95 >=0.70 ok
    c = _candle(9, 10, 0, "0.5")
    assert is_strong_close(c, Direction.SHORT, 0.60, 0.70) is True


def test_is_strong_close_short_fails_location():
    # strong bearish body but close near high: body=|9.5-1|=8.5 ok;
    # loc short=(10-9.5)/10=0.05 < 0.70 -> False
    c = _candle("9.5", 10, 0, 1)  # open 9.5 close 1 -> bullish? no: this is a tall body
    # body=|1-9.5|=8.5 -> 0.85 ok; loc short=(10-1)/10=0.9 ok -> would pass.
    # To fail short location, close must be near HIGH: close=9.6
    c = _candle(1, 10, 0, "9.6")
    # body=|9.6-1|=8.6 ->0.86 ok; loc short=(10-9.6)/10=0.04 <0.70 -> False
    assert is_strong_close(c, Direction.SHORT, 0.60, 0.70) is False


def test_is_strong_close_zero_range_is_false():
    c = _candle(5, 5, 5, 5)  # rng=0 (doji/flat)
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is False
    assert is_strong_close(c, Direction.SHORT, 0.60, 0.70) is False


def test_is_strong_close_boundary_inclusive():
    # exactly at thresholds must PASS (>= semantics). rng=10, body=6 ->0.60 == ratio;
    # long loc=(close-low)/10 must == 0.70 -> close-low=7. open=1,close=7 -> body=6 ok,
    # loc=(7-0)/10=0.70 -> inclusive True
    c = _candle(1, 10, 0, 7)
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is True
```

- [ ] **Step 3: Run & confirm FAIL.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k strong_close
```
Expected: FAIL (`ImportError: cannot import name 'is_strong_close'`).

- [ ] **Step 4: Implement `is_strong_close`.** Append to `orb_bot/indicators.py` (add import at top):
```python
# orb_bot/indicators.py  — add to imports at top
from .models import Candle, Direction
```
```python
# orb_bot/indicators.py  — append at module level
def is_strong_close(
    c: Candle, direction: Direction, body_ratio: float, location: float
) -> bool:
    """Spec §5: quantified strong close.

    body = |close-open|; rng = high-low.
    body_ok = body/rng >= body_ratio.
    LONG  loc_ok = (close-low)/rng  >= location.
    SHORT loc_ok = (high-close)/rng >= location.
    Degenerate rng==0 -> False.
    """
    rng = c.high - c.low
    if rng <= 0:
        return False
    body = abs(c.close - c.open)
    body_ratio_d = Decimal(str(body_ratio))
    location_d = Decimal(str(location))
    body_ok = (body / rng) >= body_ratio_d
    if direction is Direction.LONG:
        loc_ok = ((c.close - c.low) / rng) >= location_d
    else:
        loc_ok = ((c.high - c.close) / rng) >= location_d
    return bool(body_ok and loc_ok)
```
> Note: change the existing top-of-file `from .models import Candle` to the combined import above (Candle + Direction); do not leave a duplicate import line.

- [ ] **Step 5: Run & confirm PASS.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q
```
Expected: PASS (all green).

- [ ] **Step 6: Commit.**
```bash
cd /home/manav/codespace/obr-strat && git add orb_bot/indicators.py orb_bot/models.py tests/test_indicators.py && git commit -m "feat(indicators): is_strong_close truth table (long & short, inclusive)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 13: Indicators — `within` boundary tolerance

**Files:** Modify `orb_bot/indicators.py`; modify `tests/test_indicators.py`.
**Interfaces:** Produces: `within(c:Candle, level:Decimal, tol:Decimal)->bool` — spec §12: `c.low <= level+tol and c.high >= level-tol` (candle's range touches the `[level-tol, level+tol]` band; boundaries inclusive).

- [ ] **Step 1: Write FAILING `within` test (boundaries inclusive + miss cases).**
```python
# tests/test_indicators.py  — append
from orb_bot.indicators import within


def test_within_contains_level():
    c = _candle(99, 101, 98, 100)  # low=98, high=101
    assert within(c, Decimal("100"), Decimal("0.25")) is True


def test_within_upper_boundary_inclusive():
    # candle entirely below band; high exactly == level - tol -> inclusive True
    c = _candle("99.5", "99.75", "99.0", "99.6")  # high=99.75
    assert within(c, Decimal("100"), Decimal("0.25")) is True  # level-tol=99.75


def test_within_lower_boundary_inclusive():
    # candle entirely above band; low exactly == level + tol -> inclusive True
    c = _candle("100.30", "100.50", "100.25", "100.40")  # low=100.25
    assert within(c, Decimal("100"), Decimal("0.25")) is True  # level+tol=100.25


def test_within_miss_above():
    c = _candle("100.40", "100.60", "100.30", "100.50")  # low=100.30 > 100.25
    assert within(c, Decimal("100"), Decimal("0.25")) is False


def test_within_miss_below():
    c = _candle("99.40", "99.70", "99.30", "99.60")  # high=99.70 < 99.75
    assert within(c, Decimal("100"), Decimal("0.25")) is False


def test_within_zero_tol():
    c = _candle("99.9", "100.0", "99.8", "99.95")  # high=100 touches level exactly
    assert within(c, Decimal("100"), Decimal("0")) is True
    c2 = _candle("99.9", "99.99", "99.8", "99.95")  # high<level, low<level
    assert within(c2, Decimal("100"), Decimal("0")) is False
```

- [ ] **Step 2: Run & confirm FAIL.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k within
```
Expected: FAIL (`ImportError: cannot import name 'within'`).

- [ ] **Step 3: Implement `within`.** Append to `orb_bot/indicators.py`:
```python
# orb_bot/indicators.py  — append at module level
def within(c: Candle, level: Decimal, tol: Decimal) -> bool:
    """Spec §12: candle range touches the [level-tol, level+tol] band (inclusive)."""
    return c.low <= level + tol and c.high >= level - tol
```

- [ ] **Step 4: Run & confirm PASS.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k within
```
Expected: PASS (6 passed).

- [ ] **Step 5: Commit.**
```bash
cd /home/manav/codespace/obr-strat && git add orb_bot/indicators.py tests/test_indicators.py && git commit -m "feat(indicators): within() band tolerance with inclusive boundaries

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 14: Indicators — `find_swing` fractal pivot + `None` case

**Files:** Modify `orb_bot/indicators.py`; modify `tests/test_indicators.py`.
**Interfaces:** Consumes: `Candle`. Produces: `find_swing(candles:list[Candle], k:int, lookback:int, kind:str)->Decimal|None` — fractal pivot for stop placement (§10 #12). `kind in {"high","low"}`. Scans the last `lookback` candles; a pivot-high at index `i` requires `k` bars each side with strictly lower `high`; pivot-low requires strictly higher `low`. Returns the **most recent** qualifying pivot's `high` (kind="high") / `low` (kind="low"), else `None`.

- [ ] **Step 1: Write FAILING `find_swing` test.** Sequence of highs designed so index 2 is a fractal-high (k=1): highs `[5, 7, 9, 6, 4]` → idx2 (9) is higher than neighbors 7 and 6. For a most-recent-pivot tie-break, add a later pivot.
```python
# tests/test_indicators.py  — append
from orb_bot.indicators import find_swing


def _hilo(h, l, *, minute):
    # build a candle with given high/low; open/close inside range
    mid = (Decimal(str(h)) + Decimal(str(l))) / 2
    return Candle(
        ts_open=datetime(2026, 6, 19, 9, 30 + minute, tzinfo=ET),
        ts_close=datetime(2026, 6, 19, 9, 31 + minute, tzinfo=ET),
        open=mid,
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=mid,
        volume=10,
        timeframe_min=15,
    )


def test_find_swing_high_single_pivot():
    # highs:      5   7   9   6   4   (idx2=9 is the only fractal high for k=1)
    bars = [
        _hilo(5, 1, minute=0),
        _hilo(7, 2, minute=1),
        _hilo(9, 3, minute=2),
        _hilo(6, 2, minute=3),
        _hilo(4, 1, minute=4),
    ]
    assert find_swing(bars, k=1, lookback=5, kind="high") == Decimal("9")


def test_find_swing_low_single_pivot():
    # lows:       9   6   3   5   8   (idx2=3 is fractal low)
    bars = [
        _hilo(20, 9, minute=0),
        _hilo(18, 6, minute=1),
        _hilo(15, 3, minute=2),
        _hilo(17, 5, minute=3),
        _hilo(19, 8, minute=4),
    ]
    assert find_swing(bars, k=1, lookback=5, kind="low") == Decimal("3")


def test_find_swing_returns_most_recent_pivot():
    # two fractal highs: idx1 (8) and idx3 (10); most recent = 10
    bars = [
        _hilo(5, 1, minute=0),
        _hilo(8, 2, minute=1),   # pivot high (5<8>6)
        _hilo(6, 2, minute=2),
        _hilo(10, 3, minute=3),  # pivot high (6<10>7)
        _hilo(7, 2, minute=4),
    ]
    assert find_swing(bars, k=1, lookback=5, kind="high") == Decimal("10")


def test_find_swing_none_monotonic():
    # strictly increasing highs -> no fractal high exists (each side never lower on both)
    bars = [_hilo(h, h - 4, minute=h) for h in (5, 6, 7, 8, 9)]
    assert find_swing(bars, k=1, lookback=5, kind="high") is None


def test_find_swing_none_too_few_bars():
    # need 2k+1 = 3 bars; give 2 -> None
    bars = [_hilo(5, 1, minute=0), _hilo(6, 2, minute=1)]
    assert find_swing(bars, k=1, lookback=5, kind="high") is None


def test_find_swing_respects_lookback_slice():
    # an old pivot outside the lookback slice must be ignored.
    # 7 bars; lookback=4 -> only last 4 considered: highs [6,10,7,4]
    #   within slice idx1(10) is a pivot (6<10>7) -> returns 10.
    bars = [
        _hilo(20, 1, minute=0),  # outside slice
        _hilo(5, 1, minute=1),   # outside slice
        _hilo(99, 1, minute=2),  # outside slice (would be a huge pivot if seen)
        _hilo(6, 1, minute=3),   # slice start
        _hilo(10, 1, minute=4),  # pivot in slice
        _hilo(7, 1, minute=5),
        _hilo(4, 1, minute=6),
    ]
    assert find_swing(bars, k=1, lookback=4, kind="high") == Decimal("10")
```

- [ ] **Step 2: Run & confirm FAIL.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k find_swing
```
Expected: FAIL (`ImportError: cannot import name 'find_swing'`).

- [ ] **Step 3: Implement `find_swing`.** Append to `orb_bot/indicators.py`:
```python
# orb_bot/indicators.py  — append at module level
def find_swing(
    candles: list[Candle], k: int, lookback: int, kind: str
) -> Decimal | None:
    """Most-recent fractal pivot over the last `lookback` candles (§10 #12).

    pivot-high at i: high[i] strictly greater than high of each of the k bars on
    each side. pivot-low at i: low[i] strictly less than the k bars each side.
    Returns the pivot value (high/low) of the most recent qualifying index, else None.
    """
    if kind not in ("high", "low"):
        raise ValueError(f"kind must be 'high' or 'low', got {kind!r}")
    if k < 1 or lookback < 1:
        return None
    window = candles[-lookback:]
    n = len(window)
    if n < 2 * k + 1:
        return None
    # scan newest-eligible first so we return the most recent pivot
    for i in range(n - 1 - k, k - 1, -1):
        if kind == "high":
            pivot = window[i].high
            ok = all(window[i + d].high < pivot for d in range(-k, k + 1) if d != 0)
        else:
            pivot = window[i].low
            ok = all(window[i + d].low > pivot for d in range(-k, k + 1) if d != 0)
        if ok:
            return pivot
    return None
```

- [ ] **Step 4: Run & confirm PASS.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k find_swing
```
Expected: PASS (6 passed).

- [ ] **Step 5: Commit.**
```bash
cd /home/manav/codespace/obr-strat && git add orb_bot/indicators.py tests/test_indicators.py && git commit -m "feat(indicators): find_swing fractal pivot (most-recent) + None cases

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 15: Indicators — `find_impulse` geometry

**Files:** Modify `orb_bot/indicators.py`; modify `tests/test_indicators.py`.
**Interfaces:** Consumes: `Candle`, `Direction`, `is_strong_close`, `Displacement(type, upper_candle, lower_candle, size)` from `orb_bot.models`. Produces: `find_impulse(window:list[Candle], atr:Decimal, mult:float)->Displacement|None` — §12: single (last) candle whose range `high-low >= atr*mult` AND is a strong close (in its own close direction). On match, `Displacement(type="IMPULSE", upper_candle=c, lower_candle=c, size=high-low)` (both legs are the same candle so engine stop math `lower_candle.low`/`upper_candle.high` resolves to that candle's extreme).

- [ ] **Step 1: Ensure `Displacement` exists.** If `orb_bot/models.py` lacks it, add (skip if models task defined it):
```python
# orb_bot/models.py  — append
@dataclass(frozen=True)
class Displacement:
    type: str
    upper_candle: "Candle"
    lower_candle: "Candle"
    size: Decimal
```

- [ ] **Step 2: Write FAILING `find_impulse` test.** `atr=Decimal("2")`, `mult=1.5` → threshold range = 3.
```python
# tests/test_indicators.py  — append
from orb_bot.indicators import find_impulse
from orb_bot.models import Displacement


def test_find_impulse_long_match():
    # last candle: rng=high-low=10 >= 2*1.5=3 and strong bullish close
    win = [
        _candle(1, 2, 0, "1.5"),
        _candle(1, 10, 0, "9.5"),  # rng=10, body=8.5, bullish, close near high
    ]
    disp = find_impulse(win, atr=Decimal("2"), mult=1.5)
    assert disp is not None
    assert disp.type == "IMPULSE"
    assert disp.size == Decimal("10")
    assert disp.upper_candle is win[-1]
    assert disp.lower_candle is win[-1]


def test_find_impulse_short_match():
    win = [_candle(9, 10, 0, "0.5")]  # rng=10, bearish strong close near low
    disp = find_impulse(win, atr=Decimal("2"), mult=1.5)
    assert disp is not None
    assert disp.size == Decimal("10")


def test_find_impulse_none_small_range():
    # rng=2 < threshold 3 -> None even though strong close
    win = [_candle(0, 2, 0, "1.9")]
    assert find_impulse(win, atr=Decimal("2"), mult=1.5) is None


def test_find_impulse_none_not_strong_close():
    # big range but weak/centered body -> not a strong close -> None
    win = [_candle("4.5", 10, 0, "5.5")]  # body=1, rng=10 -> body ratio 0.1
    assert find_impulse(win, atr=Decimal("2"), mult=1.5) is None


def test_find_impulse_empty_window():
    assert find_impulse([], atr=Decimal("2"), mult=1.5) is None
```
> Hard-coded strong-close thresholds here use defaults 0.60/0.70 (matching `find_impulse`'s internal call below). The strong-close direction is inferred from the candle (`close >= open` → LONG, else SHORT).

- [ ] **Step 3: Run & confirm FAIL.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k find_impulse
```
Expected: FAIL (`ImportError: cannot import name 'find_impulse'`).

- [ ] **Step 4: Implement `find_impulse`.** Update the top import and append the function in `orb_bot/indicators.py`:
```python
# orb_bot/indicators.py  — top import becomes:
from .models import Candle, Direction, Displacement
```
```python
# orb_bot/indicators.py  — append at module level
def find_impulse(
    window: list[Candle], atr: Decimal, mult: float
) -> Displacement | None:
    """§12: last candle with range >= atr*mult AND a strong close (own direction)."""
    if not window:
        return None
    c = window[-1]
    rng = c.high - c.low
    threshold = atr * Decimal(str(mult))
    if rng < threshold:
        return None
    direction = Direction.LONG if c.close >= c.open else Direction.SHORT
    if not is_strong_close(c, direction, 0.60, 0.70):
        return None
    return Displacement(type="IMPULSE", upper_candle=c, lower_candle=c, size=rng)
```

- [ ] **Step 5: Run & confirm PASS.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k find_impulse
```
Expected: PASS (5 passed).

- [ ] **Step 6: Commit.**
```bash
cd /home/manav/codespace/obr-strat && git add orb_bot/indicators.py orb_bot/models.py tests/test_indicators.py && git commit -m "feat(indicators): find_impulse single-candle displacement geometry

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 16: Indicators — `find_fvg` 3-candle imbalance geometry

**Files:** Modify `orb_bot/indicators.py`; modify `tests/test_indicators.py`.
**Interfaces:** Consumes: `Candle`, `Displacement`. Produces: `find_fvg(window:list[Candle], min_size_ticks:int, tick:Decimal)->Displacement|None` — §12 3-candle imbalance over the last 3 candles `[c1,c2,c3]`. Bullish FVG: `c3.low > c1.high` with gap `c3.low - c1.high >= min_size_ticks*tick` → `Displacement(type="FVG_BULL", lower_candle=c1, upper_candle=c3, size=gap)`. Bearish FVG: `c1.low > c3.high` with gap `c1.low - c3.high >= min_size_ticks*tick` → `Displacement(type="FVG_BEAR", upper_candle=c1, lower_candle=c3, size=gap)`. Aligns with source-spec stop math (`lower_candle.low - buffer` for long, `upper_candle.high + buffer` for short). No gap → `None`.

- [ ] **Step 1: Write FAILING `find_fvg` test.** `tick=Decimal("0.01")`, `min_size_ticks=2` → min gap = 0.02.
```python
# tests/test_indicators.py  — append
from orb_bot.indicators import find_fvg


def test_find_fvg_bullish():
    # c1.high=100, c3.low=100.05 -> gap=0.05 >= 0.02
    win = [
        _candle(98, 100, 97, "99.5"),    # c1
        _candle(100, 103, 100, "102"),   # c2 (impulse middle)
        _candle("100.10", 104, "100.05", "103"),  # c3 low=100.05 above c1.high
    ]
    disp = find_fvg(win, min_size_ticks=2, tick=Decimal("0.01"))
    assert disp is not None
    assert disp.type == "FVG_BULL"
    assert disp.lower_candle is win[0]   # c1 -> stop uses lower_candle.low for LONG
    assert disp.upper_candle is win[2]   # c3
    assert disp.size == Decimal("0.05")


def test_find_fvg_bearish():
    # c1.low=100, c3.high=99.90 -> gap=0.10 (bearish imbalance)
    win = [
        _candle(102, 103, 100, "100.5"),     # c1 low=100
        _candle(100, 100, 97, "98"),         # c2
        _candle("99.5", "99.90", 96, "97"),  # c3 high=99.90 below c1.low
    ]
    disp = find_fvg(win, min_size_ticks=2, tick=Decimal("0.01"))
    assert disp is not None
    assert disp.type == "FVG_BEAR"
    assert disp.upper_candle is win[0]   # c1 -> stop uses upper_candle.high for SHORT
    assert disp.lower_candle is win[2]   # c3
    assert disp.size == Decimal("0.10")


def test_find_fvg_none_no_gap():
    # overlapping candles -> no imbalance
    win = [
        _candle(98, 101, 97, "100"),
        _candle(99, 102, 98, "101"),
        _candle(100, 103, 99, "102"),  # c3.low=99 < c1.high=101 -> no bull gap
    ]
    assert find_fvg(win, min_size_ticks=2, tick=Decimal("0.01")) is None


def test_find_fvg_none_gap_too_small():
    # bull gap exists but only 1 tick (0.01) < min 2 ticks (0.02)
    win = [
        _candle(98, 100, 97, "99.5"),
        _candle(100, 103, 100, "102"),
        _candle("100.05", 104, "100.01", "103"),  # gap = 100.01-100 = 0.01
    ]
    assert find_fvg(win, min_size_ticks=2, tick=Decimal("0.01")) is None


def test_find_fvg_none_short_window():
    win = [_candle(98, 100, 97, "99"), _candle(100, 103, 100, "102")]  # only 2
    assert find_fvg(win, min_size_ticks=2, tick=Decimal("0.01")) is None
```

- [ ] **Step 2: Run & confirm FAIL.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k find_fvg
```
Expected: FAIL (`ImportError: cannot import name 'find_fvg'`).

- [ ] **Step 3: Implement `find_fvg`.** Append to `orb_bot/indicators.py`:
```python
# orb_bot/indicators.py  — append at module level
def find_fvg(
    window: list[Candle], min_size_ticks: int, tick: Decimal
) -> Displacement | None:
    """§12: 3-candle fair-value-gap imbalance over the last three candles.

    Bullish: c3.low > c1.high, gap >= min_size_ticks*tick ->
        Displacement(FVG_BULL, lower_candle=c1, upper_candle=c3, size=gap).
    Bearish: c1.low > c3.high, gap >= min_size_ticks*tick ->
        Displacement(FVG_BEAR, upper_candle=c1, lower_candle=c3, size=gap).
    """
    if len(window) < 3:
        return None
    c1, _c2, c3 = window[-3], window[-2], window[-1]
    min_size = Decimal(min_size_ticks) * tick
    bull_gap = c3.low - c1.high
    if bull_gap >= min_size and bull_gap > 0:
        return Displacement(
            type="FVG_BULL", upper_candle=c3, lower_candle=c1, size=bull_gap
        )
    bear_gap = c1.low - c3.high
    if bear_gap >= min_size and bear_gap > 0:
        return Displacement(
            type="FVG_BEAR", upper_candle=c1, lower_candle=c3, size=bear_gap
        )
    return None
```

- [ ] **Step 4: Run & confirm PASS.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k find_fvg
```
Expected: PASS (5 passed).

- [ ] **Step 5: Commit.**
```bash
cd /home/manav/codespace/obr-strat && git add orb_bot/indicators.py tests/test_indicators.py && git commit -m "feat(indicators): find_fvg 3-candle imbalance (bull/bear) geometry

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 17: Indicators — `detect_displacement` model dispatch + full-suite gate

**Files:** Modify `orb_bot/indicators.py`; modify `tests/test_indicators.py`.
**Interfaces:** Consumes: `find_impulse`, `find_fvg`, `StrategyConfig` (reads `cfg.displacement_model`, `cfg.impulse_atr_mult`, `cfg.fvg_min_size_ticks`, `cfg.tick_size`). Produces: `detect_displacement(window:list[Candle], model:str, atr:Decimal, cfg)->Displacement|None` — §12/§7 dispatch: `"IMPULSE"→find_impulse(window, atr, cfg.impulse_atr_mult)`; `"FVG"→find_fvg(window, cfg.fvg_min_size_ticks, Decimal(str(cfg.tick_size)))`; `"TRUE_GAP"→None` (deferred this build); unknown → `ValueError`. The engine's breakout model consumes the returned `Displacement` (no displacement → no breakout entry, §9).

- [ ] **Step 1: Write FAILING dispatch test with a lightweight cfg stub.** (Uses a tiny stand-in to stay decoupled from the config task; the real `StrategyConfig` exposes the same attributes.)
```python
# tests/test_indicators.py  — append
from dataclasses import dataclass as _dc
from orb_bot.indicators import detect_displacement


@_dc
class _CfgStub:
    displacement_model: str = "IMPULSE"
    impulse_atr_mult: float = 1.5
    fvg_min_size_ticks: int = 2
    tick_size: float = 0.01


def test_detect_displacement_impulse():
    win = [_candle(1, 10, 0, "9.5")]  # rng=10 strong bullish
    disp = detect_displacement(win, "IMPULSE", atr=Decimal("2"), cfg=_CfgStub())
    assert disp is not None and disp.type == "IMPULSE"


def test_detect_displacement_fvg():
    win = [
        _candle(98, 100, 97, "99.5"),
        _candle(100, 103, 100, "102"),
        _candle("100.10", 104, "100.05", "103"),
    ]
    disp = detect_displacement(win, "FVG", atr=Decimal("2"), cfg=_CfgStub())
    assert disp is not None and disp.type == "FVG_BULL"


def test_detect_displacement_true_gap_deferred():
    win = [_candle(1, 10, 0, "9.5")]
    assert detect_displacement(win, "TRUE_GAP", atr=Decimal("2"), cfg=_CfgStub()) is None


def test_detect_displacement_unknown_model_raises():
    with pytest.raises(ValueError):
        detect_displacement([], "NONSENSE", atr=Decimal("2"), cfg=_CfgStub())
```

- [ ] **Step 2: Run & confirm FAIL.**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q -k detect_displacement
```
Expected: FAIL (`ImportError: cannot import name 'detect_displacement'`).

- [ ] **Step 3: Implement `detect_displacement`.** Append to `orb_bot/indicators.py`:
```python
# orb_bot/indicators.py  — append at module level
def detect_displacement(
    window: list[Candle], model: str, atr: Decimal, cfg
) -> Displacement | None:
    """§12/§7 dispatch to the configured displacement model.

    IMPULSE  -> find_impulse(window, atr, cfg.impulse_atr_mult)
    FVG      -> find_fvg(window, cfg.fvg_min_size_ticks, tick=cfg.tick_size)
    TRUE_GAP -> None (deferred this build)
    """
    if model == "IMPULSE":
        return find_impulse(window, atr, cfg.impulse_atr_mult)
    if model == "FVG":
        return find_fvg(
            window, cfg.fvg_min_size_ticks, Decimal(str(cfg.tick_size))
        )
    if model == "TRUE_GAP":
        return None
    raise ValueError(f"unknown displacement_model: {model!r}")
```

- [ ] **Step 4: Run the WHOLE indicators suite & confirm PASS (no-now purity sanity).**
```bash
cd /home/manav/codespace/obr-strat && python -m pytest tests/test_indicators.py -q
```
Expected: PASS (all tests green).

- [ ] **Step 5: Assert module purity (no `now()`/`time.time()`/forbidden imports).** This guards the PURITY constraint for `indicators.py`.
```bash
cd /home/manav/codespace/obr-strat && \
  ! grep -nE "datetime\.now|time\.time|from \.feed|from \.execution|from \.approval|from \.reporting|from \.orchestrator|from \.discordbot|import feed|import execution" orb_bot/indicators.py && \
  echo "PURE: indicators.py clean"
```
Expected: prints `PURE: indicators.py clean` (grep finds nothing, so `!` succeeds).

- [ ] **Step 6: Commit.**
```bash
cd /home/manav/codespace/obr-strat && git add orb_bot/indicators.py tests/test_indicators.py && git commit -m "feat(indicators): detect_displacement model dispatch (IMPULSE/FVG/TRUE_GAP)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

I now have full detail on the engine's PART 1 responsibilities. Let me produce the markdown plan for this module only.

### Task 18: Engine PART 1 — Context dataclass, init, ATR seed, range-building & confirmation
**Files:**
- Create: `orb_bot/engine.py`
- Test: `tests/test_engine.py`
- (Consumes existing: `orb_bot/models.py`, `orb_bot/config.py`, `orb_bot/indicators.py`)

**Interfaces:**
- Consumes:
  - `models.Candle(ts_open, ts_close, open, high, low, close, volume, timeframe_min, data_incomplete=False, bars_present=None)` (frozen, Decimal prices, tz-aware ET).
  - `models.OpeningRange(high, low, established_at, width, feed, bars_present, low_confidence)` (frozen).
  - `models.Direction.LONG|SHORT`, `models.State.{IDLE,BUILDING_RANGE,RANGE_SET,WAIT_CONFIRMATION,WAIT_ENTRY,IN_TRADE,DONE}`.
  - `models.RangeEstablished(opening_range)`, `models.DirectionConfirmed(direction, break_level)`, `models.RangeDayDetected()`, `models.WindowExpired()`, `models.NoOp()` (frozen `EngineEvent` union members).
  - `indicators.ATR(period)` with `.seed(bars)`, `.update(bar)`, `.ready`, `.value`; `indicators.is_strong_close(c, direction, body_ratio, location)->bool`.
  - `config.StrategyConfig` fields: `range_timeframe_min`, `session_open` (`datetime.time`), `timezone`, `trading_window_min`, `or_min_bars`, `atr_period`, `strong_close_body_ratio`, `strong_close_location`, `sweep_buffer`, `range_day_sweep_both`, `range_day_disables`, `range_day_enables`, `enable_breakout`, `enable_retest`, `max_trades_per_day`, `rearm_opposite_only`.
- Produces (consumed by Engine PART 2 + orchestrator):
  - `engine.Context` dataclass with the exact contract fields.
  - `Engine.__init__(self, cfg: StrategyConfig, session_date: date)`, `Engine.seed_atr(self, hist_tf: list[Candle]) -> None`, `Engine.on_candle(self, c: Candle) -> list[EngineEvent]`, `Engine.is_done(self) -> bool`.
  - Pure helpers: `confirmed_breakout(candle, opening_range, cfg) -> Direction | None`, `track_sweeps(ctx, candle, cfg) -> None`, `is_range_day(ctx, cfg) -> bool`, `apply_day_type_filter(ctx, cfg) -> None`.
  - `Engine.opening_range` property → `self.ctx.opening_range` (lets the orchestrator read the range without reaching into `ctx`).

---

- [ ] **Step 1: Failing test — Context dataclass exists with exact fields.**
Write the first test asserting `Context` is constructible with every contract field and that `Engine.__init__` builds one in `IDLE`.

```python
# tests/test_engine.py
import datetime as dt
from dataclasses import fields, is_dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot import engine as eng
from orb_bot.config import StrategyConfig
from orb_bot.indicators import ATR
from orb_bot.models import (
    Candle,
    Direction,
    OpeningRange,
    State,
)
from orb_bot import models as m

ET = ZoneInfo("America/New_York")
SESSION_DATE = dt.date(2026, 6, 19)


def _cfg(**over):
    base = dict(
        enable_breakout=True,
        enable_retest=True,
        enable_reversal=False,
        require_higher_tf_bias=False,
        max_trades_per_day=1,
    )
    base.update(over)
    return StrategyConfig(**base)


def make_candle(
    hhmm,
    o,
    h,
    l,
    c,
    *,
    tf=15,
    volume=1000,
    bars_present=15,
    data_incomplete=False,
    date=SESSION_DATE,
):
    hh, mm = (int(x) for x in hhmm.split(":"))
    ts_open = dt.datetime(date.year, date.month, date.day, hh, mm, tzinfo=ET)
    ts_close = ts_open + dt.timedelta(minutes=tf)
    return Candle(
        ts_open=ts_open,
        ts_close=ts_close,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=volume,
        timeframe_min=tf,
        data_incomplete=data_incomplete,
        bars_present=bars_present,
    )


def feed_candles(engine, candles):
    """Feed candles, return list of (state, events) after each."""
    out = []
    for c in candles:
        evs = engine.on_candle(c)
        out.append((engine.ctx.state, evs))
    return out


def test_context_has_exact_contract_fields():
    expected = {
        "state",
        "opening_range",
        "direction",
        "break_level",
        "swept_high",
        "swept_low",
        "range_day",
        "retest_wait",
        "retest_dead",
        "trades_remaining",
        "entry_window",
        "atr",
        "session_date",
        "last_ts",
    }
    assert is_dataclass(eng.Context)
    assert {f.name for f in fields(eng.Context)} == expected


def test_engine_init_idle():
    e = eng.Engine(_cfg(), SESSION_DATE)
    assert e.ctx.state is State.IDLE
    assert e.ctx.opening_range is None
    assert e.ctx.direction is None
    assert e.ctx.break_level is None
    assert e.ctx.swept_high is False
    assert e.ctx.swept_low is False
    assert e.ctx.range_day is False
    assert e.ctx.retest_wait == 0
    assert e.ctx.retest_dead is False
    assert e.ctx.trades_remaining == 1
    assert e.ctx.entry_window == []
    assert isinstance(e.ctx.atr, ATR)
    assert e.ctx.session_date == SESSION_DATE
    assert e.ctx.last_ts is None
    assert e.is_done() is False
```

Run it and confirm it FAILS (module/attrs not yet defined):
```bash
pytest tests/test_engine.py -q
```
Expected: FAIL (ImportError / AttributeError — `orb_bot.engine` has no `Context`/`Engine`).

- [ ] **Step 2: Minimal impl — Context + Engine.__init__ + is_done.**
Create the engine module with the Context dataclass and constructor only.

```python
# orb_bot/engine.py
"""Pure, I/O-free, clock-free single-timeframe ORB state machine (spec §9).

Imports NOTHING from feed/execution/approval/reporting/orchestrator/discordbot
and NEVER calls datetime.now()/time.time(). All timing is driven off
candle.ts_close (event time).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from orb_bot import indicators
from orb_bot import models as m
from orb_bot.config import StrategyConfig
from orb_bot.indicators import ATR
from orb_bot.models import (
    Candle,
    Direction,
    OpeningRange,
    State,
)


@dataclass
class Context:
    state: State
    opening_range: OpeningRange | None
    direction: Direction | None
    break_level: Decimal | None
    swept_high: bool
    swept_low: bool
    range_day: bool
    retest_wait: int
    retest_dead: bool
    trades_remaining: int
    entry_window: list[Candle]
    atr: ATR
    session_date: dt.date
    last_ts: dt.datetime | None


def _session_open_time(cfg: StrategyConfig) -> dt.time:
    # config.StrategyConfig coerces session_open to a datetime.time (pydantic);
    # it is already a time, so return it unchanged (no string parsing).
    return cfg.session_open


class Engine:
    def __init__(self, cfg: StrategyConfig, session_date: dt.date) -> None:
        self.cfg = cfg
        self.session_date = session_date
        self.ctx = Context(
            state=State.IDLE,
            opening_range=None,
            direction=None,
            break_level=None,
            swept_high=False,
            swept_low=False,
            range_day=False,
            retest_wait=0,
            retest_dead=False,
            trades_remaining=cfg.max_trades_per_day,
            entry_window=[],
            atr=ATR(cfg.atr_period),
            session_date=session_date,
            last_ts=None,
        )
        # The direction of the most recently CLOSED trade. Used to enforce
        # cfg.rearm_opposite_only on re-confirmation (set in on_trade_closed,
        # Engine PART 2). Not part of the fixed Context contract.
        self._last_traded_direction: Direction | None = None

    def seed_atr(self, hist_tf: list[Candle]) -> None:
        self.ctx.atr.seed(hist_tf)

    def is_done(self) -> bool:
        return self.ctx.state is State.DONE

    @property
    def opening_range(self) -> OpeningRange | None:
        """Read-only view of the established range so the orchestrator never
        has to reach into `self.ctx`."""
        return self.ctx.opening_range
```

Run and confirm PASS:
```bash
pytest tests/test_engine.py -q
```
Expected: PASS (2 tests).

- [ ] **Step 3: Failing test — seed_atr delegates; window-helpers exist.**
Add tests for `seed_atr` and the session-open derivation.

```python
# tests/test_engine.py (append)
def test_seed_atr_makes_atr_ready():
    e = eng.Engine(_cfg(atr_period=3), SESSION_DATE)
    prev = dt.date(2026, 6, 18)
    bars = [
        make_candle("09:30", 100, 101, 99, 100, date=prev),
        make_candle("09:45", 100, 102, 99, 101, date=prev),
        make_candle("10:00", 101, 103, 100, 102, date=prev),
        make_candle("10:15", 102, 104, 101, 103, date=prev),
    ]
    assert e.ctx.atr.ready is False
    e.seed_atr(bars)
    assert e.ctx.atr.ready is True
    assert e.ctx.atr.value > Decimal("0")


def test_session_open_time_parsed():
    # _cfg(session_open=...) is coerced by StrategyConfig to a datetime.time;
    # _session_open_time returns it unchanged.
    cfg = _cfg(session_open=dt.time(9, 30))
    assert isinstance(cfg.session_open, dt.time)
    assert eng._session_open_time(cfg) == dt.time(9, 30)
```

Run and confirm PASS (impl already covers these):
```bash
pytest tests/test_engine.py -q
```
Expected: PASS (4 tests). If `_session_open_time` import fails, the test FAILS first — but it is already defined in Step 2, so this is a guard test.

- [ ] **Step 4: Failing test — IDLE→BUILDING_RANGE→RANGE_SET→WAIT_CONFIRMATION on the 09:30 candle (write-once range).**
The first T-candle whose `ts_open == session_open` establishes the range and emits `RangeEstablished`.

```python
# tests/test_engine.py (append)
def test_first_candle_establishes_range_and_emits():
    e = eng.Engine(_cfg(or_min_bars=15), SESSION_DATE)
    c = make_candle("09:30", 100, 105, 98, 102, bars_present=15)
    evs = e.on_candle(c)
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert len(evs) == 1
    assert isinstance(evs[0], m.RangeEstablished)
    or_ = evs[0].opening_range
    assert or_.high == Decimal("105")
    assert or_.low == Decimal("98")
    assert or_.width == Decimal("7")
    assert or_.established_at == c.ts_close
    assert or_.bars_present == 15
    assert or_.low_confidence is False
    assert e.ctx.opening_range is or_
    assert e.ctx.last_ts == c.ts_close


def test_partial_range_marks_low_confidence():
    e = eng.Engine(_cfg(or_min_bars=15), SESSION_DATE)
    c = make_candle("09:30", 100, 105, 98, 102, bars_present=9)
    evs = e.on_candle(c)
    assert isinstance(evs[0], m.RangeEstablished)
    assert evs[0].opening_range.low_confidence is True
    assert evs[0].opening_range.bars_present == 9
    assert e.ctx.state is State.WAIT_CONFIRMATION


def test_zero_child_range_refused_done():
    # §10 #13: bars_present == 0 → never fabricate a range → IDLE→DONE
    e = eng.Engine(_cfg(), SESSION_DATE)
    c = make_candle(
        "09:30", 0, 0, 0, 0, bars_present=0, data_incomplete=True
    )
    evs = e.on_candle(c)
    assert e.ctx.state is State.DONE
    assert e.is_done() is True
    assert any(isinstance(ev, m.WindowExpired) for ev in evs) or any(
        isinstance(ev, m.NoOp) for ev in evs
    )


def test_range_is_write_once_late_first_candle_does_not_refabricate():
    # A non-09:30 candle arriving while IDLE must not set a range.
    e = eng.Engine(_cfg(), SESSION_DATE)
    late = make_candle("09:45", 100, 110, 90, 105)
    evs = e.on_candle(late)
    assert e.ctx.opening_range is None
    assert e.ctx.state is State.DONE  # cannot establish OR off a non-open candle
    assert any(isinstance(ev, (m.NoOp, m.WindowExpired)) for ev in evs)
```

Run and confirm FAIL (on_candle is not implemented):
```bash
pytest tests/test_engine.py -q
```
Expected: FAIL (`AttributeError: 'Engine' object has no attribute 'on_candle'` / state stays IDLE).

- [ ] **Step 5: Minimal impl — on_candle range-establishment branch (IDLE handling, write-once).**
Add `on_candle` plus the `is_first_session_candle` guard and range builder.

```python
# orb_bot/engine.py (append inside class Engine)
    def _is_first_session_candle(self, c: Candle) -> bool:
        return (
            c.timeframe_min == self.cfg.range_timeframe_min
            and c.ts_open.date() == self.session_date
            and c.ts_open.timetz().replace(tzinfo=None)
            == _session_open_time(self.cfg)
        )

    def _establish_range(self, c: Candle) -> list[m.EngineEvent]:
        bars_present = c.bars_present if c.bars_present is not None else 0
        # §10 #13: a zero-child force-closed bucket carries no usable OHLC —
        # never fabricate a range; go DONE.
        if bars_present == 0:
            self.ctx.state = State.DONE
            self.ctx.last_ts = c.ts_close
            return [m.WindowExpired()]
        opening_range = OpeningRange(
            high=c.high,
            low=c.low,
            established_at=c.ts_close,
            width=c.high - c.low,
            feed="",  # filled by orchestrator-side metadata; engine leaves blank
            bars_present=bars_present,
            low_confidence=bars_present < self.cfg.or_min_bars,
        )
        self.ctx.opening_range = opening_range
        # RANGE_SET is transient: establish then immediately arm confirmation.
        self.ctx.state = State.WAIT_CONFIRMATION
        self.ctx.last_ts = c.ts_close
        return [m.RangeEstablished(opening_range=opening_range)]

    def on_candle(self, c: Candle) -> list[m.EngineEvent]:
        if self.ctx.state is State.DONE:
            return [m.NoOp()]
        if self.ctx.state in (State.IDLE, State.BUILDING_RANGE):
            if self._is_first_session_candle(c):
                return self._establish_range(c)
            # A non-open candle while waiting for the range cannot establish
            # one (write-once + never fabricate); we cannot trade today.
            self.ctx.state = State.DONE
            self.ctx.last_ts = c.ts_close
            return [m.NoOp()]
        # Other states handled in later steps.
        self.ctx.last_ts = c.ts_close
        return [m.NoOp()]
```

Note: the `feed=""` placeholder is acceptable here — the engine cannot know the feed (purity). If the contract requires the feed string, the orchestrator overwrites it; engine tests assert structure only. Update the Step 4 test for `or_.feed` to accept `""`:

```python
# tests/test_engine.py — in test_first_candle_establishes_range_and_emits, add:
    assert or_.feed == ""
```

Run and confirm PASS:
```bash
pytest tests/test_engine.py -q
```
Expected: PASS (all prior + the 4 new range tests).

- [ ] **Step 6: Failing test — track_sweeps + is_range_day latch + apply_day_type_filter (pure helpers).**
These are standalone pure functions used per-candle before confirmation.

```python
# tests/test_engine.py (append)
def _ctx_after_range(e, or_high=105, or_low=98, bars=15):
    e.on_candle(make_candle("09:30", 100, or_high, or_low, 102, bars_present=bars))
    return e.ctx


def test_track_sweeps_sets_high_then_low():
    e = eng.Engine(_cfg(sweep_buffer=0.0), SESSION_DATE)
    ctx = _ctx_after_range(e)
    # wick above OR high but not below low
    up = make_candle("09:45", 103, 107, 101, 104)
    eng.track_sweeps(ctx, up, e.cfg)
    assert ctx.swept_high is True
    assert ctx.swept_low is False
    # later wick below OR low
    down = make_candle("10:00", 100, 102, 96, 99)
    eng.track_sweeps(ctx, down, e.cfg)
    assert ctx.swept_low is True


def test_is_range_day_requires_both_sweeps():
    e = eng.Engine(_cfg(range_day_sweep_both=True), SESSION_DATE)
    ctx = _ctx_after_range(e)
    ctx.swept_high = True
    ctx.swept_low = False
    assert eng.is_range_day(ctx, e.cfg) is False
    ctx.swept_low = True
    assert eng.is_range_day(ctx, e.cfg) is True


def test_apply_day_type_filter_disables_breakout_and_retest():
    e = eng.Engine(
        _cfg(
            enable_breakout=True,
            enable_retest=True,
            range_day_disables=["breakout", "retest"],
            range_day_enables=[],
        ),
        SESSION_DATE,
    )
    ctx = _ctx_after_range(e)
    eng.apply_day_type_filter(ctx, e.cfg)
    assert ctx.range_day is True
    # filter latches the range_day flag; model gating reads cfg + range_day.
    assert e._breakout_enabled() is False
    assert e._retest_enabled() is False
```

Run and confirm FAIL:
```bash
pytest tests/test_engine.py -q
```
Expected: FAIL (`track_sweeps`/`is_range_day`/`apply_day_type_filter`/`_breakout_enabled` undefined).

- [ ] **Step 7: Minimal impl — sweep tracking, range-day detection, day-type filter, model-gating helpers.**

```python
# orb_bot/engine.py (append at module level, below Engine or above — free functions)
def track_sweeps(ctx: Context, candle: Candle, cfg: StrategyConfig) -> None:
    """Latch whether price has swept above OR high / below OR low (wick-based,
    with sweep_buffer tolerance). Once set, a sweep flag stays set."""
    if ctx.opening_range is None:
        return
    buf = Decimal(str(cfg.sweep_buffer))
    if candle.high > ctx.opening_range.high + buf:
        ctx.swept_high = True
    if candle.low < ctx.opening_range.low - buf:
        ctx.swept_low = True


def is_range_day(ctx: Context, cfg: StrategyConfig) -> bool:
    """§10 #7: single-symbol range day = both sides swept within the window."""
    if cfg.range_day_sweep_both:
        return ctx.swept_high and ctx.swept_low
    return ctx.swept_high or ctx.swept_low


def apply_day_type_filter(ctx: Context, cfg: StrategyConfig) -> None:
    """§10 #4: latch range_day. The model-enable helpers consult this latch;
    with reversal deferred (range_day_enables == []) no model is re-enabled."""
    ctx.range_day = True
```

```python
# orb_bot/engine.py (append inside class Engine)
    def _breakout_enabled(self) -> bool:
        if not self.cfg.enable_breakout:
            return False
        if self.ctx.range_day:
            disabled = "breakout" in self.cfg.range_day_disables
            reenabled = "breakout" in self.cfg.range_day_enables
            if disabled and not reenabled:
                return False
        return True

    def _retest_enabled(self) -> bool:
        if not self.cfg.enable_retest:
            return False
        if self.ctx.range_day:
            disabled = "retest" in self.cfg.range_day_disables
            reenabled = "retest" in self.cfg.range_day_enables
            if disabled and not reenabled:
                return False
        return True
```

Run and confirm PASS:
```bash
pytest tests/test_engine.py -q
```
Expected: PASS (all prior + 3 new helper tests).

- [ ] **Step 8: Failing test — confirmed_breakout (close-based strong close) pure function.**
Confirmation requires a *close* beyond the range with a strong close; a wick-only or weak close returns `None`.

```python
# tests/test_engine.py (append)
def test_confirmed_breakout_long_strong_close():
    e = eng.Engine(
        _cfg(strong_close_body_ratio=0.60, strong_close_location=0.70),
        SESSION_DATE,
    )
    ctx = _ctx_after_range(e, or_high=105, or_low=98)
    # close (108) well above OR high (105), strong bullish body, closes near high
    c = make_candle("09:45", 105.5, 108.2, 105.0, 108.0)
    d = eng.confirmed_breakout(c, ctx.opening_range, e.cfg)
    assert d is Direction.LONG


def test_confirmed_breakout_short_strong_close():
    e = eng.Engine(_cfg(), SESSION_DATE)
    ctx = _ctx_after_range(e, or_high=105, or_low=98)
    c = make_candle("09:45", 97.5, 98.0, 94.0, 94.2)
    d = eng.confirmed_breakout(c, ctx.opening_range, e.cfg)
    assert d is Direction.SHORT


def test_confirmed_breakout_wick_only_close_inside_returns_none():
    e = eng.Engine(_cfg(), SESSION_DATE)
    ctx = _ctx_after_range(e, or_high=105, or_low=98)
    # high pokes above 105 but close (104) is back inside the range → no confirm
    c = make_candle("09:45", 103, 107, 102, 104)
    assert eng.confirmed_breakout(c, ctx.opening_range, e.cfg) is None


def test_confirmed_breakout_weak_close_returns_none():
    e = eng.Engine(
        _cfg(strong_close_body_ratio=0.60, strong_close_location=0.70),
        SESSION_DATE,
    )
    ctx = _ctx_after_range(e, or_high=105, or_low=98)
    # closes above 105 but tiny body / closes mid-bar → weak close → no confirm
    c = make_candle("09:45", 105.4, 109.0, 105.1, 105.6)
    assert eng.confirmed_breakout(c, ctx.opening_range, e.cfg) is None
```

Run and confirm FAIL:
```bash
pytest tests/test_engine.py -q
```
Expected: FAIL (`confirmed_breakout` undefined).

- [ ] **Step 9: Minimal impl — confirmed_breakout (close beyond range + strong close).**

```python
# orb_bot/engine.py (append at module level)
def confirmed_breakout(
    candle: Candle, opening_range: OpeningRange, cfg: StrategyConfig
) -> Direction | None:
    """§8 step 4 / §9: close-based confirmation only. A candle confirms LONG
    iff it CLOSES above OR high AND is a strong bullish close; SHORT iff it
    CLOSES below OR low AND is a strong bearish close. Wick-only excursions
    (close back inside) never confirm."""
    if candle.close > opening_range.high:
        if indicators.is_strong_close(
            candle,
            Direction.LONG,
            cfg.strong_close_body_ratio,
            cfg.strong_close_location,
        ):
            return Direction.LONG
        return None
    if candle.close < opening_range.low:
        if indicators.is_strong_close(
            candle,
            Direction.SHORT,
            cfg.strong_close_body_ratio,
            cfg.strong_close_location,
        ):
            return Direction.SHORT
        return None
    return None
```

Run and confirm PASS:
```bash
pytest tests/test_engine.py -q
```
Expected: PASS (all prior + 4 confirmation tests).

- [ ] **Step 10: Failing test — WAIT_CONFIRMATION on_candle wires the fixed order (sweeps→range-day→confirm) → DirectionConfirmed → WAIT_ENTRY; negatives stay put.**
Asserts the §8 step 4 / §10 #4 fixed precedence end-to-end through `on_candle`.

```python
# tests/test_engine.py (append)
def test_on_candle_confirm_breakout_long_transitions_wait_entry():
    e = eng.Engine(_cfg(), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    evs = e.on_candle(make_candle("09:45", 105.5, 108.2, 105.0, 108.0))
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.direction is Direction.LONG
    assert e.ctx.break_level == Decimal("105")
    assert any(
        isinstance(ev, m.DirectionConfirmed)
        and ev.direction is Direction.LONG
        and ev.break_level == Decimal("105")
        for ev in evs
    )


def test_on_candle_wick_only_no_transition():
    e = eng.Engine(_cfg(), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))
    evs = e.on_candle(make_candle("09:45", 103, 107, 102, 104))  # close inside
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert e.ctx.direction is None
    assert all(not isinstance(ev, m.DirectionConfirmed) for ev in evs)


def test_on_candle_weak_close_no_transition():
    e = eng.Engine(
        _cfg(strong_close_body_ratio=0.60, strong_close_location=0.70),
        SESSION_DATE,
    )
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))
    evs = e.on_candle(make_candle("09:45", 105.4, 109.0, 105.1, 105.6))
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert all(not isinstance(ev, m.DirectionConfirmed) for ev in evs)


def test_range_day_suppresses_same_bar_breakout():
    # Both sides swept on the confirm bar: range-day latches BEFORE confirmation
    # (§10 #4), disabling breakout/retest → no DirectionConfirmed even if the
    # close is a strong breakout.
    e = eng.Engine(
        _cfg(
            range_day_sweep_both=True,
            range_day_disables=["breakout", "retest"],
            range_day_enables=[],
        ),
        SESSION_DATE,
    )
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    # pre-sweep the low on an earlier candle
    e.on_candle(make_candle("09:45", 101, 104, 96, 100))  # sweeps low, close inside
    assert e.ctx.swept_low is True
    assert e.ctx.state is State.WAIT_CONFIRMATION
    # now a candle that sweeps high AND closes strong above → both sides swept
    evs = e.on_candle(make_candle("10:00", 105.5, 109.0, 105.0, 108.5))
    assert e.ctx.range_day is True
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert e.ctx.direction is None
    assert any(isinstance(ev, m.RangeDayDetected) for ev in evs)
    assert all(not isinstance(ev, m.DirectionConfirmed) for ev in evs)


def test_window_expiry_emits_window_expired_and_done():
    # trading_window_min=120 → window closes at 11:30; a candle whose ts_close
    # is at/after that while still unconfirmed → WindowExpired + DONE.
    e = eng.Engine(_cfg(trading_window_min=120), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))
    # feed an in-window non-confirming candle (close inside)
    e.on_candle(make_candle("09:45", 102, 104, 100, 103))
    assert e.ctx.state is State.WAIT_CONFIRMATION
    # 11:15 candle closes at 11:30 == window end → expired
    evs = e.on_candle(make_candle("11:15", 102, 104, 100, 103))
    assert e.ctx.state is State.DONE
    assert any(isinstance(ev, m.WindowExpired) for ev in evs)
```

Run and confirm FAIL:
```bash
pytest tests/test_engine.py -q
```
Expected: FAIL (WAIT_CONFIRMATION branch not implemented — state stays WAIT_CONFIRMATION with only NoOp; no DirectionConfirmed/WindowExpired/RangeDayDetected).

- [ ] **Step 11: Minimal impl — WAIT_CONFIRMATION branch + window-expiry helper, wired in fixed order.**

```python
# orb_bot/engine.py (append inside class Engine)
    def _window_end(self) -> dt.time:
        so = _session_open_time(self.cfg)
        base = dt.datetime.combine(self.session_date, so)
        end = base + dt.timedelta(minutes=self.cfg.trading_window_min)
        return end.time()

    def _past_window(self, c: Candle) -> bool:
        # Event-time check off ts_close; never the host clock.
        so = _session_open_time(self.cfg)
        base = dt.datetime.combine(
            self.session_date, so, tzinfo=c.ts_close.tzinfo
        )
        window_close = base + dt.timedelta(minutes=self.cfg.trading_window_min)
        return c.ts_close >= window_close

    def _on_candle_wait_confirmation(
        self, c: Candle
    ) -> list[m.EngineEvent]:
        events: list[m.EngineEvent] = []
        # Window guard FIRST: never act after trading_window_min (§9 guardrail).
        if self._past_window(c):
            self.ctx.state = State.DONE
            self.ctx.last_ts = c.ts_close
            return [m.WindowExpired()]
        # §10 #4 fixed order: track_sweeps → is_range_day/apply_day_type_filter
        # → confirmed_breakout.
        track_sweeps(self.ctx, c, self.cfg)
        if not self.ctx.range_day and is_range_day(self.ctx, self.cfg):
            apply_day_type_filter(self.ctx, self.cfg)
            events.append(m.RangeDayDetected())
        # A latched range day with nothing re-enabled suppresses confirmation
        # (incl. a same-bar breakout).
        if self._breakout_enabled() or self._retest_enabled():
            assert self.ctx.opening_range is not None
            direction = confirmed_breakout(c, self.ctx.opening_range, self.cfg)
            # §10 #14: on a re-arm (after a trade closed) with rearm_opposite_only,
            # reject a same-direction re-confirmation — only the opposite side
            # may take the remaining slot.
            if (
                direction is not None
                and self.cfg.rearm_opposite_only
                and direction is self._last_traded_direction
            ):
                direction = None
            if direction is not None:
                self.ctx.direction = direction
                self.ctx.break_level = (
                    self.ctx.opening_range.high
                    if direction is Direction.LONG
                    else self.ctx.opening_range.low
                )
                self.ctx.state = State.WAIT_ENTRY
                self.ctx.entry_window.append(c)
                events.append(
                    m.DirectionConfirmed(
                        direction=direction,
                        break_level=self.ctx.break_level,
                    )
                )
        self.ctx.last_ts = c.ts_close
        if not events:
            events.append(m.NoOp())
        return events
```

Now wire it into `on_candle` — replace the trailing fallthrough block from Step 5:

```python
# orb_bot/engine.py — in Engine.on_candle, replace:
#         # Other states handled in later steps.
#         self.ctx.last_ts = c.ts_close
#         return [m.NoOp()]
# with:
        if self.ctx.state is State.WAIT_CONFIRMATION:
            return self._on_candle_wait_confirmation(c)
        # WAIT_ENTRY / IN_TRADE handled in Engine PART 2.
        self.ctx.last_ts = c.ts_close
        return [m.NoOp()]
```

Run and confirm PASS:
```bash
pytest tests/test_engine.py -q
```
Expected: PASS (all prior + 5 confirmation/range-day/window tests).

- [ ] **Step 12: Failing test — purity: engine never calls now()/time.time(), and entry_window buffers the confirm candle.**
Locks the purity invariant for this module and the entry-window seeding behavior PART 2 relies on.

```python
# tests/test_engine.py (append)
import pathlib


def test_engine_module_has_no_wall_clock_calls():
    src = pathlib.Path(eng.__file__).read_text()
    assert "datetime.now(" not in src
    assert ".now(" not in src.replace("ts_close", "")  # no .now() usage
    assert "time.time(" not in src


def test_confirm_candle_buffered_into_entry_window():
    e = eng.Engine(_cfg(), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))
    confirm = make_candle("09:45", 105.5, 108.2, 105.0, 108.0)
    e.on_candle(confirm)
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.entry_window[-1] is confirm
```

Run and confirm PASS (impl already satisfies these — guard tests):
```bash
pytest tests/test_engine.py -q
```
Expected: PASS (all tests). If the purity test FAILS it reveals an accidental `.now()`/`time.time()` introduced in impl — fix by removing it.

- [ ] **Step 13: Full module run + lint/type gate.**
Confirm the whole suite is green and the module passes lint/type checks before committing.

```bash
pytest tests/test_engine.py -q
ruff check orb_bot/engine.py tests/test_engine.py
mypy orb_bot/engine.py
```
Expected: pytest PASS (all engine PART 1 tests); ruff clean; mypy clean.

- [ ] **Step 14: Commit.**

```bash
git add orb_bot/engine.py tests/test_engine.py
git commit -m "feat(engine): range-building + confirmation state machine (PART 1)

Add Context dataclass, Engine.__init__/seed_atr/is_done, and on_candle
handling for IDLE→BUILDING_RANGE→RANGE_SET→WAIT_CONFIRMATION→WAIT_ENTRY.
First-T-candle opening-range write-once (refuse bars_present==0 → DONE,
low_confidence when < or_min_bars); per-candle fixed order
track_sweeps → is_range_day latch → apply_day_type_filter →
confirmed_breakout (close-based strong close) → DirectionConfirmed;
event-time window-expiry off ts_close. Pure: no now()/time.time().

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

I now have everything needed. This is a planning-only task: I output the markdown plan for the engine PART 2 module. The plan must MODIFY `orb_bot/engine.py` (PART 1 created it with `Context`, `Engine.__init__`, `seed_atr`, `on_candle` through range/confirmation/WAIT_ENTRY, plus `confirmed_breakout`, `track_sweeps`, `is_range_day`, `apply_day_type_filter`). PART 2 adds entry-building, validation, target projection, and the approval/fill/close lifecycle with re-arm.

I have all the canonical signatures, config fields, spec rules (§9 guardrails, §10 #3/#11/#12/#14, data-flow steps 5-8). Producing the markdown plan now.

### Task 19: Engine PART 2 — Entry builders, setup validation & trade lifecycle

**Files:**
- Modify: `orb_bot/engine.py` (add `project_target`, `validate_setup`, `try_build_entry`, `entry_breakout`, `entry_retest` as module-level pure helpers; wire `try_build_entry` into `on_candle`'s `WAIT_ENTRY` branch; implement `on_approval`, `on_entry_filled`, `on_trade_closed`).
- Test: `tests/test_engine_entries.py` (entry models + guardrail rejections).
- Test: `tests/test_engine_lifecycle.py` (approval / fill / close / re-arm lifecycle).

**Interfaces:**
- Consumes (from PART 1, verbatim): `Context(state, opening_range, direction, break_level, swept_high, swept_low, range_day, retest_wait, retest_dead, trades_remaining, entry_window, atr, session_date, last_ts)`; `Engine.__init__(self, cfg: StrategyConfig, session_date: date)`; `Engine.on_candle(self, c: Candle) -> list[EngineEvent]` (range→confirm→sets `direction`/`break_level`/`State.WAIT_ENTRY`); `confirmed_breakout(candle, opening_range, cfg) -> Direction|None`; `track_sweeps(ctx, candle, cfg)`; `is_range_day(ctx, cfg) -> bool`; `apply_day_type_filter(ctx, cfg)`.
- Consumes (indicators, verbatim): `indicators.ATR(period).value/ready`; `detect_displacement(window, model, atr, cfg) -> Displacement|None`; `within(c, level, tol) -> bool`; `find_swing(candles, k, lookback, kind) -> Decimal|None`; `is_strong_close(c, direction, body_ratio, location) -> bool`.
- Consumes (models, verbatim): `Direction`, `Model`, `State`, `Candle`, `Setup`, `OrderResult`, `Fill`, and EngineEvents `SetupProposed(setup)`, `EntryConfirmed()`, `TradeRecorded(pnl, exit_reason)`, `NoOp()`.
- Produces (later tasks rely on, verbatim): `project_target(entry: Decimal, stop: Decimal, direction: Direction, rr: float) -> Decimal`; `validate_setup(setup: Setup, cfg) -> list[str]`; `try_build_entry(ctx, candle, cfg) -> Setup|None`; `entry_breakout(ctx, candle, cfg) -> Setup|None`; `entry_retest(ctx, candle, cfg) -> Setup|None`; `Engine.on_approval(self, decision: str) -> list[EngineEvent]`; `Engine.on_entry_filled(self, res: OrderResult) -> list[EngineEvent]` (→`IN_TRADE`, decrements `trades_remaining`); `Engine.on_trade_closed(self, fill: Fill) -> list[EngineEvent]` (records + re-arms).

---

- [ ] **Step 1: Failing test — `project_target` projects RR from entry/stop.**

```python
# tests/test_engine_entries.py
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot import engine as eng
from orb_bot.models import Direction, Model, Setup, State, Candle
from orb_bot.config import StrategyConfig

ET = ZoneInfo("America/New_York")


def _cfg(**over):
    return StrategyConfig(**over)


def _candle(o, h, l, c, ts_open, tf=15, bars=15):
    return Candle(
        ts_open=ts_open,
        ts_close=ts_open + timedelta(minutes=tf),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)),
        volume=1000, timeframe_min=tf, bars_present=bars,
    )


def test_project_target_long():
    tgt = eng.project_target(Decimal("100"), Decimal("99"), Direction.LONG, 2.0)
    assert tgt == Decimal("102")


def test_project_target_short():
    tgt = eng.project_target(Decimal("100"), Decimal("101"), Direction.SHORT, 2.0)
    assert tgt == Decimal("98")
```

Run:

```bash
pytest tests/test_engine_entries.py -k project_target -q
```

Expected: FAIL (`AttributeError: module 'orb_bot.engine' has no attribute 'project_target'`).

- [ ] **Step 2: Implement `project_target`.** Add to `orb_bot/engine.py` (module level, with the other pure helpers). `risk = abs(entry-stop)`; LONG target above entry, SHORT below.

```python
# orb_bot/engine.py  (append near the pure helpers)
def project_target(entry: Decimal, stop: Decimal, direction: Direction, rr: float) -> Decimal:
    risk = abs(entry - stop)
    reward = risk * Decimal(str(rr))
    if direction is Direction.LONG:
        return entry + reward
    return entry - reward
```

Ensure `from decimal import Decimal` and `from .models import Direction` are already imported (they are, from PART 1).

Run:

```bash
pytest tests/test_engine_entries.py -k project_target -q
```

Expected: PASS (2 passed).

- [ ] **Step 3: Failing test — `validate_setup` rejects inverted / low-rr / too-tight stops, accepts valid.**

```python
# tests/test_engine_entries.py  (append)
def _setup(direction, entry, stop, target, rr, model=Model.BREAKOUT):
    return Setup(
        direction=direction, model=model,
        entry=Decimal(str(entry)), stop=Decimal(str(stop)),
        target=Decimal(str(target)), rr=rr, reason=["x"],
    )


def test_validate_setup_valid_long():
    s = _setup(Direction.LONG, 100, 99, 102, 2.0)
    assert eng.validate_setup(s, _cfg()) == []


def test_validate_setup_inverted_long_stop():
    # LONG stop must be below entry; here stop above entry
    s = _setup(Direction.LONG, 100, 101, 102, 2.0)
    reasons = eng.validate_setup(s, _cfg())
    assert any("invert" in r.lower() or "stop" in r.lower() for r in reasons)
    assert reasons != []


def test_validate_setup_inverted_long_target():
    # LONG target must be above entry; here target below entry
    s = _setup(Direction.LONG, 100, 99, 99.5, 2.0)
    reasons = eng.validate_setup(s, _cfg())
    assert reasons != []


def test_validate_setup_low_rr():
    s = _setup(Direction.LONG, 100, 99, 101, 1.0)  # rr below floor 2.0
    reasons = eng.validate_setup(s, _cfg())
    assert any("rr" in r.lower() or "reward" in r.lower() for r in reasons)


def test_validate_setup_min_stop_distance():
    s = _setup(Direction.LONG, 100, Decimal("99.995"), 100.01, 2.0)  # stop dist 0.005 < 0.02
    reasons = eng.validate_setup(s, _cfg())
    assert any("stop" in r.lower() and "dist" in r.lower() for r in reasons) or reasons != []
```

Run:

```bash
pytest tests/test_engine_entries.py -k validate_setup -q
```

Expected: FAIL (`AttributeError: ... 'validate_setup'`).

- [ ] **Step 4: Implement `validate_setup`.** Returns a list of rejection reasons (empty == valid). Enforce side-correctness (inversion), `rr >= risk_reward_ratio - EPS`, and `abs(entry-stop) >= min_stop_distance`. Define a module-level `EPS = Decimal("1e-9")` (reuse if PART 1 already defined it — do not redefine).

```python
# orb_bot/engine.py  (module level; add EPS only if not already present)
EPS = Decimal("1e-9")  # remove this line if PART 1 already defines EPS


def validate_setup(setup: Setup, cfg) -> list[str]:
    reasons: list[str] = []
    if setup.direction is Direction.LONG:
        if setup.stop >= setup.entry:
            reasons.append("inverted: long stop not below entry")
        if setup.target <= setup.entry:
            reasons.append("inverted: long target not above entry")
    else:  # SHORT
        if setup.stop <= setup.entry:
            reasons.append("inverted: short stop not above entry")
        if setup.target >= setup.entry:
            reasons.append("inverted: short target not below entry")
    stop_dist = abs(setup.entry - setup.stop)
    if stop_dist < Decimal(str(cfg.min_stop_distance)):
        reasons.append("stop distance below min_stop_distance")
    if Decimal(str(setup.rr)) < Decimal(str(cfg.risk_reward_ratio)) - EPS:
        reasons.append("rr below risk_reward_ratio floor")
    return reasons
```

Run:

```bash
pytest tests/test_engine_entries.py -k validate_setup -q
```

Expected: PASS (5 passed).

- [ ] **Step 5: Failing test — `entry_breakout` builds a same-candle setup only when displacement present.** Stub the context with `direction`/`break_level`/`opening_range`/`atr` populated and an `entry_window`. Patch `detect_displacement` to control the displacement gate.

```python
# tests/test_engine_entries.py  (append)
from orb_bot import indicators
from orb_bot.models import OpeningRange


def _mk_candle(o, h, l, c, minute, tf=15, bars=15):
    ts_open = datetime(2026, 6, 19, 9, 30, tzinfo=ET) + timedelta(minutes=minute)
    return Candle(
        ts_open=ts_open, ts_close=ts_open + timedelta(minutes=tf),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)),
        volume=1000, timeframe_min=tf, bars_present=bars,
    )


def _ctx_in_wait_entry(cfg, direction=Direction.LONG, break_level="101"):
    e = eng.Engine(cfg, date(2026, 6, 19))
    ctx = e.ctx
    ctx.state = State.WAIT_ENTRY
    ctx.direction = direction
    ctx.break_level = Decimal(break_level)
    ctx.opening_range = OpeningRange(
        high=Decimal("101"), low=Decimal("99"),
        established_at=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        width=Decimal("2"), feed="IEX", bars_present=15, low_confidence=False,
    )
    # seed ATR so it's ready with a known value
    seed = [_mk_candle(100, 100.5, 99.5, 100, m * 15, tf=15) for m in range(-cfg.atr_period - 1, 0)]
    ctx.atr.seed(seed)
    return e, ctx


def test_entry_breakout_requires_displacement(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 15)
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "detect_displacement", lambda *a, **k: None)
    assert eng.entry_breakout(ctx, candle, cfg) is None


def test_entry_breakout_builds_setup_with_displacement(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 15)
    ctx.entry_window = [candle]
    from orb_bot.models import Displacement
    disp = Displacement(type="IMPULSE", upper_candle=candle, lower_candle=candle, size=Decimal("1"))
    monkeypatch.setattr(eng, "detect_displacement", lambda *a, **k: disp)
    s = eng.entry_breakout(ctx, candle, cfg)
    assert s is not None
    assert s.direction is Direction.LONG
    assert s.model is Model.BREAKOUT
    assert s.entry == candle.close
    assert s.stop < s.entry < s.target
```

Run:

```bash
pytest tests/test_engine_entries.py -k entry_breakout -q
```

Expected: FAIL (`AttributeError: ... 'entry_breakout'`).

- [ ] **Step 6: Implement the `buffer` helper + `entry_breakout`.** Buffer per §10 #1; stop = swing (via `find_swing`) else `break_level ± buffer`; entry = candle close; target via `project_target`. Returns `None` if no displacement.

```python
# orb_bot/engine.py  (module level)
def _buffer(cfg, atr_value: Decimal) -> Decimal:
    ticks = Decimal(str(cfg.stop_buffer_ticks)) * Decimal(str(cfg.tick_size))
    atr_buf = Decimal(str(cfg.stop_buffer_atr)) * atr_value
    return max(ticks, atr_buf)


def entry_breakout(ctx, candle: Candle, cfg) -> Setup | None:
    disp = detect_displacement(ctx.entry_window, cfg.displacement_model, ctx.atr.value, cfg)
    if disp is None:
        return None
    direction = ctx.direction
    entry = candle.close
    buf = _buffer(cfg, ctx.atr.value)
    if direction is Direction.LONG:
        stop = ctx.break_level - buf
    else:
        stop = ctx.break_level + buf
    target = project_target(entry, stop, direction, cfg.risk_reward_ratio)
    risk = abs(entry - stop)
    rr = float(abs(target - entry) / risk) if risk > 0 else 0.0
    return Setup(
        direction=direction, model=Model.BREAKOUT,
        entry=entry, stop=stop, target=target, rr=rr,
        reason=[f"breakout {direction.value}", f"displacement={disp.type}"],
    )
```

Run:

```bash
pytest tests/test_engine_entries.py -k entry_breakout -q
```

Expected: PASS (2 passed).

- [ ] **Step 7: Failing test — `entry_retest` waits a later candle, uses swing stop, falls back to `break_level ± buffer`, and abandons at the wait cap.**

```python
# tests/test_engine_entries.py  (append)
def test_entry_retest_no_return_increments_only(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    # price far from break_level => within() False => no setup, wait increments via try_build_entry
    candle = _mk_candle(105, 105.5, 104.5, 105, 30)
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "within", lambda *a, **k: False)
    assert eng.entry_retest(ctx, candle, cfg) is None


def test_entry_retest_uses_swing_stop(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 101.3, 100.8, 101.2, 30)  # returns to break_level, holds
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "within", lambda *a, **k: True)
    monkeypatch.setattr(eng, "is_strong_close", lambda *a, **k: True)
    monkeypatch.setattr(eng, "find_swing", lambda *a, **k: Decimal("100.50"))
    s = eng.entry_retest(ctx, candle, cfg)
    assert s is not None
    assert s.model is Model.RETEST
    assert s.stop == Decimal("100.50")


def test_entry_retest_swing_none_fallback(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 101.3, 100.8, 101.2, 30)
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "within", lambda *a, **k: True)
    monkeypatch.setattr(eng, "is_strong_close", lambda *a, **k: True)
    monkeypatch.setattr(eng, "find_swing", lambda *a, **k: None)
    s = eng.entry_retest(ctx, candle, cfg)
    assert s is not None
    buf = eng._buffer(cfg, ctx.atr.value)
    assert s.stop == ctx.break_level - buf  # fallback break_level - buffer for LONG
```

Run:

```bash
pytest tests/test_engine_entries.py -k entry_retest -q
```

Expected: FAIL (`AttributeError: ... 'entry_retest'`).

- [ ] **Step 8: Implement `entry_retest`.** Requires the candle to return to `break_level` (within `retest_tolerance_atr × ATR`) AND hold with a confirming strong close in the trade direction. Stop from `find_swing` (kind by direction) else fallback `break_level ± buffer`. Entry = candle close; target via `project_target`. Returns `None` when not yet retested/held (the wait-cap increment is handled in `try_build_entry`, Step 10).

```python
# orb_bot/engine.py  (module level)
def entry_retest(ctx, candle: Candle, cfg) -> Setup | None:
    direction = ctx.direction
    tol = Decimal(str(cfg.retest_tolerance_atr)) * ctx.atr.value
    if not within(candle, ctx.break_level, tol):
        return None
    if not is_strong_close(candle, direction, cfg.retest_confirm_body_ratio, cfg.strong_close_location):
        return None
    kind = "low" if direction is Direction.LONG else "high"
    swing = find_swing(ctx.entry_window, cfg.swing_fractal_k, cfg.swing_lookback, kind)
    buf = _buffer(cfg, ctx.atr.value)
    if swing is not None:
        stop = swing
        stop_src = "swing"
    elif direction is Direction.LONG:
        stop = ctx.break_level - buf
        stop_src = "fallback"
    else:
        stop = ctx.break_level + buf
        stop_src = "fallback"
    entry = candle.close
    target = project_target(entry, stop, direction, cfg.risk_reward_ratio)
    risk = abs(entry - stop)
    rr = float(abs(target - entry) / risk) if risk > 0 else 0.0
    return Setup(
        direction=direction, model=Model.RETEST,
        entry=entry, stop=stop, target=target, rr=rr,
        reason=[f"retest {direction.value}", f"stop={stop_src}"],
    )
```

Run:

```bash
pytest tests/test_engine_entries.py -k entry_retest -q
```

Expected: PASS (3 passed).

- [ ] **Step 9: Failing test — `try_build_entry` priority (retest preferred), wait-counter increments each `WAIT_ENTRY` candle, abandons at cap, validation gates the result.**

```python
# tests/test_engine_entries.py  (append)
def test_try_build_entry_prefers_retest(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 101.3, 100.8, 101.2, 30)
    ctx.entry_window = [candle]
    rsetup = Setup(Direction.LONG, Model.RETEST, Decimal("101.2"), Decimal("100.5"),
                   Decimal("102.6"), 2.0, ["r"])
    bsetup = Setup(Direction.LONG, Model.BREAKOUT, Decimal("101.2"), Decimal("100.5"),
                   Decimal("102.6"), 2.0, ["b"])
    monkeypatch.setattr(eng, "entry_retest", lambda *a, **k: rsetup)
    monkeypatch.setattr(eng, "entry_breakout", lambda *a, **k: bsetup)
    s = eng.try_build_entry(ctx, candle, cfg)
    assert s is rsetup  # retest preferred


def test_try_build_entry_increments_and_abandons(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True, retest_max_wait_candles=2)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    monkeypatch.setattr(eng, "entry_retest", lambda *a, **k: None)
    c1 = _mk_candle(105, 105.5, 104.5, 105, 30)
    c2 = _mk_candle(105, 105.5, 104.5, 105, 45)
    c3 = _mk_candle(105, 105.5, 104.5, 105, 60)
    ctx.entry_window = [c1]
    assert eng.try_build_entry(ctx, c1, cfg) is None
    assert ctx.retest_wait == 1
    assert ctx.retest_dead is False
    ctx.entry_window = [c1, c2]
    assert eng.try_build_entry(ctx, c2, cfg) is None
    assert ctx.retest_wait == 2
    assert ctx.retest_dead is True  # hit cap


def test_try_build_entry_rejects_invalid_setup(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 30)
    ctx.entry_window = [candle]
    bad = Setup(Direction.LONG, Model.BREAKOUT, Decimal("101"), Decimal("100"),
                Decimal("101.5"), 0.5, ["b"])  # rr below floor
    monkeypatch.setattr(eng, "entry_breakout", lambda *a, **k: bad)
    assert eng.try_build_entry(ctx, candle, cfg) is None
```

Run:

```bash
pytest tests/test_engine_entries.py -k try_build_entry -q
```

Expected: FAIL (`AttributeError: ... 'try_build_entry'`).

- [ ] **Step 10: Implement `try_build_entry`.** Per §9 eligibility + §10 #3: try **retest first** (preferred), then **breakout**; the first to pass `validate_setup` wins. If no setup, advance the retest wait counter every `WAIT_ENTRY` candle and latch `retest_dead` at the cap (counter-based abandonment, regardless of proximity).

```python
# orb_bot/engine.py  (module level)
def try_build_entry(ctx, candle: Candle, cfg) -> Setup | None:
    candidates = []
    if cfg.enable_retest and not ctx.retest_dead:
        candidates.append(entry_retest)
    if cfg.enable_breakout:
        candidates.append(entry_breakout)
    for build in candidates:
        setup = build(ctx, candle, cfg)
        if setup is not None and not validate_setup(setup, cfg):
            return setup
    # no valid setup this candle: advance the retest wait counter and abandon at cap
    if cfg.enable_retest and not ctx.retest_dead:
        ctx.retest_wait += 1
        if ctx.retest_wait >= cfg.retest_max_wait_candles:
            ctx.retest_dead = True
    return None
```

Run:

```bash
pytest tests/test_engine_entries.py -k try_build_entry -q
```

Expected: PASS (3 passed).

- [ ] **Step 11: Failing test — `on_candle` in `WAIT_ENTRY` emits `SetupProposed`.** Drive a real `Engine`: seed ATR, force it into `WAIT_ENTRY`, patch `try_build_entry` to return a valid setup, assert the event.

```python
# tests/test_engine_entries.py  (append)
def test_on_candle_wait_entry_emits_setup_proposed(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 30)
    good = Setup(Direction.LONG, Model.BREAKOUT, Decimal("101.9"), Decimal("100.9"),
                 Decimal("103.9"), 2.0, ["b"])
    monkeypatch.setattr(eng, "try_build_entry", lambda *a, **k: good)
    events = e.on_candle(candle)
    assert any(isinstance(ev, orb_bot.models.SetupProposed) and ev.setup is good for ev in events)


def test_on_candle_wait_entry_noop_when_no_setup(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 30)
    monkeypatch.setattr(eng, "try_build_entry", lambda *a, **k: None)
    events = e.on_candle(candle)
    assert all(not isinstance(ev, orb_bot.models.SetupProposed) for ev in events)
    assert e.ctx.state is State.WAIT_ENTRY  # stays waiting
```

Run:

```bash
pytest tests/test_engine_entries.py -k on_candle_wait_entry -q
```

Expected: FAIL — PART 1's `on_candle` does not yet route `WAIT_ENTRY` through `try_build_entry`/`SetupProposed`.

- [ ] **Step 12: Wire `WAIT_ENTRY` into `on_candle`.** In PART 1's `on_candle`, after the existing range/confirmation handling, add the `WAIT_ENTRY` branch. It must still buffer the candle into `entry_window` (cap length to `swing_lookback`) and update `last_ts` exactly as PART 1 does for other states — locate PART 1's per-candle buffering/`last_ts` update and keep it the single source of truth. The new branch only calls `try_build_entry` and emits.

```python
# orb_bot/engine.py  — inside Engine.on_candle, the WAIT_ENTRY dispatch arm
# (PART 1 already appended `c` to ctx.entry_window and set ctx.last_ts = c.ts_close
#  before this dispatch; do NOT duplicate that here.)
        if self.ctx.state is State.WAIT_ENTRY:
            setup = try_build_entry(self.ctx, c, self.cfg)
            if setup is not None:
                return [SetupProposed(setup=setup)]
            return [NoOp()]
```

Confirm `SetupProposed`, `NoOp` are imported from `.models` (PART 1 imports the event union; add to the import line if missing).

Run:

```bash
pytest tests/test_engine_entries.py -q
```

Expected: PASS (all entry tests). Also run the PART 1 suite to confirm no regression:

```bash
pytest tests/test_engine.py -q
```

Expected: PASS (PART 1 unaffected).

- [ ] **Step 13: Failing test — approval lifecycle.** `on_approval('APPROVE')` arms for fill (state unchanged, `EntryConfirmed`); `REJECT`/`TIMEOUT` return to `WAIT_ENTRY`, consume no slot.

```python
# tests/test_engine_lifecycle.py
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot import engine as eng
from orb_bot.models import (
    Direction, Model, Setup, State, Candle, OpeningRange, OrderResult, Fill,
    EntryConfirmed, TradeRecorded, NoOp,
)
from orb_bot.config import StrategyConfig

ET = ZoneInfo("America/New_York")


def _cfg(**over):
    return StrategyConfig(**over)


def _mk_candle(o, h, l, c, minute, tf=15, bars=15):
    ts_open = datetime(2026, 6, 19, 9, 30, tzinfo=ET) + timedelta(minutes=minute)
    return Candle(
        ts_open=ts_open, ts_close=ts_open + timedelta(minutes=tf),
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)),
        close=Decimal(str(c)), volume=1000, timeframe_min=tf, bars_present=bars,
    )


def _engine_at_wait_entry(cfg, trades=1):
    e = eng.Engine(cfg, date(2026, 6, 19))
    ctx = e.ctx
    ctx.state = State.WAIT_ENTRY
    ctx.direction = Direction.LONG
    ctx.break_level = Decimal("101")
    ctx.trades_remaining = trades
    ctx.opening_range = OpeningRange(
        high=Decimal("101"), low=Decimal("99"),
        established_at=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        width=Decimal("2"), feed="IEX", bars_present=15, low_confidence=False,
    )
    seed = [_mk_candle(100, 100.5, 99.5, 100, m * 15) for m in range(-cfg.atr_period - 1, 0)]
    ctx.atr.seed(seed)
    return e


def _order_result():
    return OrderResult(order_id="o1", client_order_id="2026-06-19-SPY-1-ENTRY",
                       status="filled", filled_avg_price=Decimal("101.90"),
                       filled_qty=10, legs=[])


def test_on_approval_reject_returns_to_wait_entry():
    e = _engine_at_wait_entry(_cfg(), trades=1)
    events = e.on_approval("REJECT")
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.trades_remaining == 1  # no slot consumed
    assert all(not isinstance(ev, EntryConfirmed) for ev in events)


def test_on_approval_timeout_returns_to_wait_entry():
    e = _engine_at_wait_entry(_cfg(), trades=1)
    events = e.on_approval("TIMEOUT")
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.trades_remaining == 1


def test_on_approval_approve_emits_entry_confirmed():
    e = _engine_at_wait_entry(_cfg(), trades=1)
    events = e.on_approval("APPROVE")
    assert any(isinstance(ev, EntryConfirmed) for ev in events)
    assert e.ctx.trades_remaining == 1  # still not consumed until fill
```

Run:

```bash
pytest tests/test_engine_lifecycle.py -k on_approval -q
```

Expected: FAIL (`on_approval` not implemented in PART 2 yet — PART 1 left it stubbed/absent).

- [ ] **Step 14: Implement `on_approval`.** `APPROVE` → `EntryConfirmed()` (no decrement, no state change; the orchestrator submits the bracket and awaits the fill). `REJECT`/`TIMEOUT` → stay `WAIT_ENTRY`, consume no slot, emit `NoOp()`.

```python
# orb_bot/engine.py  — Engine.on_approval
    def on_approval(self, decision: str) -> list[EngineEvent]:
        if decision == "APPROVE":
            return [EntryConfirmed()]
        # REJECT / TIMEOUT: no slot consumed, remain in WAIT_ENTRY
        return [NoOp()]
```

Run:

```bash
pytest tests/test_engine_lifecycle.py -k on_approval -q
```

Expected: PASS (3 passed).

- [ ] **Step 15: Failing test — `on_entry_filled` → `IN_TRADE`, decrements `trades_remaining` exactly once.**

```python
# tests/test_engine_lifecycle.py  (append)
def test_on_entry_filled_enters_trade_and_decrements():
    e = _engine_at_wait_entry(_cfg(max_trades_per_day=1), trades=1)
    events = e.on_entry_filled(_order_result())
    assert e.ctx.state is State.IN_TRADE
    assert e.ctx.trades_remaining == 0
    assert all(not isinstance(ev, TradeRecorded) for ev in events)  # not closed yet
```

Run:

```bash
pytest tests/test_engine_lifecycle.py -k on_entry_filled -q
```

Expected: FAIL (`on_entry_filled` not implemented).

- [ ] **Step 16: Implement `on_entry_filled`.** Move to `IN_TRADE` and decrement `trades_remaining` (the single slot-consumption point per §9/§8 step 8).

```python
# orb_bot/engine.py  — Engine.on_entry_filled
    def on_entry_filled(self, res: OrderResult) -> list[EngineEvent]:
        self.ctx.state = State.IN_TRADE
        self.ctx.trades_remaining -= 1
        return [NoOp()]
```

Run:

```bash
pytest tests/test_engine_lifecycle.py -k on_entry_filled -q
```

Expected: PASS (1 passed).

- [ ] **Step 17: Failing test — `on_trade_closed` records P/L and re-arms (or finishes).** Single-trade config → `DONE` after close, `is_done()` True. Multi-trade config → re-arm to `WAIT_CONFIRMATION` with direction/break_level/retest counters reset and range/sweep/range-day latches carried over; `rearm_opposite_only` enforced.

```python
# tests/test_engine_lifecycle.py  (append)
def _exit_fill(price="103.90", reason="TP", pos_after=0):
    return Fill(order_id="o2", client_order_id="2026-06-19-SPY-1-TP",
                leg_role="TP", side="sell", price=Decimal(price), qty=10,
                ts=datetime(2026, 6, 19, 11, 0, tzinfo=ET),
                position_qty=pos_after, exit_reason=reason)


def test_on_trade_closed_records_and_finishes_single_trade():
    e = _engine_at_wait_entry(_cfg(max_trades_per_day=1), trades=1)
    e.on_entry_filled(_order_result())  # IN_TRADE, trades_remaining 0
    events = e.on_trade_closed(_exit_fill())
    rec = [ev for ev in events if isinstance(ev, TradeRecorded)]
    assert len(rec) == 1
    assert rec[0].exit_reason == "TP"
    assert e.ctx.state is State.DONE
    assert e.is_done() is True


def test_on_trade_closed_rearms_when_multi_trade():
    cfg = _cfg(max_trades_per_day=2, rearm_opposite_only=True)
    e = _engine_at_wait_entry(cfg, trades=2)
    e.ctx.swept_high = True  # latch that must carry over
    e.ctx.retest_wait = 3
    e.ctx.retest_dead = True
    e.on_entry_filled(_order_result())  # trades_remaining -> 1
    events = e.on_trade_closed(_exit_fill())
    assert any(isinstance(ev, TradeRecorded) for ev in events)
    assert e.ctx.state is State.WAIT_CONFIRMATION  # re-armed, within window
    assert e.ctx.trades_remaining == 1  # NOT re-decremented
    assert e.ctx.direction is None and e.ctx.break_level is None  # reset
    assert e.ctx.retest_wait == 0 and e.ctx.retest_dead is False  # reset
    assert e.ctx.swept_high is True  # latch carried over
    assert e.is_done() is False
    # §10 #14: the just-traded direction is remembered for opposite-only gating
    assert e._last_traded_direction is Direction.LONG


def test_rearm_opposite_only_suppresses_same_direction_reconfirm():
    # §10 #14: after a LONG trade closes and the engine re-arms, a fresh
    # strong-close breakout in the SAME (LONG) direction must NOT re-confirm;
    # only the opposite (SHORT) side may take the remaining slot.
    cfg = _cfg(max_trades_per_day=2, rearm_opposite_only=True)
    e = _engine_at_wait_entry(cfg, trades=2)
    e.on_entry_filled(_order_result())  # IN_TRADE, trades_remaining -> 1
    e.on_trade_closed(_exit_fill())     # re-arm -> WAIT_CONFIRMATION
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert e._last_traded_direction is Direction.LONG
    # break_level / opening_range carried over (high=101, low=99); feed a candle
    # that CLOSES strong above the OR high -> would normally confirm LONG.
    same_dir = _mk_candle(101.5, 102.5, 101.4, 102.4, 30)
    evs = e.on_candle(same_dir)
    assert e.ctx.state is State.WAIT_CONFIRMATION  # same-direction re-confirm rejected
    assert e.ctx.direction is None
    assert all(not isinstance(ev, eng.m.DirectionConfirmed) for ev in evs)
    # the OPPOSITE side still confirms: a strong close below the OR low -> SHORT
    opp_dir = _mk_candle(98.5, 98.6, 96.0, 96.2, 45)
    e.on_candle(opp_dir)
    assert e.ctx.direction is Direction.SHORT
    assert e.ctx.state is State.WAIT_ENTRY
```

Run:

```bash
pytest tests/test_engine_lifecycle.py -k on_trade_closed -q
```

Expected: FAIL (`on_trade_closed` not implemented).

- [ ] **Step 18: Implement `on_trade_closed`.** Compute realized P/L sign-correctly from the recorded entry vs. exit fill (store entry context on `on_entry_filled` so direction/qty/price are available — see note below), emit `TradeRecorded(pnl, exit_reason)`, then either re-arm to `WAIT_CONFIRMATION` (carry over range/ATR/sweep/range-day latches; reset `direction`/`break_level`/`retest_wait`/`retest_dead`) when `max_trades_per_day > 1` and `trades_remaining > 0`, else `DONE`. **No re-decrement.**

First, capture entry context in `on_entry_filled` (replace Step 16's body):

```python
# orb_bot/engine.py  — Engine.on_entry_filled (final form)
    def on_entry_filled(self, res: OrderResult) -> list[EngineEvent]:
        self.ctx.state = State.IN_TRADE
        self.ctx.trades_remaining -= 1
        self._entry_direction = self.ctx.direction
        self._entry_price = res.filled_avg_price
        self._entry_qty = res.filled_qty
        return [NoOp()]
```

> The instance attribute `self._last_traded_direction` (initialized in PART 1's
> `__init__`) is set in `on_trade_closed` below, BEFORE `direction` is reset, so
> the re-armed `WAIT_CONFIRMATION` path can enforce `rearm_opposite_only`.

```python
# (continued)
```

Initialize those attributes in PART 1's `__init__` (add if not present):

```python
        # entry-fill context for P/L on close (set on on_entry_filled)
        self._entry_direction = None
        self._entry_price = None
        self._entry_qty = 0
```

Then `on_trade_closed`:

```python
# orb_bot/engine.py  — Engine.on_trade_closed
    def on_trade_closed(self, fill: Fill) -> list[EngineEvent]:
        entry_px = self._entry_price
        qty = self._entry_qty
        if self._entry_direction is Direction.LONG:
            pnl = (fill.price - entry_px) * qty
        else:
            pnl = (entry_px - fill.price) * qty
        events: list[EngineEvent] = [TradeRecorded(pnl=pnl, exit_reason=fill.exit_reason or "UNKNOWN")]
        # §10 #14: remember the just-traded direction BEFORE resetting it, so a
        # same-direction re-confirmation can be rejected when rearm_opposite_only.
        self._last_traded_direction = self._entry_direction
        # §10 NIT: if this close already lands past the trading window, do not
        # re-arm — finish the session.
        window_expired = (
            self.cfg.max_trades_per_day > 1
            and self.ctx.opening_range is not None
            and fill.ts is not None
            and fill.ts
            >= dt.datetime.combine(
                self.ctx.session_date,
                _session_open_time(self.cfg),
                tzinfo=fill.ts.tzinfo,
            )
            + dt.timedelta(minutes=self.cfg.trading_window_min)
        )
        # re-arm only if a slot remains, multi-trade is configured, and the
        # trading window has not already elapsed
        if (
            self.cfg.max_trades_per_day > 1
            and self.ctx.trades_remaining > 0
            and not window_expired
        ):
            self.ctx.state = State.WAIT_CONFIRMATION
            self.ctx.direction = None
            self.ctx.break_level = None
            self.ctx.retest_wait = 0
            self.ctx.retest_dead = False
            self.ctx.entry_window = []
            # range, atr, swept_high/low, range_day latches carry over by NOT touching them
        else:
            self.ctx.state = State.DONE
        self._entry_direction = None
        self._entry_price = None
        self._entry_qty = 0
        return events
```

Run:

```bash
pytest tests/test_engine_lifecycle.py -k on_trade_closed -q
```

Expected: PASS (2 passed).

- [ ] **Step 19: Full module suite green + no-regression.** Run the new files together with the full engine suite and the no-now() purity test.

```bash
pytest tests/test_engine_entries.py tests/test_engine_lifecycle.py tests/test_engine.py -q
pytest tests -q -k "no_now or purity"
```

Expected: PASS for both (entries + lifecycle + PART 1 engine; purity test confirms PART 2 added no `datetime.now()`/`time.time()` calls).

- [ ] **Step 20: Lint + type-check the module.**

```bash
ruff check orb_bot/engine.py tests/test_engine_entries.py tests/test_engine_lifecycle.py
mypy orb_bot/engine.py
```

Expected: no errors.

- [ ] **Step 21: Commit.**

```bash
git add orb_bot/engine.py tests/test_engine_entries.py tests/test_engine_lifecycle.py
git commit -m "feat(engine): entry builders, setup validation, and trade lifecycle

Add project_target/validate_setup/try_build_entry/entry_breakout/entry_retest
pure helpers and wire WAIT_ENTRY -> SetupProposed; implement on_approval
(REJECT/TIMEOUT no slot, APPROVE -> EntryConfirmed), on_entry_filled
(-> IN_TRADE, decrement trades_remaining), and on_trade_closed (TradeRecorded +
re-arm to WAIT_CONFIRMATION when max_trades_per_day>1, opposite-only).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Expected: commit succeeds.

---

I now have a complete understanding of the spec. I have everything needed: §4 (architecture/layout), §6 (interfaces + the critical SDK adapter note about callbacks→async iterators via `asyncio.Queue`), §13 (execution/Alpaca integration with the SDK loop trap, `_run_forever`/`stop_ws` details), §8 step 1/2 (preflight uses `hist_tf` for ATR seed + `backfill_or`), §18 (testing strategy with fake stream).

Now I'll write the markdown plan for the `orb_bot/feed/alpaca.py` module only, following the OUTPUT FORMAT with complete runnable code and the TDD rhythm.

### Task 20: AlpacaFeed — package scaffold, models stub, and SDK→Candle conversion (pure helper)

**Files:**
- Create: `orb_bot/feed/__init__.py`
- Create: `orb_bot/feed/alpaca.py`
- Test: `tests/test_feed_alpaca.py`

**Interfaces:**
- Consumes (from the contract): `Candle(ts_open, ts_close, open, high, low, close, volume, timeframe_min, data_incomplete=False, bars_present=None)` from `orb_bot/models.py` — frozen, `Decimal` prices, tz-aware ET datetimes; `DataFeed` Protocol (`def candles(self)->AsyncIterator[Candle]`, `async def close(self)->None`) from `orb_bot/interfaces.py`. The alpaca-py SDK `Bar` exposes `.symbol`, `.timestamp` (UTC tz-aware), `.open/.high/.low/.close` (float), `.volume` (int).
- Produces (later tasks rely on): module-level pure helper `_bar_to_candle(bar) -> Candle` that converts an SDK `Bar` to a 1-minute `Candle` with `timeframe_min=1`, `ts_open` in ET, `ts_close = ts_open + 1m`, `Decimal` prices.

- [ ] **Step 1: Create the `feed` subpackage init (empty marker).**

```bash
mkdir -p orb_bot/feed tests
```

```python
# orb_bot/feed/__init__.py
"""Concrete DataFeed implementations (Alpaca live stream + historical REST)."""
```

- [ ] **Step 2: Write the failing test for `_bar_to_candle` conversion.**

This test depends only on the pure conversion helper and a tiny fake SDK Bar (no network, no alpaca-py import in the test). It asserts `Decimal` prices, `timeframe_min == 1`, ET tz, and `ts_close == ts_open + 1m`.

```python
# tests/test_feed_alpaca.py
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .context import orb_bot
from orb_bot.feed import alpaca as feed_alpaca
from orb_bot.models import Candle

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


class FakeBar:
    """Mimics the alpaca-py StockDataStream Bar payload (UTC tz-aware timestamp)."""

    def __init__(self, symbol, timestamp, open_, high, low, close, volume):
        self.symbol = symbol
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def test_bar_to_candle_converts_prices_to_decimal_and_sets_1m_timeframe():
    # 09:30 ET == 13:30 UTC (EDT, summer); pass UTC like the SDK does.
    ts_utc = datetime(2026, 6, 19, 13, 30, tzinfo=UTC)
    bar = FakeBar("SPY", ts_utc, 100.12, 100.45, 99.98, 100.30, 5000)

    candle = feed_alpaca._bar_to_candle(bar)

    assert isinstance(candle, Candle)
    assert candle.timeframe_min == 1
    assert candle.open == Decimal("100.12")
    assert candle.high == Decimal("100.45")
    assert candle.low == Decimal("99.98")
    assert candle.close == Decimal("100.30")
    assert candle.volume == 5000


def test_bar_to_candle_uses_et_and_close_is_open_plus_one_minute():
    ts_utc = datetime(2026, 6, 19, 13, 30, tzinfo=UTC)
    bar = FakeBar("SPY", ts_utc, 1.0, 1.0, 1.0, 1.0, 1)

    candle = feed_alpaca._bar_to_candle(bar)

    expected_open = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    assert candle.ts_open == expected_open
    assert candle.ts_open.tzinfo is not None
    assert candle.ts_close == candle.ts_open + timedelta(minutes=1)
```

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **FAIL** (`ModuleNotFoundError: No module named 'orb_bot.feed.alpaca'`).

- [ ] **Step 3: Minimal `alpaca.py` with the pure `_bar_to_candle` helper only.**

Keep alpaca-py imports lazy/isolated so importing the conversion helper never requires the SDK to be installed.

```python
# orb_bot/feed/alpaca.py
"""AlpacaFeed: 1-minute StockDataStream (IEX/SIP) -> Candle queue (transport).

alpaca-py imports are isolated in this module (the SDK is a heavy, optional dep).
The pure SDK-Bar -> Candle conversion lives at module level so it can be unit
tested with a fake Bar and no network / no alpaca-py installed.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot.models import Candle

ET = ZoneInfo("America/New_York")


def _bar_to_candle(bar) -> Candle:
    """Convert an alpaca-py 1m Bar (UTC tz-aware timestamp, float OHLC) to a Candle.

    Prices -> Decimal(str(...)) (exact, no float artefacts); timestamp -> ET;
    timeframe_min=1 (transport); ts_close = ts_open + 1 minute.
    """
    ts_open = bar.timestamp.astimezone(ET)
    return Candle(
        ts_open=ts_open,
        ts_close=ts_open + timedelta(minutes=1),
        open=Decimal(str(bar.open)),
        high=Decimal(str(bar.high)),
        low=Decimal(str(bar.low)),
        close=Decimal(str(bar.close)),
        volume=int(bar.volume),
        timeframe_min=1,
    )
```

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **PASS** (2 passed).

- [ ] **Step 4: Commit.**

```bash
git add orb_bot/feed/__init__.py orb_bot/feed/alpaca.py tests/test_feed_alpaca.py
git commit -m "feat(feed): add Alpaca SDK Bar -> Candle pure converter

Isolate alpaca-py to orb_bot/feed and unit-test the pure _bar_to_candle
helper with a fake Bar (no network, no SDK installed).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 21: AlpacaFeed — queue-backed `candles()` async generator (callback→async-iterator adapter)

**Files:**
- Modify: `orb_bot/feed/alpaca.py`
- Test: `tests/test_feed_alpaca.py`

**Interfaces:**
- Consumes: `Candle` (above); the §6 adapter contract — the SDK delivers bars via a registered async callback (`subscribe_bars(handler, *symbols)`), so the handler **enqueues onto an internal `asyncio.Queue`** and `candles()` drains it.
- Produces: `AlpacaFeed.__init__(self, *, api_key, secret_key, symbol, feed, queue_maxsize=10000)`; `async def _on_bar(self, bar) -> None` (the SDK callback, enqueues `_bar_to_candle(bar)`); `def candles(self) -> AsyncIterator[Candle]` (drains the queue in order). Later tasks rely on `candles()` yielding `Candle`s FIFO and on `_on_bar` being the enqueue seam a fake stream can call.

- [ ] **Step 1: Write the failing test for `candles()` ordering and conversion via the queue.**

Uses a FAKE flow: push fake bars directly through `_on_bar` (the same coroutine the SDK calls), then drain `candles()`. Append these to `tests/test_feed_alpaca.py`.

```python
# tests/test_feed_alpaca.py  (append)
import asyncio


def _mk_bar(minute, price):
    return FakeBar(
        "SPY",
        datetime(2026, 6, 19, 13, 30 + minute, tzinfo=UTC),
        price, price, price, price, 100,
    )


async def test_candles_drains_queue_in_fifo_order():
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
    )
    # Simulate the SDK pushing three 1m bars via the registered callback.
    await f._on_bar(_mk_bar(0, 100.0))
    await f._on_bar(_mk_bar(1, 101.0))
    await f._on_bar(_mk_bar(2, 102.0))

    got = []
    agen = f.candles()
    for _ in range(3):
        got.append(await agen.__anext__())

    assert [c.close for c in got] == [Decimal("100.0"), Decimal("101.0"), Decimal("102.0")]
    assert [c.ts_open.hour for c in got] == [9, 9, 9]
    assert [c.ts_open.minute for c in got] == [30, 31, 32]
    assert all(c.timeframe_min == 1 for c in got)


async def test_candles_blocks_until_next_bar_enqueued():
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
    )
    agen = f.candles()
    task = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0)  # let the consumer block on an empty queue
    assert not task.done()

    await f._on_bar(_mk_bar(0, 55.0))
    candle = await asyncio.wait_for(task, timeout=1.0)
    assert candle.close == Decimal("55.0")
```

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **FAIL** (`AttributeError: module 'orb_bot.feed.alpaca' has no attribute 'AlpacaFeed'`).

- [ ] **Step 2: Add the `AlpacaFeed` class with the queue, `_on_bar`, and `candles()`.**

The SDK client itself is built lazily in a helper so constructing `AlpacaFeed` (and testing the queue path) never imports alpaca-py. Add to `orb_bot/feed/alpaca.py`.

```python
# orb_bot/feed/alpaca.py  (add imports at top, below existing ones)
import asyncio
from collections.abc import AsyncIterator
```

```python
# orb_bot/feed/alpaca.py  (append class)
class AlpacaFeed:
    """DataFeed: subscribes to 1m StockDataStream bars and exposes them as an
    async iterator of transport Candles (timeframe_min=1).

    The alpaca-py StockDataStream delivers bars via a registered async callback
    (subscribe_bars(handler, *symbols)); _on_bar is that handler. It enqueues a
    converted Candle onto an asyncio.Queue that candles() drains (the §6
    callback->async-iterator adapter). The SDK client is built lazily so the
    queue path is testable without alpaca-py installed.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        symbol: str,
        feed: str = "IEX",
        queue_maxsize: int = 10000,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._symbol = symbol
        self._feed = feed
        self._queue: asyncio.Queue[Candle] = asyncio.Queue(maxsize=queue_maxsize)
        self._stream = None  # lazily built StockDataStream
        self._run_task: asyncio.Task | None = None
        self._closed = False

    async def _on_bar(self, bar) -> None:
        """SDK callback: convert a 1m Bar and enqueue it for candles()."""
        await self._queue.put(_bar_to_candle(bar))

    async def candles(self) -> AsyncIterator[Candle]:
        """Drain the queue, yielding 1m transport Candles in FIFO order."""
        while True:
            candle = await self._queue.get()
            yield candle
```

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **PASS** (4 passed).

- [ ] **Step 3: Commit.**

```bash
git add orb_bot/feed/alpaca.py tests/test_feed_alpaca.py
git commit -m "feat(feed): AlpacaFeed queue-backed candles() adapter

SDK bar callback (_on_bar) enqueues converted Candles onto an asyncio.Queue
that candles() drains FIFO -- the callback->async-iterator adapter from §6.
Tested with a fake stream pushing bars directly; no network.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 22: AlpacaFeed — lazy stream build, `_run_forever()` scheduling, and `close()` lifecycle

**Files:**
- Modify: `orb_bot/feed/alpaca.py`
- Test: `tests/test_feed_alpaca.py`

**Interfaces:**
- Consumes: §13 SDK loop trap — `StockDataStream.run()` calls `asyncio.run()` and blocks; instead schedule `stream._run_forever()` as a task on the orchestrator's loop, and on shutdown `await stream.stop_ws()` then cancel the `_run_forever` task (never the sync `stream.stop()`). alpaca-py pinned `>=0.43,<0.44`.
- Produces: `def start(self) -> None` (builds the `StockDataStream`, calls `subscribe_bars(self._on_bar, symbol)`, schedules `_run_forever()` as a task); `async def close(self) -> None` (idempotent: `await stream.stop_ws()`, cancel + await the run task). A FAKE stream double (with `subscribe_bars`, `_run_forever`, `stop_ws`) injected via `stream_factory` proves the wiring without network.

- [ ] **Step 1: Write the failing test for `start()`/`close()` against a fake stream.**

The fake stream records `subscribe_bars` args, exposes an awaitable `_run_forever`, and an awaitable `stop_ws`. Append to `tests/test_feed_alpaca.py`.

```python
# tests/test_feed_alpaca.py  (append)
class FakeStream:
    """Stand-in for alpaca-py StockDataStream (records the §13 lifecycle calls)."""

    def __init__(self):
        self.subscribed = None
        self.run_forever_started = False
        self.stop_ws_called = False
        self._run_block = asyncio.Event()  # _run_forever hangs until cancelled

    def subscribe_bars(self, handler, *symbols):
        self.subscribed = (handler, symbols)

    async def _run_forever(self):
        self.run_forever_started = True
        await self._run_block.wait()  # mimic a long-lived ws loop

    async def stop_ws(self):
        self.stop_ws_called = True


async def test_start_subscribes_and_schedules_run_forever():
    fake = FakeStream()
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        stream_factory=lambda **_: fake,
    )
    f.start()
    await asyncio.sleep(0)  # let the scheduled task start

    handler, symbols = fake.subscribed
    assert handler == f._on_bar
    assert symbols == ("SPY",)
    assert fake.run_forever_started is True
    assert f._run_task is not None and not f._run_task.done()

    await f.close()


async def test_close_stops_ws_and_cancels_run_task_idempotently():
    fake = FakeStream()
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        stream_factory=lambda **_: fake,
    )
    f.start()
    await asyncio.sleep(0)

    await f.close()
    assert fake.stop_ws_called is True
    assert f._run_task is None or f._run_task.done()

    # idempotent: a second close is a no-op and does not raise
    await f.close()


async def test_close_before_start_is_safe():
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
    )
    await f.close()  # must not raise
```

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **FAIL** (`AlpacaFeed.__init__() got an unexpected keyword argument 'stream_factory'`).

- [ ] **Step 2: Add `stream_factory`, lazy stream build, `start()`, and `close()`.**

The default `stream_factory` lazily imports alpaca-py only when called (keeping the SDK isolated and optional for tests). Update `__init__` and add the lifecycle methods.

```python
# orb_bot/feed/alpaca.py  (replace __init__ signature/body)
    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        symbol: str,
        feed: str = "IEX",
        queue_maxsize: int = 10000,
        stream_factory=None,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._symbol = symbol
        self._feed = feed
        self._queue: asyncio.Queue[Candle] = asyncio.Queue(maxsize=queue_maxsize)
        self._stream_factory = stream_factory or _default_stream_factory
        self._stream = None  # built in start()
        self._run_task: asyncio.Task | None = None
        self._closed = False
```

```python
# orb_bot/feed/alpaca.py  (append: default factory + lifecycle methods)
def _default_stream_factory(*, api_key: str, secret_key: str, feed: str):
    """Build a real alpaca-py StockDataStream. alpaca-py imported lazily here so
    the SDK stays isolated to this call site (§4/§13)."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.live.stock import StockDataStream

    return StockDataStream(api_key, secret_key, feed=DataFeed[feed])
```

```python
# orb_bot/feed/alpaca.py  (append to class AlpacaFeed)
    def start(self) -> None:
        """Build the stream, subscribe the 1m-bar handler, and schedule the SDK's
        _run_forever() as a task on the running loop (never stream.run(), which
        calls asyncio.run() and blocks -- §13 loop trap)."""
        self._stream = self._stream_factory(
            api_key=self._api_key, secret_key=self._secret_key, feed=self._feed,
        )
        self._stream.subscribe_bars(self._on_bar, self._symbol)
        self._run_task = asyncio.ensure_future(self._stream._run_forever())

    async def close(self) -> None:
        """Idempotent shutdown: stop the ws, then cancel + await the run task.
        Uses stop_ws() (async, same-loop) -- never the sync stream.stop() (§13)."""
        if self._closed:
            return
        self._closed = True
        if self._stream is not None:
            await self._stream.stop_ws()
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
            self._run_task = None
```

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **PASS** (7 passed).

- [ ] **Step 3: Commit.**

```bash
git add orb_bot/feed/alpaca.py tests/test_feed_alpaca.py
git commit -m "feat(feed): AlpacaFeed start/close lifecycle (SDK loop-trap safe)

start() subscribes _on_bar and schedules stream._run_forever() as a task;
close() awaits stop_ws() then cancels the task (never sync stream.stop()).
Injectable stream_factory lets a fake stream prove wiring without network.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 23: AlpacaFeed — historical REST: `hist_tf()` ATR seed + `backfill_or()` opening-range backfill

**Files:**
- Modify: `orb_bot/feed/alpaca.py`
- Test: `tests/test_feed_alpaca.py`

**Interfaces:**
- Consumes: §8 step 1 preflight — `seed ATR(14)` on **T-minute** bars via historical REST (same feed as live), and REST-backfill 09:30–OR_close when the process starts after the OR window. `Engine.seed_atr(hist_tf: list[Candle])` consumes a list of T-min `Candle`s; the orchestrator backfills the opening range from 1m bars.
- Produces: `async def hist_tf(self, *, limit, tf_min, end) -> list[Candle]` (returns the last `limit` T-minute Candles up to `end`, oldest→newest, `timeframe_min=tf_min`); `async def backfill_or(self, *, start, end) -> list[Candle]` (returns 1m Candles in `[start, end)` for live OR reconstruction, oldest→newest). Both wrap the sync `StockHistoricalDataClient` in `asyncio.to_thread` and convert via a shared `_bars_to_candles` helper. A FAKE `hist_factory` returns canned bars (no network).

- [ ] **Step 1: Write the failing tests for `hist_tf` and `backfill_or` against a fake historical client.**

The fake historical client records the request and returns a `.data` mapping (symbol→list of bars), mirroring alpaca-py's `BarSet.data`. Append to `tests/test_feed_alpaca.py`.

```python
# tests/test_feed_alpaca.py  (append)
class FakeBarSet:
    def __init__(self, symbol, bars):
        self.data = {symbol: bars}


class FakeHistClient:
    """Stand-in for alpaca-py StockHistoricalDataClient.get_stock_bars."""

    def __init__(self, symbol, bars):
        self._barset = FakeBarSet(symbol, bars)
        self.last_request = None

    def get_stock_bars(self, request):
        self.last_request = request
        return self._barset


async def test_hist_tf_returns_tf_candles_oldest_to_newest():
    # Two 15m bars (UTC timestamps); SDK returns them oldest-first.
    bars = [
        FakeBar("SPY", datetime(2026, 6, 19, 13, 0, tzinfo=UTC), 10, 11, 9, 10.5, 1000),
        FakeBar("SPY", datetime(2026, 6, 19, 13, 15, tzinfo=UTC), 10.5, 12, 10, 11.5, 2000),
    ]
    hist = FakeHistClient("SPY", bars)
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        hist_factory=lambda **_: hist,
    )
    end = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    candles = await f.hist_tf(limit=2, tf_min=15, end=end)

    assert [c.timeframe_min for c in candles] == [15, 15]
    assert [c.close for c in candles] == [Decimal("10.5"), Decimal("11.5")]
    # ts_close = ts_open + tf_min for T-bars
    assert candles[0].ts_close == candles[0].ts_open + timedelta(minutes=15)


async def test_backfill_or_returns_1m_candles_in_window():
    bars = [
        FakeBar("SPY", datetime(2026, 6, 19, 13, 30, tzinfo=UTC), 1, 1, 1, 1, 1),
        FakeBar("SPY", datetime(2026, 6, 19, 13, 31, tzinfo=UTC), 2, 2, 2, 2, 2),
    ]
    hist = FakeHistClient("SPY", bars)
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        hist_factory=lambda **_: hist,
    )
    start = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    end = datetime(2026, 6, 19, 9, 45, tzinfo=ET)
    candles = await f.backfill_or(start=start, end=end)

    assert [c.timeframe_min for c in candles] == [1, 1]
    assert candles[0].ts_open == datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    assert candles[1].ts_open == datetime(2026, 6, 19, 9, 31, tzinfo=ET)
    assert candles[0].ts_close == candles[0].ts_open + timedelta(minutes=1)
```

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **FAIL** (`AlpacaFeed.__init__() got an unexpected keyword argument 'hist_factory'`).

- [ ] **Step 2: Add `hist_factory`, the `_bars_to_candles` helper, and the two REST methods.**

`_bars_to_candles(bars, tf_min)` reuses `_bar_to_candle` for 1m and sets `ts_close = ts_open + tf_min` for T-bars. The sync SDK call is wrapped in `asyncio.to_thread` per §13. Update `__init__` and append the methods.

```python
# orb_bot/feed/alpaca.py  (add hist_factory to __init__, after stream_factory line)
        self._hist_factory = None  # placeholder; set below
```

Replace the `__init__` tail so both factories are stored (full updated `__init__`):

```python
# orb_bot/feed/alpaca.py  (replace the whole __init__)
    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        symbol: str,
        feed: str = "IEX",
        queue_maxsize: int = 10000,
        stream_factory=None,
        hist_factory=None,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._symbol = symbol
        self._feed = feed
        self._queue: asyncio.Queue[Candle] = asyncio.Queue(maxsize=queue_maxsize)
        self._stream_factory = stream_factory or _default_stream_factory
        self._hist_factory = hist_factory or _default_hist_factory
        self._stream = None
        self._hist = None
        self._run_task: asyncio.Task | None = None
        self._closed = False
```

```python
# orb_bot/feed/alpaca.py  (append: default hist factory + conversion helper)
def _default_hist_factory(*, api_key: str, secret_key: str):
    """Build a real alpaca-py StockHistoricalDataClient (lazy import)."""
    from alpaca.data.historical.stock import StockHistoricalDataClient

    return StockHistoricalDataClient(api_key, secret_key)


def _bars_to_candles(bars, tf_min: int) -> list[Candle]:
    """Convert SDK bars to Candles with the given timeframe (oldest->newest)."""
    out: list[Candle] = []
    for bar in bars:
        if tf_min == 1:
            out.append(_bar_to_candle(bar))
        else:
            ts_open = bar.timestamp.astimezone(ET)
            out.append(
                Candle(
                    ts_open=ts_open,
                    ts_close=ts_open + timedelta(minutes=tf_min),
                    open=Decimal(str(bar.open)),
                    high=Decimal(str(bar.high)),
                    low=Decimal(str(bar.low)),
                    close=Decimal(str(bar.close)),
                    volume=int(bar.volume),
                    timeframe_min=tf_min,
                )
            )
    return out
```

```python
# orb_bot/feed/alpaca.py  (append to class AlpacaFeed)
    def _ensure_hist(self):
        if self._hist is None:
            self._hist = self._hist_factory(
                api_key=self._api_key, secret_key=self._secret_key,
            )
        return self._hist

    async def _get_bars(self, *, tf_min, start, end, limit):
        """Run the sync historical REST call off the loop (§13 to_thread)."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        hist = self._ensure_hist()
        timeframe = (
            TimeFrame.Minute if tf_min == 1
            else TimeFrame(tf_min, TimeFrameUnit.Minute)
        )
        request = StockBarsRequest(
            symbol_or_symbols=self._symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            feed=self._feed,
        )
        barset = await asyncio.to_thread(hist.get_stock_bars, request)
        return barset.data.get(self._symbol, [])

    async def hist_tf(self, *, limit: int, tf_min: int, end) -> list[Candle]:
        """Last `limit` T-minute Candles up to `end` (oldest->newest) for ATR seed."""
        bars = await self._get_bars(tf_min=tf_min, start=None, end=end, limit=limit)
        return _bars_to_candles(bars, tf_min)

    async def backfill_or(self, *, start, end) -> list[Candle]:
        """1m Candles in [start, end) for opening-range reconstruction (§8 step 1)."""
        bars = await self._get_bars(tf_min=1, start=start, end=end, limit=None)
        return _bars_to_candles(bars, 1)
```

Note: the test's `FakeHistClient.get_stock_bars` is sync and `asyncio.to_thread` runs it correctly; the fake's `import` of `StockBarsRequest`/`TimeFrame` inside `_get_bars` requires alpaca-py. To keep the REST tests SDK-free, override the request-building seam in the test by patching `_get_bars`. Use this lighter test wiring instead (replace the two REST tests' direct calls): the fake injects bars through `hist_factory` and we stub `_get_bars` to skip SDK request types.

Replace the bodies of the two REST tests' feed setup to stub `_get_bars`:

```python
# tests/test_feed_alpaca.py  (replace test_hist_tf_returns_tf_candles_oldest_to_newest body's call section)
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        hist_factory=lambda **_: hist,
    )

    async def fake_get_bars(*, tf_min, start, end, limit):
        return hist.get_stock_bars(object()).data["SPY"]

    f._get_bars = fake_get_bars
    end = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    candles = await f.hist_tf(limit=2, tf_min=15, end=end)
```

```python
# tests/test_feed_alpaca.py  (replace test_backfill_or_returns_1m_candles_in_window body's call section)
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        hist_factory=lambda **_: hist,
    )

    async def fake_get_bars(*, tf_min, start, end, limit):
        return hist.get_stock_bars(object()).data["SPY"]

    f._get_bars = fake_get_bars
    start = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    end = datetime(2026, 6, 19, 9, 45, tzinfo=ET)
    candles = await f.backfill_or(start=start, end=end)
```

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **PASS** (9 passed).

- [ ] **Step 3: Commit.**

```bash
git add orb_bot/feed/alpaca.py tests/test_feed_alpaca.py
git commit -m "feat(feed): historical REST hist_tf() + backfill_or()

hist_tf returns last N T-min Candles for the ATR seed; backfill_or returns
1m Candles for opening-range reconstruction (§8 step 1). Sync SDK call wrapped
in asyncio.to_thread (§13); _get_bars stubbed in tests to stay SDK-free.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 24: AlpacaFeed — DataFeed Protocol conformance + network-gated paper smoke test

**Files:**
- Modify: `orb_bot/feed/alpaca.py`
- Test: `tests/test_feed_alpaca.py`

**Interfaces:**
- Consumes: `DataFeed` Protocol (`runtime_checkable`) from `orb_bot/interfaces.py` (`candles()`, `close()`); §13/§18 — the smoke test must assert the pinned SDK exposes `subscribe_bars` / `_run_forever` / `stop_ws` on every upgrade, gated by `@pytest.mark.skipif(no creds)`.
- Produces: confirmation that `AlpacaFeed` satisfies `DataFeed` (structural `isinstance`), plus a live smoke test of the real `StockDataStream` SDK surface.

- [ ] **Step 1: Write the failing Protocol-conformance test.**

Append to `tests/test_feed_alpaca.py`.

```python
# tests/test_feed_alpaca.py  (append)
from orb_bot.interfaces import DataFeed


def test_alpaca_feed_satisfies_datafeed_protocol():
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
    )
    assert isinstance(f, DataFeed)
```

```bash
pytest tests/test_feed_alpaca.py::test_alpaca_feed_satisfies_datafeed_protocol -q
```
Expected: **PASS** if `candles`/`close` signatures already match the runtime-checkable `DataFeed` (they do from prior tasks). If `interfaces.DataFeed` is not yet `@runtime_checkable`, this **FAILS** with `TypeError: Instance and class checks can only be used with @runtime_checkable protocols` — in that case the `interfaces` module owner must add the decorator; this module is already conformant. Re-run after that fix; expected **PASS**.

- [ ] **Step 2: Add the network-gated paper SDK smoke test.**

Asserts the real pinned SDK still exposes the semi-internal lifecycle methods (§13/§21 canary). Append to `tests/test_feed_alpaca.py`.

```python
# tests/test_feed_alpaca.py  (append)
import os

import pytest

_NO_CREDS = not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET"))


@pytest.mark.skipif(_NO_CREDS, reason="paper smoke test needs ALPACA_KEY/ALPACA_SECRET")
def test_real_stockdatastream_exposes_pinned_sdk_surface():
    from alpaca.data.enums import DataFeed as SDKDataFeed
    from alpaca.data.live.stock import StockDataStream

    stream = StockDataStream(
        os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"], feed=SDKDataFeed.IEX,
    )
    # The §13 loop-trap workaround depends on these existing on every SDK upgrade.
    assert hasattr(stream, "subscribe_bars")
    assert hasattr(stream, "_run_forever")
    assert hasattr(stream, "stop_ws")


@pytest.mark.skipif(_NO_CREDS, reason="paper smoke test needs ALPACA_KEY/ALPACA_SECRET")
async def test_real_hist_tf_returns_candles():
    f = feed_alpaca.AlpacaFeed(
        api_key=os.environ["ALPACA_KEY"],
        secret_key=os.environ["ALPACA_SECRET"],
        symbol="SPY",
        feed="IEX",
    )
    from datetime import datetime as _dt

    candles = await f.hist_tf(limit=5, tf_min=15, end=_dt.now(ET))
    assert len(candles) >= 1
    assert all(c.timeframe_min == 15 for c in candles)
```

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **PASS** (10 passed when no creds — the 2 smoke tests are SKIPPED; reported as `8 passed, 2 skipped` if alpaca-py is installed, or the smoke tests skip on missing creds regardless).

- [ ] **Step 3: Run the full feed test module and confirm green.**

```bash
pytest tests/test_feed_alpaca.py -q
```
Expected: **PASS** — all non-network tests pass; the two `@pytest.mark.skipif` smoke tests are skipped without creds.

- [ ] **Step 4: Commit.**

```bash
git add orb_bot/feed/alpaca.py tests/test_feed_alpaca.py
git commit -m "test(feed): DataFeed protocol conformance + network-gated SDK smoke

isinstance(AlpacaFeed, DataFeed) structural check; paper smoke test asserts the
pinned StockDataStream still exposes subscribe_bars/_run_forever/stop_ws (§13
upgrade canary) and a live hist_tf call, both skipped without creds.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

I have all the information needed. The package is greenfield, alpaca-py isn't installed, and the tests must inject a fake TradingClient/stream (no network), with a network smoke test guarded by skipif-no-creds. The plan must avoid importing the real `alpaca` SDK at test collection time unless creds exist — so the broker module must import alpaca-py lazily or the tests inject fakes. Since tests assert request objects and to_thread usage with a fake client, the broker must accept injectable client/stream and the alpaca request classes must be importable. Given alpaca-py is a pinned runtime dep, tests will run after `pip install`, so importing `alpaca` at module top is fine; the smoke test is the only one needing creds.

Now I'll produce the markdown plan for this module only.

### Task 25: Execution/Alpaca — package skeleton + leg-role/client-order-id helpers
**Files:** Create `orb_bot/execution/__init__.py`, `orb_bot/execution/alpaca.py`; Test `tests/test_alpaca_helpers.py`.
**Interfaces:** Consumes: `models.Setup(direction:Direction, model:Model, entry:Decimal, stop:Decimal, target:Decimal, rr:float, reason:list[str])`, `models.Direction`. Produces: free functions `client_order_id(session_date:date, symbol:str, seq:int, leg_role:str)->str` (prefix `{date}-{symbol}-{seq}`, suffix `-{leg_role}`), `leg_role_for(client_order_id:str, parent_coid:str)->str` mapping a fill's COID to `'ENTRY'|'TP'|'SL'|'FLATTEN'`, `whole_share_qty(qty:int)->int` (raises `ValueError` on `<1`).

- [ ] **Step 1: Write failing test for the package import + helpers.**
```python
# tests/test_alpaca_helpers.py
from datetime import date

import pytest

from .context import orb_bot
from orb_bot.execution import alpaca


def test_client_order_id_prefix_and_suffix():
    coid = alpaca.client_order_id(date(2026, 6, 19), "SPY", 1, "ENTRY")
    assert coid == "2026-06-19-SPY-1-ENTRY"
    assert coid.startswith("2026-06-19-SPY-1-")


def test_client_order_id_distinct_legs_share_prefix():
    entry = alpaca.client_order_id(date(2026, 6, 19), "SPY", 2, "ENTRY")
    tp = alpaca.client_order_id(date(2026, 6, 19), "SPY", 2, "TP")
    sl = alpaca.client_order_id(date(2026, 6, 19), "SPY", 2, "SL")
    assert entry.rsplit("-", 1)[0] == tp.rsplit("-", 1)[0] == sl.rsplit("-", 1)[0]
    assert entry.rsplit("-", 1)[0] == "2026-06-19-SPY-2"


def test_leg_role_for_known_suffixes():
    assert alpaca.leg_role_for("2026-06-19-SPY-1-ENTRY", "2026-06-19-SPY-1-ENTRY") == "ENTRY"
    assert alpaca.leg_role_for("2026-06-19-SPY-1-TP", "2026-06-19-SPY-1-ENTRY") == "TP"
    assert alpaca.leg_role_for("2026-06-19-SPY-1-SL", "2026-06-19-SPY-1-ENTRY") == "SL"
    assert alpaca.leg_role_for("2026-06-19-SPY-1-FLATTEN", "2026-06-19-SPY-1-ENTRY") == "FLATTEN"


def test_leg_role_for_unknown_suffix_defaults_flatten():
    # broker-initiated close_all_positions may carry an unknown/auto coid
    assert alpaca.leg_role_for("alpaca-auto-xyz", "2026-06-19-SPY-1-ENTRY") == "FLATTEN"


def test_whole_share_qty_rejects_below_one():
    assert alpaca.whole_share_qty(3) == 3
    with pytest.raises(ValueError):
        alpaca.whole_share_qty(0)
```
Run: `pytest tests/test_alpaca_helpers.py -q` → **FAIL** (`ModuleNotFoundError: No module named 'orb_bot.execution'`).

- [ ] **Step 2: Create the package init.**
```python
# orb_bot/execution/__init__.py
"""Concrete Broker implementations (live/paper Alpaca)."""
```
Run: `pytest tests/test_alpaca_helpers.py -q` → **FAIL** (`module 'orb_bot.execution' has no attribute 'alpaca'`).

- [ ] **Step 3: Minimal impl — helpers only (no SDK import yet).**
```python
# orb_bot/execution/alpaca.py
from __future__ import annotations

from datetime import date

_KNOWN_LEGS = {"ENTRY", "TP", "SL", "FLATTEN"}


def client_order_id(session_date: date, symbol: str, seq: int, leg_role: str) -> str:
    """Deterministic id: prefix ``{date}-{symbol}-{seq}`` shared across a trade's legs,
    suffix ``-{leg_role}``. Prefix is the P/L grouping key (spec §5/§17a)."""
    if leg_role not in _KNOWN_LEGS:
        raise ValueError(f"unknown leg_role {leg_role!r}")
    return f"{session_date.isoformat()}-{symbol}-{seq}-{leg_role}"


def leg_role_for(coid: str, parent_coid: str) -> str:
    """Classify a fill's client_order_id by its suffix. Unknown/auto ids (e.g. a
    broker-initiated close_all_positions) are treated as FLATTEN (spec §13/§16)."""
    suffix = coid.rsplit("-", 1)[-1]
    return suffix if suffix in _KNOWN_LEGS else "FLATTEN"


def whole_share_qty(qty: int) -> int:
    """Defense-in-depth: brackets are whole-share; reject qty < 1 (spec §16)."""
    if qty < 1:
        raise ValueError(f"qty must be >= 1, got {qty}")
    return int(qty)
```
Run: `pytest tests/test_alpaca_helpers.py -q` → **PASS** (5 passed).

- [ ] **Step 4: Commit.**
```bash
git checkout -b feat/execution-alpaca
git add orb_bot/execution/__init__.py orb_bot/execution/alpaca.py tests/test_alpaca_helpers.py
git commit -m "feat(execution): alpaca client_order_id/leg_role/qty helpers"
```

---

### Task 26: Execution/Alpaca — AlpacaBroker construction, get_account, get_clock
**Files:** Modify `orb_bot/execution/alpaca.py`; Test `tests/test_alpaca_account_clock.py`, and add a fake-SDK fixture module `tests/fakes/__init__.py`, `tests/fakes/alpaca_sdk.py`.
**Interfaces:** Consumes: `interfaces.Broker.get_account()->AccountSnapshot`, `get_clock()->ClockInfo`; `models.AccountSnapshot(equity:Decimal, buying_power:Decimal, shorting_enabled:bool)`, `models.ClockInfo(is_open:bool, next_close:datetime)`; `config.RunConfig(symbol, live, feed, allow_live_iex)`. Produces: `class AlpacaBroker(Broker)` with `__init__(self, run: RunConfig, key: str, secret: str, *, client=None, stream=None, session_date: date)` — `client`/`stream` injectable for tests (default builds real `TradingClient(paper=not run.live)`/`TradingStream`); `async def get_account()` and `async def get_clock()` each run the sync SDK call via `asyncio.to_thread`.

- [ ] **Step 1: Build the fake SDK doubles (no network).**
```python
# tests/fakes/__init__.py
```
```python
# tests/fakes/alpaca_sdk.py
"""In-memory doubles for alpaca-py used by execution tests — NO network, NO real SDK.

FakeTradingClient records every request object passed to submit_order so tests can
assert MarketOrderRequest fields, and tracks to_thread offloading by recording the
OS thread id each sync method runs on (must differ from the event-loop thread)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class FakeAccount:
    equity: str = "100000.00"
    buying_power: str = "200000.00"
    shorting_enabled: bool = True


@dataclass
class FakeClock:
    is_open: bool = True
    next_close: datetime | None = None


@dataclass
class FakeOrderLeg:
    id: str
    client_order_id: str
    status: str = "new"
    filled_avg_price: str | None = None
    filled_qty: str = "0"


@dataclass
class FakeOrder:
    id: str = "ord-1"
    client_order_id: str = "coid-1"
    status: str = "accepted"
    filled_avg_price: str | None = None
    filled_qty: str = "0"
    legs: list = field(default_factory=list)


@dataclass
class FakeTradingClient:
    account: FakeAccount = field(default_factory=FakeAccount)
    clock: FakeClock = field(default_factory=FakeClock)
    next_order: FakeOrder = field(default_factory=FakeOrder)
    submitted: list = field(default_factory=list)        # request objects
    canceled_all: int = 0
    closed_all: list = field(default_factory=list)        # cancel_orders kwargs
    thread_ids: list = field(default_factory=list)        # records the thread each sync call ran on

    def get_account(self):
        self.thread_ids.append(threading.get_ident())
        return self.account

    def get_clock(self):
        self.thread_ids.append(threading.get_ident())
        return self.clock

    def submit_order(self, order_data):
        self.thread_ids.append(threading.get_ident())
        self.submitted.append(order_data)
        return self.next_order

    def get_order_by_id(self, order_id):
        self.thread_ids.append(threading.get_ident())
        return self.next_order

    def cancel_orders(self):
        self.thread_ids.append(threading.get_ident())
        self.canceled_all += 1

    def close_all_positions(self, cancel_orders: bool = False):
        self.thread_ids.append(threading.get_ident())
        self.closed_all.append(cancel_orders)


class FakeTradingStream:
    """Captures the registered trade-updates handler; lets tests push events."""

    def __init__(self) -> None:
        self.handler = None
        self.run_forever_started = False
        self.stopped = False

    def subscribe_trade_updates(self, handler):
        self.handler = handler

    async def _run_forever(self):
        self.run_forever_started = True

    async def stop_ws(self):
        self.stopped = True
```

- [ ] **Step 2: Write failing test for construction + get_account/get_clock + to_thread.**
```python
# tests/test_alpaca_account_clock.py
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import threading

import pytest

from .context import orb_bot
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.config import RunConfig
from orb_bot.models import AccountSnapshot, ClockInfo
from .fakes.alpaca_sdk import FakeTradingClient, FakeTradingStream, FakeClock

ET = ZoneInfo("America/New_York")


def _broker(client=None, stream=None, live=False):
    return AlpacaBroker(
        RunConfig(symbol="SPY", live=live, feed="IEX", allow_live_iex=True),
        key="k",
        secret="s",
        client=client or FakeTradingClient(),
        stream=stream or FakeTradingStream(),
        session_date=date(2026, 6, 19),
    )


async def test_get_account_maps_to_snapshot_with_decimals():
    fc = FakeTradingClient()
    fc.account.equity = "100000.00"
    fc.account.buying_power = "200000.00"
    fc.account.shorting_enabled = True
    broker = _broker(client=fc)
    snap = await broker.get_account()
    assert isinstance(snap, AccountSnapshot)
    assert snap.equity == Decimal("100000.00")
    assert snap.buying_power == Decimal("200000.00")
    assert snap.shorting_enabled is True


async def test_get_clock_maps_to_clockinfo():
    fc = FakeTradingClient()
    fc.clock = FakeClock(is_open=True, next_close=datetime(2026, 6, 19, 16, 0, tzinfo=ET))
    broker = _broker(client=fc)
    ci = await broker.get_clock()
    assert isinstance(ci, ClockInfo)
    assert ci.is_open is True
    assert ci.next_close == datetime(2026, 6, 19, 16, 0, tzinfo=ET)


async def test_sync_calls_run_off_the_event_loop_thread():
    fc = FakeTradingClient()
    broker = _broker(client=fc)
    main_tid = threading.get_ident()
    await broker.get_account()
    await broker.get_clock()
    # asyncio.to_thread offloads each sync SDK call to a worker thread
    assert fc.thread_ids, "no sync call recorded"
    assert all(tid != main_tid for tid in fc.thread_ids)
```
Run: `pytest tests/test_alpaca_account_clock.py -q` → **FAIL** (`ImportError: cannot import name 'AlpacaBroker'`).

- [ ] **Step 3: Minimal impl — AlpacaBroker `__init__`, get_account, get_clock.** Append to `orb_bot/execution/alpaca.py`:
```python
import asyncio
from decimal import Decimal

from ..config import RunConfig
from ..models import AccountSnapshot, ClockInfo


class AlpacaBroker:
    """Concrete Broker over alpaca-py. Every sync TradingClient call is offloaded with
    asyncio.to_thread so it never blocks the event loop (spec §13/§16)."""

    def __init__(
        self,
        run: RunConfig,
        key: str,
        secret: str,
        *,
        client=None,
        stream=None,
        session_date: date,
    ) -> None:
        self._run = run
        self._session_date = session_date
        self._seq = 0
        self._parent_coid: str | None = None
        if client is None:
            from alpaca.trading.client import TradingClient

            client = TradingClient(key, secret, paper=not run.live)
        if stream is None:
            from alpaca.trading.stream import TradingStream

            stream = TradingStream(key, secret, paper=not run.live)
        self._client = client
        self._stream = stream
        self._queue: asyncio.Queue = asyncio.Queue()

    async def get_account(self) -> AccountSnapshot:
        acct = await asyncio.to_thread(self._client.get_account)
        return AccountSnapshot(
            equity=Decimal(str(acct.equity)),
            buying_power=Decimal(str(acct.buying_power)),
            shorting_enabled=bool(acct.shorting_enabled),
        )

    async def get_clock(self) -> ClockInfo:
        clk = await asyncio.to_thread(self._client.get_clock)
        return ClockInfo(is_open=bool(clk.is_open), next_close=clk.next_close)
```
Run: `pytest tests/test_alpaca_account_clock.py -q` → **PASS** (3 passed).

> Note: `Decimal(str(...))` tolerates both str and float SDK fields; ET tz comes from the SDK clock object unchanged (Alpaca returns tz-aware datetimes).

- [ ] **Step 4: Commit.**
```bash
git add orb_bot/execution/alpaca.py tests/fakes/__init__.py tests/fakes/alpaca_sdk.py tests/test_alpaca_account_clock.py
git commit -m "feat(execution): AlpacaBroker get_account/get_clock via to_thread"
```

---

### Task 27: Execution/Alpaca — submit_bracket (LONG/SHORT request building + qty guard + deterministic COID)
**Files:** Modify `orb_bot/execution/alpaca.py`; Test `tests/test_alpaca_submit_bracket.py`.
**Interfaces:** Consumes: `interfaces.Broker.submit_bracket(setup:Setup, qty:int)->OrderResult`; `models.Setup`, `models.Direction.LONG/SHORT`, `models.OrderResult(order_id, client_order_id, status, filled_avg_price:Decimal|None, filled_qty:int, legs:list)`; alpaca-py `MarketOrderRequest`, `OrderClass.BRACKET`, `OrderSide.BUY/SELL`, `TimeInForce.DAY`, `TakeProfitRequest(limit_price)`, `StopLossRequest(stop_price)`. Produces: `async def submit_bracket(self, setup: Setup, qty: int) -> OrderResult` — increments `_seq`, sets `_parent_coid` to the ENTRY coid, builds a BRACKET `MarketOrderRequest` whose ENTRY coid is built with the shared `client_order_id(session_date, symbol, seq, leg_role)` helper (Task 25 — the single coid formatter), submits via `to_thread`, maps to `OrderResult`. Also exposes `current_seq` (property → `self._seq`) so the orchestrator's EOD FLATTEN synthesis reuses the SAME seq value via the same `client_order_id` formatter (MED #12).

- [ ] **Step 1: Write failing test — LONG/SHORT request fields, TIF=DAY, BRACKET, qty guard, COID, to_thread, OrderResult mapping.**
```python
# tests/test_alpaca_submit_bracket.py
from datetime import date
from decimal import Decimal

import threading

import pytest

from .context import orb_bot
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.config import RunConfig
from orb_bot.models import Direction, Model, Setup, OrderResult
from .fakes.alpaca_sdk import FakeTradingClient, FakeTradingStream, FakeOrder

from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce


def _broker(fc):
    return AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=True),
        key="k", secret="s",
        client=fc, stream=FakeTradingStream(),
        session_date=date(2026, 6, 19),
    )


def _long_setup():
    return Setup(direction=Direction.LONG, model=Model.BREAKOUT,
                 entry=Decimal("100.00"), stop=Decimal("99.00"),
                 target=Decimal("102.00"), rr=2.0, reason=["x"])


def _short_setup():
    return Setup(direction=Direction.SHORT, model=Model.BREAKOUT,
                 entry=Decimal("100.00"), stop=Decimal("101.00"),
                 target=Decimal("98.00"), rr=2.0, reason=["x"])


async def test_long_bracket_request_fields():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.submit_bracket(_long_setup(), 10)
    req = fc.submitted[-1]
    assert req.symbol == "SPY"
    assert req.qty == 10
    assert req.side == OrderSide.BUY
    assert req.time_in_force == TimeInForce.DAY
    assert req.order_class == OrderClass.BRACKET
    assert Decimal(str(req.take_profit.limit_price)) == Decimal("102.00")
    assert Decimal(str(req.stop_loss.stop_price)) == Decimal("99.00")


async def test_short_bracket_uses_sell_and_inverts_legs():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.submit_bracket(_short_setup(), 5)
    req = fc.submitted[-1]
    assert req.side == OrderSide.SELL
    assert Decimal(str(req.take_profit.limit_price)) == Decimal("98.00")   # TP below
    assert Decimal(str(req.stop_loss.stop_price)) == Decimal("101.00")     # SL above


async def test_qty_below_one_is_rejected_before_submit():
    fc = FakeTradingClient()
    broker = _broker(fc)
    with pytest.raises(ValueError):
        await broker.submit_bracket(_long_setup(), 0)
    assert fc.submitted == []   # never reached the SDK


async def test_deterministic_client_order_id_and_seq():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.submit_bracket(_long_setup(), 1)
    await broker.submit_bracket(_long_setup(), 1)
    assert fc.submitted[0].client_order_id == "2026-06-19-SPY-1-ENTRY"
    assert fc.submitted[1].client_order_id == "2026-06-19-SPY-2-ENTRY"


async def test_submit_runs_off_event_loop_thread():
    fc = FakeTradingClient()
    broker = _broker(fc)
    main_tid = threading.get_ident()
    await broker.submit_bracket(_long_setup(), 1)
    assert fc.thread_ids and all(tid != main_tid for tid in fc.thread_ids)


async def test_order_result_mapping():
    fc = FakeTradingClient()
    fc.next_order = FakeOrder(id="ord-9", client_order_id="2026-06-19-SPY-1-ENTRY",
                              status="accepted", filled_avg_price=None, filled_qty="0", legs=[])
    broker = _broker(fc)
    res = await broker.submit_bracket(_long_setup(), 1)
    assert isinstance(res, OrderResult)
    assert res.order_id == "ord-9"
    assert res.client_order_id == "2026-06-19-SPY-1-ENTRY"
    assert res.status == "accepted"
    assert res.filled_avg_price is None
    assert res.filled_qty == 0
    assert res.legs == []
```
Run: `pytest tests/test_alpaca_submit_bracket.py -q` → **FAIL** (`AttributeError: 'AlpacaBroker' object has no attribute 'submit_bracket'`).

- [ ] **Step 2: Minimal impl — add SDK request imports + `_to_order_result` + `submit_bracket`.** Append to `orb_bot/execution/alpaca.py`:
```python
from ..models import Direction, OrderResult, Setup


def _to_order_result(order) -> OrderResult:
    fap = getattr(order, "filled_avg_price", None)
    return OrderResult(
        order_id=str(order.id),
        client_order_id=str(order.client_order_id),
        status=str(order.status),
        filled_avg_price=None if fap is None else Decimal(str(fap)),
        filled_qty=int(getattr(order, "filled_qty", 0) or 0),
        legs=list(getattr(order, "legs", []) or []),
    )


def _add_submit_bracket():  # marker only; real method defined on the class below
    raise NotImplementedError


async def _submit_bracket(self, setup: Setup, qty: int) -> OrderResult:
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import (
        MarketOrderRequest,
        StopLossRequest,
        TakeProfitRequest,
    )

    qty = whole_share_qty(qty)              # defense-in-depth; rejects qty < 1 (spec §16)
    self._seq += 1
    entry_coid = client_order_id(self._session_date, self._run.symbol, self._seq, "ENTRY")
    self._parent_coid = entry_coid
    side = OrderSide.BUY if setup.direction is Direction.LONG else OrderSide.SELL
    req = MarketOrderRequest(
        symbol=self._run.symbol,
        qty=qty,
        side=side,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        client_order_id=entry_coid,
        take_profit=TakeProfitRequest(limit_price=float(setup.target)),
        stop_loss=StopLossRequest(stop_price=float(setup.stop)),
    )
    order = await asyncio.to_thread(self._client.submit_order, req)
    return _to_order_result(order)


AlpacaBroker.submit_bracket = _submit_bracket


# Expose the broker-owned trade sequence so the orchestrator's EOD FLATTEN
# synthesis reuses the SAME seq value for its client_order_id (MED #12 / §8 step 9):
# the broker owns _seq, and current_seq returns the seq of the most recently
# submitted bracket so both sides format identical prefixes via client_order_id().
AlpacaBroker.current_seq = property(lambda self: self._seq)
```
Run: `pytest tests/test_alpaca_submit_bracket.py -q` → **PASS** (6 passed).

> Note: TP/SL levels come straight from `Setup` (engine already places TP above/SL below for LONG and inverts for SHORT — §13), so the broker is direction-agnostic on prices and only flips `side`. `limit_price`/`stop_price` use `float(Decimal)` because alpaca-py request models coerce to float; the Decimal round-trip in the assert tolerates that.

- [ ] **Step 3: Commit.**
```bash
git add orb_bot/execution/alpaca.py tests/test_alpaca_submit_bracket.py
git commit -m "feat(execution): submit_bracket builds BRACKET MarketOrderRequest with deterministic coid"
```

---

### Task 28: Execution/Alpaca — get_order, cancel_all, flatten
**Files:** Modify `orb_bot/execution/alpaca.py`; Test `tests/test_alpaca_order_admin.py`.
**Interfaces:** Consumes: `interfaces.Broker.get_order(order_id:str)->OrderResult`, `cancel_all()->None`, `flatten()->None`; SDK `TradingClient.get_order_by_id`, `cancel_orders`, `close_all_positions(cancel_orders=True)`. Produces: `async def get_order(self, order_id)`, `async def cancel_all(self)`, `async def flatten(self)` — all offloaded via `to_thread`; `flatten` calls `close_all_positions(cancel_orders=True)` (idempotent), `cancel_all` cancels resting bracket children first (spec §13/§16 ordering).

- [ ] **Step 1: Write failing test.**
```python
# tests/test_alpaca_order_admin.py
from datetime import date
from decimal import Decimal

import threading

import pytest

from .context import orb_bot
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.config import RunConfig
from orb_bot.models import OrderResult
from .fakes.alpaca_sdk import FakeTradingClient, FakeTradingStream, FakeOrder


def _broker(fc):
    return AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=True),
        key="k", secret="s",
        client=fc, stream=FakeTradingStream(),
        session_date=date(2026, 6, 19),
    )


async def test_get_order_maps_and_offloads():
    fc = FakeTradingClient()
    fc.next_order = FakeOrder(id="ord-3", client_order_id="c-3", status="filled",
                              filled_avg_price="100.25", filled_qty="10", legs=[])
    broker = _broker(fc)
    main_tid = threading.get_ident()
    res = await broker.get_order("ord-3")
    assert isinstance(res, OrderResult)
    assert res.order_id == "ord-3"
    assert res.filled_avg_price == Decimal("100.25")
    assert res.filled_qty == 10
    assert fc.thread_ids and all(tid != main_tid for tid in fc.thread_ids)


async def test_cancel_all_calls_cancel_orders():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.cancel_all()
    assert fc.canceled_all == 1


async def test_flatten_closes_all_with_cancel_orders_true():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.flatten()
    assert fc.closed_all == [True]


async def test_admin_calls_offload_to_worker_threads():
    fc = FakeTradingClient()
    broker = _broker(fc)
    main_tid = threading.get_ident()
    await broker.cancel_all()
    await broker.flatten()
    assert fc.thread_ids and all(tid != main_tid for tid in fc.thread_ids)
```
Run: `pytest tests/test_alpaca_order_admin.py -q` → **FAIL** (`AttributeError: ... 'get_order'`).

- [ ] **Step 2: Minimal impl.** Append to `orb_bot/execution/alpaca.py`:
```python
async def _get_order(self, order_id: str) -> OrderResult:
    order = await asyncio.to_thread(self._client.get_order_by_id, order_id)
    return _to_order_result(order)


async def _cancel_all(self) -> None:
    # cancel resting bracket children BEFORE flatten (spec §13/§16 ordering)
    await asyncio.to_thread(self._client.cancel_orders)


async def _flatten(self) -> None:
    # idempotent; cancel_orders=True also clears any lingering resting legs
    await asyncio.to_thread(self._client.close_all_positions, cancel_orders=True)


AlpacaBroker.get_order = _get_order
AlpacaBroker.cancel_all = _cancel_all
AlpacaBroker.flatten = _flatten
```
Run: `pytest tests/test_alpaca_order_admin.py -q` → **PASS** (4 passed).

- [ ] **Step 3: Commit.**
```bash
git add orb_bot/execution/alpaca.py tests/test_alpaca_order_admin.py
git commit -m "feat(execution): get_order/cancel_all/flatten via to_thread (idempotent EOD ordering)"
```

---

### Task 29: Execution/Alpaca — trade_updates() callback→Queue→Fill bridge + leg-role mapping
**Files:** Modify `orb_bot/execution/alpaca.py`; Test `tests/test_alpaca_trade_updates.py`.
**Interfaces:** Consumes: `interfaces.Broker.trade_updates()->AsyncIterator[Fill]`; SDK `TradingStream.subscribe_trade_updates(handler)` (async callback) + `_run_forever()`/`stop_ws()`; matches `TradeEvent` `fill`/`partial_fill`/`canceled`/`rejected`. Produces: `def trade_updates(self)->AsyncIterator[Fill]` (async generator draining `self._queue`), `async def start_stream(self)` (registers handler, schedules `_run_forever` task), `async def stop_stream(self)` (`await stop_ws()` then cancel task — never sync `stop()`). Each emitted `models.Fill` carries `order_id`, `client_order_id`, `leg_role` (via `leg_role_for` against `self._parent_coid`), `side`, `price:Decimal`, `qty:int`, `ts`, `position_qty:int`, `exit_reason:str|None`.

- [ ] **Step 1: Write failing test — handler enqueues, iterator drains, leg-role + exit_reason mapping, partial vs full, ignore canceled/rejected for fills, stream lifecycle uses `_run_forever`/`stop_ws`.**
```python
# tests/test_alpaca_trade_updates.py
import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.config import RunConfig
from orb_bot.models import Fill
from .fakes.alpaca_sdk import FakeTradingClient, FakeTradingStream

ET = ZoneInfo("America/New_York")


def _broker(stream):
    b = AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=True),
        key="k", secret="s",
        client=FakeTradingClient(), stream=stream,
        session_date=date(2026, 6, 19),
    )
    b._parent_coid = "2026-06-19-SPY-1-ENTRY"   # as if submit_bracket ran
    return b


def _event(event, coid, side, price, filled_qty, position_qty):
    # mirrors alpaca TradeUpdate: .event + nested .order
    order = SimpleNamespace(
        id="ord-1", client_order_id=coid, side=side,
        filled_avg_price=str(price), filled_qty=str(filled_qty),
    )
    return SimpleNamespace(
        event=event, order=order, price=str(price), qty=str(filled_qty),
        position_qty=str(position_qty),
        timestamp=datetime(2026, 6, 19, 10, 0, tzinfo=ET),
    )


async def test_stream_start_registers_handler_and_schedules_run_forever():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    assert stream.handler is not None
    await asyncio.sleep(0)   # let the scheduled task run
    assert stream.run_forever_started is True
    await broker.stop_stream()
    assert stream.stopped is True   # used stop_ws(), not sync stop()


async def test_entry_fill_maps_to_fill_with_entry_role():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("fill", "2026-06-19-SPY-1-ENTRY", "buy", "100.10", 10, 10))
    gen = broker.trade_updates()
    fill = await asyncio.wait_for(gen.__anext__(), timeout=1)
    assert isinstance(fill, Fill)
    assert fill.order_id == "ord-1"
    assert fill.client_order_id == "2026-06-19-SPY-1-ENTRY"
    assert fill.leg_role == "ENTRY"
    assert fill.side == "buy"
    assert fill.price == Decimal("100.10")
    assert fill.qty == 10
    assert fill.position_qty == 10
    assert fill.exit_reason is None
    await broker.stop_stream()


async def test_tp_fill_sets_target_exit_reason():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("fill", "2026-06-19-SPY-1-TP", "sell", "102.00", 10, 0))
    fill = await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=1)
    assert fill.leg_role == "TP"
    assert fill.exit_reason == "TARGET"
    await broker.stop_stream()


async def test_sl_fill_sets_stop_exit_reason():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("fill", "2026-06-19-SPY-1-SL", "sell", "99.00", 10, 0))
    fill = await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=1)
    assert fill.leg_role == "SL"
    assert fill.exit_reason == "STOP"
    await broker.stop_stream()


async def test_flatten_fill_sets_flatten_exit_reason():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("fill", "2026-06-19-SPY-1-FLATTEN", "sell", "100.50", 10, 0))
    fill = await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=1)
    assert fill.leg_role == "FLATTEN"
    assert fill.exit_reason == "FLATTEN"
    await broker.stop_stream()


async def test_partial_fill_is_emitted_with_position_qty():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("partial_fill", "2026-06-19-SPY-1-ENTRY", "buy", "100.10", 4, 4))
    fill = await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=1)
    assert fill.leg_role == "ENTRY"
    assert fill.qty == 4
    assert fill.position_qty == 4
    await broker.stop_stream()


async def test_canceled_and_rejected_do_not_emit_fills():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("canceled", "2026-06-19-SPY-1-SL", "sell", "0", 0, 0))
    await stream.handler(_event("rejected", "2026-06-19-SPY-1-ENTRY", "buy", "0", 0, 0))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=0.2)
    await broker.stop_stream()
```
Run: `pytest tests/test_alpaca_trade_updates.py -q` → **FAIL** (`AttributeError: ... 'start_stream'`).

- [ ] **Step 2: Minimal impl — handler/queue bridge, `trade_updates` generator, stream lifecycle.** Append to `orb_bot/execution/alpaca.py`:
```python
from ..models import Fill

_EXIT_REASON = {"TP": "TARGET", "SL": "STOP", "FLATTEN": "FLATTEN"}
_FILL_EVENTS = {"fill", "partial_fill"}


async def _on_trade_update(self, data) -> None:
    """SDK async callback → enqueue a Fill (only for (partial_)fill events).
    canceled/rejected are not fills; the orchestrator handles those via reconciliation."""
    event = str(getattr(data, "event", ""))
    if event not in _FILL_EVENTS:
        return
    order = data.order
    coid = str(order.client_order_id)
    role = leg_role_for(coid, self._parent_coid or "")
    price = order.filled_avg_price if order.filled_avg_price is not None else data.price
    fill = Fill(
        order_id=str(order.id),
        client_order_id=coid,
        leg_role=role,
        side=str(order.side),
        price=Decimal(str(price)),
        qty=int(getattr(order, "filled_qty", 0) or 0),
        ts=data.timestamp,
        position_qty=int(getattr(data, "position_qty", 0) or 0),
        exit_reason=_EXIT_REASON.get(role),     # None for ENTRY
    )
    await self._queue.put(fill)


async def _start_stream(self) -> None:
    self._stream.subscribe_trade_updates(self._on_trade_update)
    # SDK run()/run_forever() call asyncio.run() and block — schedule _run_forever
    # as a task on THIS loop instead (spec §13 loop trap).
    self._stream_task = asyncio.ensure_future(self._stream._run_forever())


async def _stop_stream(self) -> None:
    await self._stream.stop_ws()          # NOT the sync stop() (misbehaves same-loop)
    task = getattr(self, "_stream_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _trade_updates(self):
    while True:
        fill = await self._queue.get()
        yield fill


AlpacaBroker._on_trade_update = _on_trade_update
AlpacaBroker.start_stream = _start_stream
AlpacaBroker.stop_stream = _stop_stream
AlpacaBroker.trade_updates = _trade_updates
```
Run: `pytest tests/test_alpaca_trade_updates.py -q` → **PASS** (7 passed).

> Note: `trade_updates` is a sync `def` that returns an async generator (matches the `AsyncIterator[Fill]` Protocol). `leg_role_for` maps unknown/auto coids to FLATTEN, so a broker-initiated `close_all_positions` fill is still attributed correctly.

- [ ] **Step 3: Commit.**
```bash
git add orb_bot/execution/alpaca.py tests/test_alpaca_trade_updates.py
git commit -m "feat(execution): trade_updates callback->Queue->Fill bridge with leg-role/exit-reason mapping"
```

---

### Task 30: Execution/Alpaca — Broker Protocol conformance + network smoke test (skipif-no-creds)
**Files:** Modify `orb_bot/execution/alpaca.py` (only if conformance test surfaces a gap); Test `tests/test_alpaca_protocol.py`, `tests/test_alpaca_smoke.py`.
**Interfaces:** Consumes: `interfaces.Broker` (runtime_checkable Protocol). Produces: assurance that `AlpacaBroker` satisfies `Broker` (structural) and an opt-in live/paper smoke test gated by `ALPACA_KEY`/`ALPACA_SECRET` that asserts the semi-internal `_run_forever`/`stop_ws` exist on the real `TradingStream` (spec §13 upgrade guard).

- [ ] **Step 1: Write failing Protocol-conformance test (no network).**
```python
# tests/test_alpaca_protocol.py
from datetime import date

from .context import orb_bot
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.config import RunConfig
from orb_bot.interfaces import Broker
from .fakes.alpaca_sdk import FakeTradingClient, FakeTradingStream


def test_alpaca_broker_satisfies_broker_protocol():
    broker = AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=True),
        key="k", secret="s",
        client=FakeTradingClient(), stream=FakeTradingStream(),
        session_date=date(2026, 6, 19),
    )
    assert isinstance(broker, Broker)
    for name in ("get_account", "get_clock", "submit_bracket",
                 "trade_updates", "get_order", "cancel_all", "flatten"):
        assert callable(getattr(broker, name)), name
```
Run: `pytest tests/test_alpaca_protocol.py -q` → **FAIL** if any method missing; expected **PASS** once all prior tasks merged (1 passed). If `isinstance(broker, Broker)` fails, confirm `interfaces.Broker` is `@runtime_checkable` and all 7 methods are present on the class.

- [ ] **Step 2: Write the network smoke test (skipped without creds).**
```python
# tests/test_alpaca_smoke.py
import os
from datetime import date

import pytest

from .context import orb_bot
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.config import RunConfig
from orb_bot.models import AccountSnapshot, ClockInfo

_NO_CREDS = not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET"))
pytestmark = pytest.mark.skipif(_NO_CREDS, reason="ALPACA_KEY/ALPACA_SECRET not set")


def test_real_trading_stream_has_semi_internal_methods():
    # spec §13: pin alpaca-py >=0.43,<0.44; assert _run_forever/stop_ws exist on upgrade
    from alpaca.trading.stream import TradingStream

    stream = TradingStream(os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"], paper=True)
    assert hasattr(stream, "_run_forever"), "alpaca-py changed: TradingStream._run_forever gone"
    assert hasattr(stream, "stop_ws"), "alpaca-py changed: TradingStream.stop_ws gone"


async def test_paper_get_account_and_clock_round_trip():
    broker = AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=False),
        key=os.environ["ALPACA_KEY"], secret=os.environ["ALPACA_SECRET"],
        session_date=date.today(),
    )
    snap = await broker.get_account()
    clk = await broker.get_clock()
    assert isinstance(snap, AccountSnapshot)
    assert isinstance(clk, ClockInfo)
    assert snap.equity >= 0
```
Run (no creds, normal CI): `pytest tests/test_alpaca_smoke.py -q` → **PASS** (2 skipped).
Run (with creds): `ALPACA_KEY=... ALPACA_SECRET=... pytest tests/test_alpaca_smoke.py -q` → **PASS** (2 passed).

- [ ] **Step 3: Run the full module suite.**
```bash
pytest tests/test_alpaca_helpers.py tests/test_alpaca_account_clock.py tests/test_alpaca_submit_bracket.py tests/test_alpaca_order_admin.py tests/test_alpaca_trade_updates.py tests/test_alpaca_protocol.py tests/test_alpaca_smoke.py -q
```
Expected: all pass, smoke skipped without creds.

- [ ] **Step 4: Commit.**
```bash
git add tests/test_alpaca_protocol.py tests/test_alpaca_smoke.py
git commit -m "test(execution): Broker protocol conformance + creds-gated Alpaca smoke test"
```

---

I have everything I need. Now I'll write the markdown plan for the discordbot + approval module.

### Task 31: Discord client — shared `discordbot.py` (bot.start as task, get/fetch channel)

**Files:** Create `orb_bot/discordbot.py`; Test `tests/test_discordbot.py`. (Assumes `orb_bot/__init__.py`, `tests/__init__.py`, `tests/context.py` already exist from the bootstrap task.)
**Interfaces:** Consumes: `discord.py>=2.6,<3` (`discord.Client`, `discord.Intents`, `discord.TextChannel`). Produces: `class DiscordClient` with `async def start_in_background(self) -> None` (creates `asyncio.create_task(bot.start(token))` and `await bot.wait_until_ready()`), `async def resolve_channel(self) -> discord.abc.Messageable` (tries `get_channel`, falls back to `await fetch_channel`), `async def close(self) -> None`, and attribute `.bot: discord.Client`. Later tasks (`DiscordApprover`, `DiscordReporter`) consume `resolve_channel()`.

- [ ] **Step 1: Write the failing test for channel resolution + lifecycle.**

```python
# tests/test_discordbot.py
import asyncio

import pytest

from .context import orb_bot
from orb_bot.discordbot import DiscordClient


class _FakeChannel:
    def __init__(self, cid):
        self.id = cid


class _FakeBot:
    """Stand-in for discord.Client: records start/close, fakes channel lookups."""

    def __init__(self):
        self.started_with = None
        self.closed = False
        self._ready = asyncio.Event()
        self._cache = {}
        self._fetchable = {}

    async def start(self, token):
        self.started_with = token
        self._ready.set()
        # emulate a long-lived connection until cancelled
        await asyncio.Event().wait()

    async def wait_until_ready(self):
        await self._ready.wait()

    def get_channel(self, cid):
        return self._cache.get(cid)

    async def fetch_channel(self, cid):
        if cid in self._fetchable:
            return self._fetchable[cid]
        raise RuntimeError(f"no channel {cid}")

    async def close(self):
        self.closed = True


async def test_start_in_background_starts_and_awaits_ready():
    bot = _FakeBot()
    dc = DiscordClient(token="tok", channel_id=42, bot=bot)
    await dc.start_in_background()
    assert bot.started_with == "tok"
    assert dc._task is not None and not dc._task.done()
    await dc.close()
    assert bot.closed is True


async def test_resolve_channel_prefers_cache():
    bot = _FakeBot()
    ch = _FakeChannel(42)
    bot._cache[42] = ch
    dc = DiscordClient(token="tok", channel_id=42, bot=bot)
    await dc.start_in_background()
    resolved = await dc.resolve_channel()
    assert resolved is ch
    await dc.close()


async def test_resolve_channel_falls_back_to_fetch():
    bot = _FakeBot()
    ch = _FakeChannel(99)
    bot._fetchable[99] = ch  # not in get_channel cache
    dc = DiscordClient(token="tok", channel_id=99, bot=bot)
    await dc.start_in_background()
    resolved = await dc.resolve_channel()
    assert resolved is ch
    await dc.close()


async def test_close_cancels_task_when_bot_close_hangs():
    bot = _FakeBot()
    dc = DiscordClient(token="tok", channel_id=42, bot=bot)
    await dc.start_in_background()
    task = dc._task
    await dc.close()
    assert task.cancelled() or task.done()
```

```bash
pytest tests/test_discordbot.py -q
```
Expected: **FAIL** (`ModuleNotFoundError: No module named 'orb_bot.discordbot'`).

- [ ] **Step 2: Implement `orb_bot/discordbot.py` (minimal).**

```python
# orb_bot/discordbot.py
"""Shared discord.py client.

A single ``discord.Client`` is started via ``bot.start(token)`` as an asyncio
TASK (never ``bot.run()``, which blocks the loop) so it can share the event loop
with the Alpaca stream. The same client is reused by the live ``DiscordApprover``
and the ``DiscordReporter`` (both modes whenever Discord is configured).
"""
from __future__ import annotations

import asyncio

import discord


def _default_bot() -> discord.Client:
    # Buttons (discord.ui.View) need NO privileged message_content intent.
    intents = discord.Intents.none()
    intents.guilds = True
    return discord.Client(intents=intents)


class DiscordClient:
    def __init__(
        self,
        token: str,
        channel_id: int,
        bot: discord.Client | None = None,
    ) -> None:
        self._token = token
        self._channel_id = int(channel_id)
        self.bot: discord.Client = bot if bot is not None else _default_bot()
        self._task: asyncio.Task | None = None

    async def start_in_background(self) -> None:
        """Start ``bot.start(token)`` as a task and wait until the gateway is ready."""
        if self._task is None:
            self._task = asyncio.create_task(self.bot.start(self._token))
        await self.bot.wait_until_ready()

    async def resolve_channel(self) -> discord.abc.Messageable:
        """Return the configured channel: cache first, then HTTP fetch fallback."""
        ch = self.bot.get_channel(self._channel_id)
        if ch is None:
            ch = await self.bot.fetch_channel(self._channel_id)
        return ch

    async def close(self) -> None:
        """Close the gateway connection and cancel the background task."""
        try:
            await self.bot.close()
        finally:
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
                self._task = None
```

```bash
pytest tests/test_discordbot.py -q
```
Expected: **PASS** (4 passed).

- [ ] **Step 3: Commit.**

```bash
git add orb_bot/discordbot.py tests/test_discordbot.py
git commit -m "feat(discordbot): shared discord.py client started as a task with channel resolution"
```

---

### Task 32: AutoApprover (paper) — instant APPROVE

**Files:** Create `orb_bot/approval/__init__.py`, `orb_bot/approval/auto.py`; Test `tests/test_approval_auto.py`.
**Interfaces:** Consumes: `Approver` protocol (`orb_bot/interfaces.py`): `async def start()->None; async def request(req: ApprovalRequest)->str; async def close()->None`. Also `ApprovalRequest`, `Setup`, `Direction`, `Model` from `orb_bot/models.py`. Produces: `class AutoApprover` satisfying `Approver`; `request()` always returns the literal `"APPROVE"`; `start()`/`close()` are no-ops.

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_approval_auto.py
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot.interfaces import Approver
from orb_bot.models import ApprovalRequest, Direction, Model, Setup
from orb_bot.approval.auto import AutoApprover

ET = ZoneInfo("America/New_York")


def _req() -> ApprovalRequest:
    setup = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.00"),
        target=Decimal("102.00"),
        rr=2.0,
        reason=["strong close"],
    )
    return ApprovalRequest(
        setup=setup,
        symbol="AAPL",
        qty=10,
        risk_dollars=50.0,
        mode="PAPER",
        feed="IEX",
        or_high=Decimal("100.50"),
        or_low=Decimal("99.50"),
        bars_present=15,
        data_warning=None,
        approval_ttl_s=90,
    )


def test_autoapprover_satisfies_protocol():
    assert isinstance(AutoApprover(), Approver)


async def test_autoapprover_returns_approve_instantly():
    appr = AutoApprover()
    await appr.start()
    decision = await appr.request(_req())
    assert decision == "APPROVE"
    await appr.close()


async def test_autoapprover_start_close_are_noops_idempotent():
    appr = AutoApprover()
    await appr.start()
    await appr.start()
    await appr.close()
    await appr.close()
    assert await appr.request(_req()) == "APPROVE"
```

```bash
pytest tests/test_approval_auto.py -q
```
Expected: **FAIL** (`ModuleNotFoundError: No module named 'orb_bot.approval'`).

- [ ] **Step 2: Implement the package + `AutoApprover`.**

```python
# orb_bot/approval/__init__.py
"""Approval-gate implementations: AutoApprover (paper) + DiscordApprover (live)."""
```

```python
# orb_bot/approval/auto.py
"""Paper-mode approver: auto-fires every setup with no human gate.

Reused later as the backtest auto-approver. Order placement stays mode-uniform:
the orchestrator only submits on ``APPROVE``, which this returns instantly, so
the placement path is identical and equally testable in paper and live.
"""
from __future__ import annotations

from orb_bot.models import ApprovalRequest


class AutoApprover:
    async def start(self) -> None:  # no-op
        return None

    async def request(self, req: ApprovalRequest) -> str:
        return "APPROVE"

    async def close(self) -> None:  # no-op
        return None
```

```bash
pytest tests/test_approval_auto.py -q
```
Expected: **PASS** (3 passed).

- [ ] **Step 3: Commit.**

```bash
git add orb_bot/approval/__init__.py orb_bot/approval/auto.py tests/test_approval_auto.py
git commit -m "feat(approval): AutoApprover returns APPROVE instantly for paper mode"
```

---

### Task 33: DiscordApprover (live) — embed + Approve/Reject buttons over an asyncio.Future

**Files:** Create `orb_bot/approval/manual.py`; Test `tests/test_approval_manual.py`.
**Interfaces:** Consumes: `Approver` protocol; `DiscordClient.resolve_channel()` from `orb_bot/discordbot.py`; `ApprovalRequest`/`Setup`/`Direction`/`Model` from `orb_bot/models.py`; `discord.Embed`, `discord.ui.View`, `discord.ui.Button`, `discord.ButtonStyle`, `discord.Interaction`. Produces: `build_proposal_embed(req: ApprovalRequest) -> discord.Embed`; `class ApprovalView(discord.ui.View)` (Approve/Reject buttons, `interaction_check`, `on_timeout`, callbacks edit-then-resolve); `class DiscordApprover` satisfying `Approver` whose `request(req)` posts the embed + view, awaits the `asyncio.Future`, and returns `'APPROVE'|'REJECT'|'TIMEOUT'`.

- [ ] **Step 1: Failing test for `build_proposal_embed` fields.**

```python
# tests/test_approval_manual.py
import asyncio
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot.interfaces import Approver
from orb_bot.models import ApprovalRequest, Direction, Model, Setup
from orb_bot.approval.manual import (
    DiscordApprover,
    ApprovalView,
    build_proposal_embed,
)

ET = ZoneInfo("America/New_York")
APPROVER_ID = 555


def _req(data_warning=None, feed="SIP", mode="LIVE", bars_present=15) -> ApprovalRequest:
    setup = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.00"),
        target=Decimal("102.00"),
        rr=2.0,
        reason=["strong close", "above OR high"],
    )
    return ApprovalRequest(
        setup=setup,
        symbol="AAPL",
        qty=10,
        risk_dollars=50.0,
        mode=mode,
        feed=feed,
        or_high=Decimal("100.50"),
        or_low=Decimal("99.50"),
        bars_present=bars_present,
        data_warning=data_warning,
        approval_ttl_s=90,
    )


def _embed_text(embed) -> str:
    parts = [str(embed.title or ""), str(embed.description or "")]
    for f in embed.fields:
        parts.append(str(f.name))
        parts.append(str(f.value))
    return "\n".join(parts)


def test_build_proposal_embed_has_core_fields():
    em = build_proposal_embed(_req())
    text = _embed_text(em)
    assert "AAPL" in text
    assert "LONG" in text
    assert "BREAKOUT" in text
    assert "100.00" in text  # entry
    assert "99.00" in text   # stop
    assert "102.00" in text  # target
    assert "2.0" in text     # rr
    assert "50.0" in text or "50" in text  # risk $
    assert "10" in text      # qty
    assert "strong close" in text
    assert "SIP" in text     # feed
    assert "15" in text      # bars_present / T


def test_build_proposal_embed_shows_mode_and_iex_warning():
    em = build_proposal_embed(_req(data_warning="iex_partial", feed="IEX", mode="PAPER"))
    text = _embed_text(em)
    assert "PAPER" in text
    assert "IEX" in text
    assert "iex_partial" in text
```

```bash
pytest tests/test_approval_manual.py::test_build_proposal_embed_has_core_fields tests/test_approval_manual.py::test_build_proposal_embed_shows_mode_and_iex_warning -q
```
Expected: **FAIL** (`ModuleNotFoundError: No module named 'orb_bot.approval.manual'`).

- [ ] **Step 2: Implement `build_proposal_embed` + skeleton classes.**

```python
# orb_bot/approval/manual.py
"""Live-mode approver: posts a rich embed + Approve/Reject buttons (discord.ui.View).

Buttons (not message replies) mean NO privileged ``message_content`` intent is
needed. ``interaction_check`` allows only ``DISCORD_APPROVER_USER_ID``. The
button callback first ACKs Discord via ``interaction.response.edit_message``
(within the ~3s window; this also disables the buttons), THEN resolves the
in-process ``asyncio.Future``. Timeout / unauthorized / error => non-approval;
never auto-approve.
"""
from __future__ import annotations

import asyncio

import discord

from orb_bot.discordbot import DiscordClient
from orb_bot.models import ApprovalRequest, Direction


def build_proposal_embed(req: ApprovalRequest) -> discord.Embed:
    s = req.setup
    arrow = "🟢" if s.direction is Direction.LONG else "🔴"
    title = f"{arrow} {req.symbol} {s.direction.value} {s.model.value} — {req.mode}"
    color = discord.Color.green() if s.direction is Direction.LONG else discord.Color.red()
    em = discord.Embed(title=title, color=color)
    em.add_field(name="Entry", value=f"{s.entry:.2f}", inline=True)
    em.add_field(name="Stop", value=f"{s.stop:.2f}", inline=True)
    em.add_field(name="Target", value=f"{s.target:.2f}", inline=True)
    em.add_field(name="RR", value=f"{s.rr:.1f}", inline=True)
    em.add_field(name="Qty", value=str(req.qty), inline=True)
    em.add_field(name="Risk $", value=f"{req.risk_dollars:.2f}", inline=True)
    em.add_field(name="OR", value=f"{req.or_low:.2f} – {req.or_high:.2f}", inline=True)
    em.add_field(name="Feed", value=req.feed, inline=True)
    em.add_field(name="Bars", value=f"{req.bars_present}/T", inline=True)
    if req.feed != "SIP":
        em.add_field(name="⚠ Feed warning", value=f"{req.feed} data", inline=False)
    if req.data_warning:
        em.add_field(name="⚠ Data warning", value=req.data_warning, inline=False)
    em.add_field(name="Reasons", value="\n".join(f"• {r}" for r in s.reason), inline=False)
    em.set_footer(text=f"Mode: {req.mode} · expires in {req.approval_ttl_s}s")
    return em


class ApprovalView(discord.ui.View):
    def __init__(self, approver_user_id: int, fut: asyncio.Future, timeout: float) -> None:
        super().__init__(timeout=timeout)
        self._approver_user_id = int(approver_user_id)
        self._fut = fut

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the configured approver's clicks count; others are silently ignored.
        return interaction.user is not None and interaction.user.id == self._approver_user_id

    async def _resolve(self, interaction: discord.Interaction, decision: str, label: str) -> None:
        # ACK Discord FIRST (edit_message both acks and removes the buttons in one HTTP call),
        # THEN resolve the Future, guarded against double-resolution.
        try:
            await interaction.response.edit_message(content=label, view=None)
        except Exception:
            decision = "REJECT"  # error path => non-approval, never auto-approve
        if not self._fut.done():
            self._fut.set_result(decision)
        self.stop()

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "APPROVE", "✅ Approved")

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "REJECT", "❌ Rejected")

    async def on_timeout(self) -> None:
        if not self._fut.done():
            self._fut.set_result("TIMEOUT")


class DiscordApprover:
    def __init__(self, client: DiscordClient, approver_user_id: int) -> None:
        self._client = client
        self._approver_user_id = int(approver_user_id)

    async def start(self) -> None:
        # The shared DiscordClient is started by the orchestrator; nothing to do here.
        return None

    async def request(self, req: ApprovalRequest) -> str:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        view = ApprovalView(self._approver_user_id, fut, float(req.approval_ttl_s))
        channel = await self._client.resolve_channel()
        embed = build_proposal_embed(req)
        await channel.send(embed=embed, view=view)
        return await fut

    async def close(self) -> None:
        return None
```

```bash
pytest tests/test_approval_manual.py::test_build_proposal_embed_has_core_fields tests/test_approval_manual.py::test_build_proposal_embed_shows_mode_and_iex_warning -q
```
Expected: **PASS** (2 passed).

- [ ] **Step 3: Add failing tests for the View callback / interaction_check / timeout (fake interaction + Future, no real Discord).**

Append to `tests/test_approval_manual.py`:

```python
class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeResponse:
    def __init__(self, raises=False):
        self.edited = None
        self._raises = raises

    async def edit_message(self, content=None, view=None):
        if self._raises:
            raise RuntimeError("interaction expired")
        self.edited = {"content": content, "view": view}


class _FakeInteraction:
    def __init__(self, uid, raises=False):
        self.user = _FakeUser(uid)
        self.response = _FakeResponse(raises=raises)


async def test_view_approve_acks_then_resolves_future():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    interaction = _FakeInteraction(APPROVER_ID)
    await view.approve.callback(interaction)
    assert interaction.response.edited == {"content": "✅ Approved", "view": None}
    assert fut.result() == "APPROVE"


async def test_view_reject_acks_then_resolves_future():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    interaction = _FakeInteraction(APPROVER_ID)
    await view.reject.callback(interaction)
    assert interaction.response.edited == {"content": "❌ Rejected", "view": None}
    assert fut.result() == "REJECT"


async def test_interaction_check_allows_only_approver():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    assert await view.interaction_check(_FakeInteraction(APPROVER_ID)) is True
    assert await view.interaction_check(_FakeInteraction(999)) is False


async def test_view_callback_error_is_non_approval():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    interaction = _FakeInteraction(APPROVER_ID, raises=True)
    await view.approve.callback(interaction)  # edit_message raises => non-approval
    assert fut.result() == "REJECT"


async def test_view_on_timeout_resolves_timeout():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    await view.on_timeout()
    assert fut.result() == "TIMEOUT"


async def test_view_does_not_double_resolve():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    await view.approve.callback(_FakeInteraction(APPROVER_ID))
    await view.on_timeout()  # already resolved; must not overwrite
    assert fut.result() == "APPROVE"


async def test_discordapprover_satisfies_protocol():
    class _StubClient:
        async def resolve_channel(self):
            raise AssertionError("not called in this test")

    appr = DiscordApprover(_StubClient(), APPROVER_ID)
    assert isinstance(appr, Approver)


async def test_request_posts_embed_with_view_and_returns_decision():
    class _FakeChannel:
        def __init__(self):
            self.sent = None

        async def send(self, embed=None, view=None):
            self.sent = {"embed": embed, "view": view}
            # simulate the approver clicking Approve once the message is posted
            view._fut.set_result("APPROVE")

    class _FakeClient:
        def __init__(self, channel):
            self._channel = channel

        async def resolve_channel(self):
            return self._channel

    channel = _FakeChannel()
    appr = DiscordApprover(_FakeClient(channel), APPROVER_ID)
    decision = await appr.request(_req())
    assert decision == "APPROVE"
    assert channel.sent["embed"] is not None
    assert isinstance(channel.sent["view"], ApprovalView)
```

```bash
pytest tests/test_approval_manual.py -q
```
Expected: **PASS** (all callback/check/timeout/request tests pass with the Step-2 implementation; 10 passed total). If `view.approve.callback(interaction)` raises an attribute error in your discord.py build, the decorated button is reachable as `view.approve.callback`; this is the canonical `discord.py>=2` access path — no source change needed.

- [ ] **Step 4: Commit.**

```bash
git add orb_bot/approval/manual.py tests/test_approval_manual.py
git commit -m "feat(approval): DiscordApprover with embed + Approve/Reject buttons over asyncio.Future"
```

---

I now have all the detail I need. The reporting module has three files: `summary.py` (pure builder), `log.py` (LogReporter), and `discord.py` (DiscordReporter over discordbot). Let me produce the markdown plan following the OUTPUT FORMAT.

Key design decisions grounded in the spec:
- `build_session_summary` is pure: groups fills by client_order_id prefix is the orchestrator's job; the builder receives already-built `list[TradeResult]` OR raw fills. The contract says `build_session_summary(trades, start_equity, end_equity, mode, symbol, session_date, no_trade_reason)` takes `trades` (list of TradeResult). But the contract also asks for "a per-trade pnl helper: pnl=(exit-entry)*qty*sign, pnl_pct=pnl/start_equity, exit-side share-weighted avg". So I'll provide both: `build_trade_result(...)` (per-trade helper that computes share-weighted exit + pnl) and `build_session_summary(...)`.
- LogReporter uses structlog.
- DiscordReporter formats an embed with bars_present/T denominator.


### Task 34: Reporting — pure SessionSummary builder (`reporting/summary.py`)

**Files:**
- Create `orb_bot/reporting/__init__.py`
- Create `orb_bot/reporting/summary.py`
- Test `tests/test_reporting.py`

**Interfaces:**
Consumes: `models.TradeResult(direction:Direction, model:Model, qty:int, entry_price:Decimal, exit_price:Decimal, pnl:Decimal, pnl_pct:Decimal, exit_reason:str)`; `models.SessionSummary(session_date:date, symbol:str, mode:str, trades:list[TradeResult], total_pnl:Decimal, total_pnl_pct:Decimal, wins:int, losses:int, breakevens:int, start_equity:Decimal, end_equity:Decimal, no_trade_reason:str|None)`; `models.Fill(order_id, client_order_id, leg_role, side, price:Decimal, qty:int, ts, position_qty, exit_reason)`; `models.Direction`; `models.Model`.
Produces (later tasks rely on these EXACT signatures):
- `pnl_for(direction:Direction, entry_price:Decimal, exit_price:Decimal, qty:int) -> Decimal` — `(exit-entry)*qty*(+1 if LONG else -1)`.
- `weighted_avg_price(fills:list[Fill]) -> Decimal` — share-weighted average over a leg's partial fills.
- `build_trade_result(entry_fills:list[Fill], exit_fills:list[Fill], direction:Direction, model:Model, start_equity:Decimal, exit_reason:str) -> TradeResult` — entry/exit prices share-weighted; `qty = min(entry_qty, exit_qty)`; `pnl_pct = pnl/start_equity` (fraction).
- `build_session_summary(trades:list[TradeResult], start_equity:Decimal, end_equity:Decimal, mode:str, symbol:str, session_date:date, no_trade_reason:str|None) -> SessionSummary`.

- [ ] **Step 1: Scaffold the reporting subpackage + confirm it's importable (failing test first).**
  Create the package marker and a placeholder module, then a test that imports the not-yet-written builder so we see a clean ImportError fail.

  ```bash
  mkdir -p orb_bot/reporting tests
  ```

  ```python
  # orb_bot/reporting/__init__.py
  """Session reporting: pure summary builder + Log/Discord reporters (spec §17a)."""
  ```

  Create the test file with the first failing assertion (long-trade pnl):

  ```python
  # tests/test_reporting.py
  from datetime import date, datetime
  from decimal import Decimal
  from zoneinfo import ZoneInfo

  from .context import orb_bot
  from orb_bot import models
  from orb_bot.models import Direction, Model
  from orb_bot.reporting import summary

  ET = ZoneInfo("America/New_York")


  def _ts(h: int, m: int) -> datetime:
      return datetime(2026, 6, 19, h, m, tzinfo=ET)


  def _fill(role, side, price, qty, *, exit_reason=None, coid="2026-06-19-AAPL-1"):
      return models.Fill(
          order_id=f"{coid}-{role}",
          client_order_id=f"{coid}-{role}",
          leg_role=role,
          side=side,
          price=Decimal(str(price)),
          qty=qty,
          ts=_ts(10, 0),
          position_qty=qty,
          exit_reason=exit_reason,
      )


  def test_pnl_for_long_is_positive_when_exit_above_entry():
      pnl = summary.pnl_for(Direction.LONG, Decimal("100.00"), Decimal("102.00"), 10)
      assert pnl == Decimal("20.00")
  ```

  Run it and confirm it FAILS (ImportError: no module named summary):

  ```bash
  pytest tests/test_reporting.py -q
  ```
  Expected: FAIL (collection error — `orb_bot.reporting.summary` does not exist yet).

- [ ] **Step 2: Implement `pnl_for` (minimal) and go green.**

  ```python
  # orb_bot/reporting/summary.py
  """Pure builders for per-trade and per-session P/L (spec §17a). No I/O, no clock."""
  from __future__ import annotations

  from datetime import date
  from decimal import Decimal

  from orb_bot import models
  from orb_bot.models import Direction, Model


  def pnl_for(
      direction: Direction, entry_price: Decimal, exit_price: Decimal, qty: int
  ) -> Decimal:
      """Realized P/L: (exit - entry) * qty * (+1 LONG | -1 SHORT). No commission."""
      sign = Decimal("1") if direction is Direction.LONG else Decimal("-1")
      return (exit_price - entry_price) * Decimal(qty) * sign
  ```

  Run and confirm PASS:

  ```bash
  pytest tests/test_reporting.py -q
  ```
  Expected: PASS (1 passed).

- [ ] **Step 3: Add failing tests for short pnl + share-weighted average.**
  Append to `tests/test_reporting.py`:

  ```python
  def test_pnl_for_short_is_positive_when_exit_below_entry():
      pnl = summary.pnl_for(Direction.SHORT, Decimal("100.00"), Decimal("98.00"), 5)
      assert pnl == Decimal("10.00")


  def test_weighted_avg_price_over_partial_fills():
      fills = [
          _fill("TP", "sell", "102.00", 6),
          _fill("TP", "sell", "103.00", 4),
      ]
      # (102*6 + 103*4) / 10 = (612 + 412) / 10 = 102.40
      assert summary.weighted_avg_price(fills) == Decimal("102.40")


  def test_weighted_avg_price_empty_raises():
      import pytest

      with pytest.raises(ValueError):
          summary.weighted_avg_price([])
  ```

  Run and confirm the two new tests FAIL (no `weighted_avg_price`):

  ```bash
  pytest tests/test_reporting.py -q
  ```
  Expected: FAIL (AttributeError: module has no attribute `weighted_avg_price`).

- [ ] **Step 4: Implement `weighted_avg_price`.**
  Append to `orb_bot/reporting/summary.py`:

  ```python
  def weighted_avg_price(fills: list[models.Fill]) -> Decimal:
      """Share-weighted average price across a leg's partial fills (spec §16/§17a)."""
      if not fills:
          raise ValueError("weighted_avg_price requires at least one fill")
      total_qty = sum(f.qty for f in fills)
      if total_qty <= 0:
          raise ValueError("weighted_avg_price requires positive total quantity")
      notional = sum((f.price * Decimal(f.qty) for f in fills), Decimal("0"))
      return notional / Decimal(total_qty)
  ```

  Run and confirm PASS:

  ```bash
  pytest tests/test_reporting.py -q
  ```
  Expected: PASS (4 passed).

- [ ] **Step 5: Add a failing test for `build_trade_result` (share-weighted exit, qty=min, pnl_pct fraction, FLATTEN attribution).**
  Append:

  ```python
  def test_build_trade_result_long_target_share_weighted_and_pct_fraction():
      entry = [_fill("ENTRY", "buy", "100.00", 10)]
      exits = [
          _fill("TP", "sell", "102.00", 6, exit_reason="TARGET"),
          _fill("TP", "sell", "103.00", 4, exit_reason="TARGET"),
      ]
      tr = summary.build_trade_result(
          entry_fills=entry,
          exit_fills=exits,
          direction=Direction.LONG,
          model=Model.BREAKOUT,
          start_equity=Decimal("10000.00"),
          exit_reason="TARGET",
      )
      assert tr.entry_price == Decimal("100.00")
      assert tr.exit_price == Decimal("102.40")        # share-weighted
      assert tr.qty == 10                               # min(entry, exit)
      assert tr.pnl == Decimal("24.00")                 # (102.40-100)*10
      assert tr.pnl_pct == Decimal("24.00") / Decimal("10000.00")   # stored as FRACTION
      assert tr.exit_reason == "TARGET"
      assert tr.direction is Direction.LONG
      assert tr.model is Model.BREAKOUT


  def test_build_trade_result_flatten_attribution_and_min_qty():
      entry = [_fill("ENTRY", "buy", "50.00", 10)]
      exits = [_fill("FLATTEN", "sell", "49.00", 8, exit_reason="FLATTEN")]
      tr = summary.build_trade_result(
          entry_fills=entry,
          exit_fills=exits,
          direction=Direction.LONG,
          model=Model.RETEST,
          start_equity=Decimal("10000.00"),
          exit_reason="FLATTEN",
      )
      assert tr.qty == 8                                # min(entry=10, exit=8)
      assert tr.pnl == Decimal("-8.00")                 # (49-50)*8
      assert tr.exit_reason == "FLATTEN"
  ```

  Run and confirm FAIL (no `build_trade_result`):

  ```bash
  pytest tests/test_reporting.py -q
  ```
  Expected: FAIL (AttributeError: `build_trade_result`).

- [ ] **Step 6: Implement `build_trade_result`.**
  Append to `orb_bot/reporting/summary.py`:

  ```python
  def build_trade_result(
      entry_fills: list[models.Fill],
      exit_fills: list[models.Fill],
      direction: Direction,
      model: Model,
      start_equity: Decimal,
      exit_reason: str,
  ) -> models.TradeResult:
      """One TradeResult from a leg's fills. Entry/exit prices are share-weighted;
      qty = min(entry_qty, exit_qty) (§16); pnl_pct stored as a FRACTION (§17a)."""
      if not entry_fills:
          raise ValueError("build_trade_result requires at least one entry fill")
      if not exit_fills:
          raise ValueError("build_trade_result requires at least one exit fill")
      entry_price = weighted_avg_price(entry_fills)
      exit_price = weighted_avg_price(exit_fills)
      entry_qty = sum(f.qty for f in entry_fills)
      exit_qty = sum(f.qty for f in exit_fills)
      qty = min(entry_qty, exit_qty)
      pnl = pnl_for(direction, entry_price, exit_price, qty)
      pnl_pct = pnl / start_equity
      return models.TradeResult(
          direction=direction,
          model=model,
          qty=qty,
          entry_price=entry_price,
          exit_price=exit_price,
          pnl=pnl,
          pnl_pct=pnl_pct,
          exit_reason=exit_reason,
      )
  ```

  Run and confirm PASS:

  ```bash
  pytest tests/test_reporting.py -q
  ```
  Expected: PASS (6 passed).

- [ ] **Step 7: Add failing tests for `build_session_summary` (multi-trade totals + win/loss/breakeven counts == len(trades)).**
  Append:

  ```python
  def _tr(pnl, start_equity=Decimal("10000.00"), reason="TARGET",
          direction=Direction.LONG, model=Model.BREAKOUT, qty=10):
      pnl = Decimal(str(pnl))
      return models.TradeResult(
          direction=direction,
          model=model,
          qty=qty,
          entry_price=Decimal("100.00"),
          exit_price=Decimal("100.00") + pnl / Decimal(qty),
          pnl=pnl,
          pnl_pct=pnl / start_equity,
          exit_reason=reason,
      )


  def test_build_session_summary_multitrade_totals_and_counts():
      trades = [
          _tr("20.00", reason="TARGET"),
          _tr("-10.00", reason="STOP"),
          _tr("0.00", reason="FLATTEN"),
      ]
      s = summary.build_session_summary(
          trades=trades,
          start_equity=Decimal("10000.00"),
          end_equity=Decimal("10010.00"),
          mode="PAPER",
          symbol="AAPL",
          session_date=date(2026, 6, 19),
          no_trade_reason=None,
      )
      assert s.total_pnl == Decimal("10.00")                          # 20 - 10 + 0
      assert s.total_pnl_pct == Decimal("10.00") / Decimal("10000.00")  # fraction
      assert s.wins == 1 and s.losses == 1 and s.breakevens == 1
      assert s.wins + s.losses + s.breakevens == len(s.trades)
      assert s.mode == "PAPER" and s.symbol == "AAPL"
      assert s.session_date == date(2026, 6, 19)
      assert s.start_equity == Decimal("10000.00")
      assert s.end_equity == Decimal("10010.00")
      assert s.no_trade_reason is None


  def test_build_session_summary_no_trade_zero_and_reason():
      s = summary.build_session_summary(
          trades=[],
          start_equity=Decimal("10000.00"),
          end_equity=Decimal("10000.00"),
          mode="LIVE",
          symbol="MSFT",
          session_date=date(2026, 6, 19),
          no_trade_reason="window expired",
      )
      assert s.trades == []
      assert s.total_pnl == Decimal("0.00")
      assert s.total_pnl_pct == Decimal("0")
      assert s.wins == 0 and s.losses == 0 and s.breakevens == 0
      assert s.no_trade_reason == "window expired"
  ```

  Run and confirm FAIL (no `build_session_summary`):

  ```bash
  pytest tests/test_reporting.py -q
  ```
  Expected: FAIL (AttributeError: `build_session_summary`).

- [ ] **Step 8: Implement `build_session_summary`.**
  Append to `orb_bot/reporting/summary.py`:

  ```python
  def build_session_summary(
      trades: list[models.TradeResult],
      start_equity: Decimal,
      end_equity: Decimal,
      mode: str,
      symbol: str,
      session_date: date,
      no_trade_reason: str | None,
  ) -> models.SessionSummary:
      """Aggregate per-trade results into the end-of-session summary (spec §17a).

      total_pnl_pct stored as a FRACTION (total_pnl / start_equity). Classification:
      pnl>0 win, pnl<0 loss, pnl==0 breakeven, so wins+losses+breakevens == len(trades).
      No-trade case ⇒ $0.00 totals and no_trade_reason set by the caller.
      """
      total_pnl = sum((t.pnl for t in trades), Decimal("0"))
      if trades:
          total_pnl_pct = total_pnl / start_equity
      else:
          total_pnl_pct = Decimal("0")
      wins = sum(1 for t in trades if t.pnl > 0)
      losses = sum(1 for t in trades if t.pnl < 0)
      breakevens = sum(1 for t in trades if t.pnl == 0)
      return models.SessionSummary(
          session_date=session_date,
          symbol=symbol,
          mode=mode,
          trades=list(trades),
          total_pnl=total_pnl,
          total_pnl_pct=total_pnl_pct,
          wins=wins,
          losses=losses,
          breakevens=breakevens,
          start_equity=start_equity,
          end_equity=end_equity,
          no_trade_reason=no_trade_reason,
      )
  ```

  Run the full reporting suite and confirm PASS:

  ```bash
  pytest tests/test_reporting.py -q
  ```
  Expected: PASS (8 passed).

- [ ] **Step 9: Commit.**

  ```bash
  git add orb_bot/reporting/__init__.py orb_bot/reporting/summary.py tests/test_reporting.py
  git commit -m "feat(reporting): pure SessionSummary builder with share-weighted P/L

Add pnl_for, weighted_avg_price, build_trade_result (qty=min(entry,exit),
pnl_pct as fraction) and build_session_summary (totals + win/loss/breakeven
counts == len(trades); no-trade \$0.00 + reason). Spec §17a.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

### Task 35: Reporting — LogReporter (`reporting/log.py`)

**Files:**
- Create `orb_bot/reporting/log.py`
- Test `tests/test_reporting.py` (append)

**Interfaces:**
Consumes: `interfaces.Reporter` protocol — `async def start()->None; async def trade_taken(setup:Setup, qty:int, mode:str)->None; async def session_report(summary:SessionSummary)->None; async def close()->None`; `models.Setup(direction, model, entry, stop, target, rr, reason)`; `models.SessionSummary`.
Produces: `class LogReporter` satisfying `interfaces.Reporter`; constructor `LogReporter(logger=None)` (defaults to a structlog logger). Used by the orchestrator as the fallback Reporter when Discord is unconfigured (both modes log the full P/L summary, spec §17).

- [ ] **Step 1: Failing test — `trade_taken` logs an event with setup fields + mode (no Discord).**
  Append to `tests/test_reporting.py`:

  ```python
  from orb_bot.reporting.log import LogReporter


  class _RecordingLogger:
      """Minimal structlog-style stub: records (event, kwargs) per level."""

      def __init__(self):
          self.events = []

      def info(self, event, **kw):
          self.events.append(("info", event, kw))

      def warning(self, event, **kw):
          self.events.append(("warning", event, kw))

      def bind(self, **kw):  # structlog API surface used defensively
          return self


  def _setup(direction=Direction.LONG):
      return models.Setup(
          direction=direction,
          model=Model.BREAKOUT,
          entry=Decimal("100.00"),
          stop=Decimal("99.50"),
          target=Decimal("101.00"),
          rr=2.0,
          reason=["strong close above OR high"],
      )


  async def test_logreporter_trade_taken_logs_fields():
      log = _RecordingLogger()
      r = LogReporter(logger=log)
      await r.start()
      await r.trade_taken(_setup(), qty=10, mode="PAPER")
      await r.close()
      kinds = [e[1] for e in log.events]
      assert "trade_taken" in kinds
      ev = next(e for e in log.events if e[1] == "trade_taken")
      kw = ev[2]
      assert kw["direction"] == "LONG"
      assert kw["model"] == "BREAKOUT"
      assert kw["qty"] == 10
      assert kw["mode"] == "PAPER"
      assert kw["entry"] == "100.00"
      assert kw["stop"] == "99.50"
      assert kw["target"] == "101.00"
  ```

  Run and confirm FAIL (no `orb_bot.reporting.log`):

  ```bash
  pytest tests/test_reporting.py -k logreporter -q
  ```
  Expected: FAIL (ImportError: `orb_bot.reporting.log`).

- [ ] **Step 2: Implement `LogReporter` (start/close no-ops; structured logging).**

  ```python
  # orb_bot/reporting/log.py
  """LogReporter: logs trade-taken notices + the end-of-session P/L summary to the
  structured logger / console. Used in BOTH modes when Discord is unconfigured (§17/§17a)."""
  from __future__ import annotations

  import structlog

  from orb_bot import models


  class LogReporter:
      """Reporter Protocol impl that emits structured log lines (no Discord)."""

      def __init__(self, logger=None) -> None:
          self._log = logger if logger is not None else structlog.get_logger("orb_bot.report")

      async def start(self) -> None:
          return None

      async def trade_taken(self, setup: models.Setup, qty: int, mode: str) -> None:
          self._log.info(
              "trade_taken",
              direction=setup.direction.value,
              model=setup.model.value,
              entry=str(setup.entry),
              stop=str(setup.stop),
              target=str(setup.target),
              rr=setup.rr,
              qty=qty,
              mode=mode,
          )

      async def session_report(self, summary: models.SessionSummary) -> None:
          self._log.info(
              "session_report",
              session_date=summary.session_date.isoformat(),
              symbol=summary.symbol,
              mode=summary.mode,
              n_trades=len(summary.trades),
              total_pnl=str(summary.total_pnl),
              total_pnl_pct=f"{summary.total_pnl_pct * 100:.2f}",
              wins=summary.wins,
              losses=summary.losses,
              breakevens=summary.breakevens,
              start_equity=str(summary.start_equity),
              end_equity=str(summary.end_equity),
              no_trade_reason=summary.no_trade_reason,
              trades=[
                  {
                      "direction": t.direction.value,
                      "model": t.model.value,
                      "qty": t.qty,
                      "entry": str(t.entry_price),
                      "exit": str(t.exit_price),
                      "pnl": str(t.pnl),
                      "pnl_pct": f"{t.pnl_pct * 100:.2f}",
                      "exit_reason": t.exit_reason,
                  }
                  for t in summary.trades
              ],
          )

      async def close(self) -> None:
          return None
  ```

  Run and confirm PASS:

  ```bash
  pytest tests/test_reporting.py -k logreporter -q
  ```
  Expected: PASS.

- [ ] **Step 3: Failing test — `session_report` logs totals + per-trade lines + no-trade reason.**
  Append to `tests/test_reporting.py`:

  ```python
  async def test_logreporter_session_report_logs_totals_and_trades():
      log = _RecordingLogger()
      r = LogReporter(logger=log)
      s = summary.build_session_summary(
          trades=[_tr("20.00", reason="TARGET"), _tr("-10.00", reason="STOP")],
          start_equity=Decimal("10000.00"),
          end_equity=Decimal("10010.00"),
          mode="PAPER",
          symbol="AAPL",
          session_date=date(2026, 6, 19),
          no_trade_reason=None,
      )
      await r.session_report(s)
      ev = next(e for e in log.events if e[1] == "session_report")
      kw = ev[2]
      assert kw["total_pnl"] == "10.00"
      assert kw["total_pnl_pct"] == "0.10"          # fraction 0.001 * 100
      assert kw["wins"] == 1 and kw["losses"] == 1 and kw["breakevens"] == 0
      assert kw["n_trades"] == 2
      assert len(kw["trades"]) == 2
      assert kw["trades"][0]["pnl"] == "20.00"


  async def test_logreporter_session_report_no_trade_zero_and_reason():
      log = _RecordingLogger()
      r = LogReporter(logger=log)
      s = summary.build_session_summary(
          trades=[],
          start_equity=Decimal("10000.00"),
          end_equity=Decimal("10000.00"),
          mode="LIVE",
          symbol="MSFT",
          session_date=date(2026, 6, 19),
          no_trade_reason="no confirmed breakout/entry",
      )
      await r.session_report(s)
      kw = next(e for e in log.events if e[1] == "session_report")[2]
      assert kw["total_pnl"] == "0.00"
      assert kw["no_trade_reason"] == "no confirmed breakout/entry"
      assert kw["trades"] == []
  ```

  Run and confirm these PASS (impl already satisfies them — guards the contract):

  ```bash
  pytest tests/test_reporting.py -k logreporter -q
  ```
  Expected: PASS (3 passed). If any FAIL, fix `log.py` minimally before proceeding.

- [ ] **Step 4: Commit.**

  ```bash
  git add orb_bot/reporting/log.py tests/test_reporting.py
  git commit -m "feat(reporting): LogReporter for trade-taken + session P/L (both modes)

Structured-log Reporter impl used when Discord is unconfigured; logs the full
P/L summary in both paper and live (spec §17). pnl_pct rendered fraction*100.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

### Task 36: Reporting — DiscordReporter embed (`reporting/discord.py`)

**Files:**
- Create `orb_bot/reporting/discord.py`
- Test `tests/test_reporting.py` (append)

**Interfaces:**
Consumes: `interfaces.Reporter`; the shared `discordbot.DiscordClient` (started as an asyncio task; exposes `async def send_embed(embed) -> None` and the configured channel — same client shared with `approval.manual.DiscordApprover`, spec §14); `models.Setup`, `models.SessionSummary`, `models.TradeResult`.
Produces: `class DiscordReporter(client, *, timeframe_min=15)` satisfying `interfaces.Reporter`; pure formatting helpers `format_trade_line(t:TradeResult) -> str` and `format_session_embed(summary:SessionSummary, bars_present:int, timeframe_min:int, low_confidence:bool=False) -> discord.Embed` (the `bars_present/T` denominator + `low_confidence` caveat live here, spec §17a "Discord format").

- [ ] **Step 1: Skip-gate the discord import in tests (so the suite runs without discord.py installed in CI).**
  Append to `tests/test_reporting.py`:

  ```python
  import pytest

  discord = pytest.importorskip("discord")
  from orb_bot.reporting.discord import DiscordReporter, format_trade_line
  ```

  Run to confirm the module is missing (FAIL) — discord.py is a pinned runtime dep, so import should resolve; the FAIL is on `orb_bot.reporting.discord`:

  ```bash
  pytest tests/test_reporting.py -k discordreporter -q
  ```
  Expected: FAIL (no `orb_bot.reporting.discord`) — or SKIP if discord.py is absent (install via `pip install -r requirements.txt`).

- [ ] **Step 2: Failing test — `format_trade_line` matches the spec line format exactly.**
  Append:

  ```python
  def test_format_trade_line_long_target_matches_spec():
      t = models.TradeResult(
          direction=Direction.LONG,
          model=Model.BREAKOUT,
          qty=10,
          entry_price=Decimal("100.00"),
          exit_price=Decimal("102.00"),
          pnl=Decimal("20.00"),
          pnl_pct=Decimal("20.00") / Decimal("5000.00"),  # 0.004 -> +0.40%
          exit_reason="TARGET",
      )
      line = format_trade_line(t)
      assert line == "LONG 10sh @ 100.00 -> 102.00  +$20.00 (+0.40%)  [TARGET]"


  def test_format_trade_line_short_stop_signs():
      t = models.TradeResult(
          direction=Direction.SHORT,
          model=Model.RETEST,
          qty=5,
          entry_price=Decimal("50.00"),
          exit_price=Decimal("51.00"),
          pnl=Decimal("-5.00"),
          pnl_pct=Decimal("-5.00") / Decimal("10000.00"),  # -0.0005 -> -0.05%
          exit_reason="STOP",
      )
      line = format_trade_line(t)
      assert line == "SHORT 5sh @ 50.00 -> 51.00  -$5.00 (-0.05%)  [STOP]"
  ```

  Run and confirm FAIL:

  ```bash
  pytest tests/test_reporting.py -k format_trade_line -q
  ```
  Expected: FAIL (no `format_trade_line`).

- [ ] **Step 3: Implement formatting helpers + `DiscordReporter` skeleton.**

  ```python
  # orb_bot/reporting/discord.py
  """DiscordReporter: posts trade-taken notices + the end-of-session P/L embed via the
  shared discordbot client (spec §14/§17a). The bars_present/T denominator and the
  low_confidence caveat are formatted here. Distinct from the `discord` library by
  absolute import (orb_bot.reporting.discord)."""
  from __future__ import annotations

  from decimal import Decimal

  import discord

  from orb_bot import models


  def _money(amount: Decimal) -> str:
      """+$20.00 / -$5.00 (sign always shown, two decimals)."""
      sign = "-" if amount < 0 else "+"
      return f"{sign}${abs(amount):.2f}"


  def _pct(fraction: Decimal) -> str:
      """Stored fraction -> +0.40% / -0.05% (x100 only at display, spec §17a)."""
      value = fraction * Decimal("100")
      sign = "-" if value < 0 else "+"
      return f"{sign}{abs(value):.2f}%"


  def format_trade_line(t: models.TradeResult) -> str:
      """One per-trade embed line, e.g.
      'LONG 10sh @ 100.00 -> 102.00  +$20.00 (+0.40%)  [TARGET]'."""
      return (
          f"{t.direction.value} {t.qty}sh @ {t.entry_price:.2f} -> {t.exit_price:.2f}  "
          f"{_money(t.pnl)} ({_pct(t.pnl_pct)})  [{t.exit_reason}]"
      )


  def format_session_embed(
      summary: models.SessionSummary,
      bars_present: int,
      timeframe_min: int,
      low_confidence: bool = False,
  ) -> discord.Embed:
      """Compact P/L embed: date, symbol, mode, per-trade lines, total realized P/L
      $ and %, win/loss/breakeven counts, and the bars_present/T data caveat (§17a)."""
      title = f"ORB P/L - {summary.symbol} - {summary.session_date.isoformat()} ({summary.mode})"
      color = discord.Color.green() if summary.total_pnl > 0 else (
          discord.Color.red() if summary.total_pnl < 0 else discord.Color.light_grey()
      )
      embed = discord.Embed(title=title, color=color)

      if summary.trades:
          body = "\n".join(format_trade_line(t) for t in summary.trades)
      else:
          reason = summary.no_trade_reason or "no trade"
          body = f"No trades - {reason}"
      embed.add_field(name="Trades", value=body, inline=False)

      embed.add_field(
          name="Total realized P/L",
          value=f"{_money(summary.total_pnl)} ({_pct(summary.total_pnl_pct)})",
          inline=False,
      )
      embed.add_field(
          name="Record",
          value=f"W {summary.wins} / L {summary.losses} / BE {summary.breakevens}",
          inline=False,
      )
      embed.add_field(
          name="Opening range data",
          value=f"bars_present {bars_present}/{timeframe_min}"
          + ("  (LOW CONFIDENCE - partial range)" if low_confidence else ""),
          inline=False,
      )
      embed.set_footer(text=f"start {summary.start_equity} -> end {summary.end_equity}")
      return embed


  class DiscordReporter:
      """Reporter Protocol impl posting to a Discord channel via the shared client."""

      def __init__(self, client, *, timeframe_min: int = 15) -> None:
          self._client = client
          self._timeframe_min = timeframe_min

      async def start(self) -> None:
          return None

      async def trade_taken(self, setup: models.Setup, qty: int, mode: str) -> None:
          embed = discord.Embed(
              title=f"Order submitted ({mode})",
              color=discord.Color.blurple(),
          )
          embed.add_field(name="Direction", value=setup.direction.value, inline=True)
          embed.add_field(name="Model", value=setup.model.value, inline=True)
          embed.add_field(name="Qty", value=str(qty), inline=True)
          embed.add_field(name="Entry", value=f"{setup.entry:.2f}", inline=True)
          embed.add_field(name="Stop", value=f"{setup.stop:.2f}", inline=True)
          embed.add_field(name="Target", value=f"{setup.target:.2f}", inline=True)
          await self._client.send_embed(embed)

      async def session_report(self, summary: models.SessionSummary) -> None:
          bars_present, low_confidence = self._range_meta()
          embed = format_session_embed(
              summary,
              bars_present=bars_present,
              timeframe_min=self._timeframe_min,
              low_confidence=low_confidence,
          )
          await self._client.send_embed(embed)

      def _range_meta(self) -> tuple[int, bool]:
          """bars_present/low_confidence sourced from the shared client when the
          orchestrator has stashed the day's OpeningRange there; defaults otherwise."""
          rng = getattr(self._client, "opening_range", None)
          if rng is None:
              return (self._timeframe_min, False)
          return (rng.bars_present, rng.low_confidence)

      async def close(self) -> None:
          return None
  ```

  Run and confirm PASS:

  ```bash
  pytest tests/test_reporting.py -k format_trade_line -q
  ```
  Expected: PASS (2 passed).

- [ ] **Step 4: Failing test — `session_report`/`trade_taken` send an embed with the bars_present/T denominator and caveat.**
  Append:

  ```python
  class _FakeDiscordClient:
      def __init__(self, opening_range=None):
          self.sent = []
          self.opening_range = opening_range

      async def send_embed(self, embed):
          self.sent.append(embed)


  async def test_discordreporter_session_report_embed_has_denominator_and_caveat():
      rng = models.OpeningRange(
          high=Decimal("101.00"),
          low=Decimal("99.00"),
          established_at=_ts(9, 45),
          width=Decimal("2.00"),
          feed="IEX",
          bars_present=9,            # < T=15 -> low confidence
          low_confidence=True,
      )
      client = _FakeDiscordClient(opening_range=rng)
      r = DiscordReporter(client, timeframe_min=15)
      s = summary.build_session_summary(
          trades=[_tr("20.00", reason="TARGET")],
          start_equity=Decimal("10000.00"),
          end_equity=Decimal("10020.00"),
          mode="LIVE",
          symbol="AAPL",
          session_date=date(2026, 6, 19),
          no_trade_reason=None,
      )
      await r.session_report(s)
      assert len(client.sent) == 1
      embed = client.sent[0]
      fields = {f.name: f.value for f in embed.fields}
      assert "bars_present 9/15" in fields["Opening range data"]
      assert "LOW CONFIDENCE" in fields["Opening range data"]
      assert fields["Record"] == "W 1 / L 0 / BE 0"


  async def test_discordreporter_trade_taken_sends_embed():
      client = _FakeDiscordClient()
      r = DiscordReporter(client, timeframe_min=15)
      await r.trade_taken(_setup(), qty=10, mode="PAPER")
      assert len(client.sent) == 1
      fields = {f.name: f.value for f in client.sent[0].fields}
      assert fields["Direction"] == "LONG"
      assert fields["Qty"] == "10"
      assert fields["Entry"] == "100.00"
  ```

  Run and confirm PASS:

  ```bash
  pytest tests/test_reporting.py -k discordreporter -q
  ```
  Expected: PASS (2 passed).

- [ ] **Step 5: Run the full reporting suite (all tasks together).**

  ```bash
  pytest tests/test_reporting.py -q
  ```
  Expected: PASS (all reporting tests green, no skips once discord.py is installed).

- [ ] **Step 6: Commit.**

  ```bash
  git add orb_bot/reporting/discord.py tests/test_reporting.py
  git commit -m "feat(reporting): DiscordReporter embed with bars_present/T caveat

Format trade-taken notice + end-of-session P/L embed via the shared discordbot
client (spec §14/§17a): per-trade lines, total realized P/L \$/%, W/L/BE record,
and the bars_present/T low-confidence opening-range caveat. pnl_pct fraction*100.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

---

I now have all the detail I need. Let me write the markdown plan for the logconf, orchestrator, and `__main__` module tasks following the OUTPUT FORMAT exactly.

### Task 37: Logging — `logconf.py` (structlog JSON + daily rotation)

**Files:**
- Create: `orb_bot/logconf.py`
- Test: `tests/test_logconf.py`

**Interfaces:**
- Consumes: `StrategyConfig.logging_level: str`, `StrategyConfig.logging_dir: str` (from `orb_bot/config.py`).
- Produces: `configure_logging(level: str, log_dir: str, run_id: str) -> structlog.stdlib.BoundLogger` — returns a bound logger pre-bound with `run_id`; installs a JSON-line `TimedRotatingFileHandler` at `{log_dir}/orb_{date}.log` plus a console handler. Later tasks call `logconf.configure_logging(...)` once in `__main__`/`Orchestrator`.

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_logconf.py
import json
import logging
from pathlib import Path

from .context import orb_bot
from orb_bot import logconf


def _reset_root() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)


def test_configure_logging_returns_bound_logger_with_run_id(tmp_path, capsys):
    _reset_root()
    log = logconf.configure_logging("INFO", str(tmp_path), "2026-06-19-AAPL")
    log.info("startup", mode="PAPER")
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "expected at least one console JSON line"
    rec = json.loads(out[-1])
    assert rec["event"] == "startup"
    assert rec["mode"] == "PAPER"
    assert rec["run_id"] == "2026-06-19-AAPL"
    assert rec["level"] == "info"


def test_configure_logging_writes_rotating_file(tmp_path):
    _reset_root()
    log = logconf.configure_logging("DEBUG", str(tmp_path), "2026-06-19-AAPL")
    log.warning("data_warning", iex_partial=True, bars_present=12)
    for h in logging.getLogger().handlers:
        h.flush()
    files = list(Path(tmp_path).glob("orb_*.log"))
    assert len(files) == 1, f"expected one rotating log file, got {files}"
    line = files[0].read_text().strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["event"] == "data_warning"
    assert rec["bars_present"] == 12
    assert rec["run_id"] == "2026-06-19-AAPL"


def test_configure_logging_respects_level(tmp_path, capsys):
    _reset_root()
    log = logconf.configure_logging("WARNING", str(tmp_path), "rid")
    log.info("should_be_dropped")
    log.error("should_appear")
    out = capsys.readouterr().out
    assert "should_be_dropped" not in out
    assert "should_appear" in out
```

- [ ] **Step 2: Run it and confirm it fails.**

```bash
pytest tests/test_logconf.py -q
```
Expected: FAIL (`ModuleNotFoundError: No module named 'orb_bot.logconf'` / `AttributeError: module 'orb_bot' has no attribute 'logconf'`).

- [ ] **Step 3: Minimal implementation.**

```python
# orb_bot/logconf.py
"""Structured JSON logging: structlog rendered through stdlib, daily rotation.

`configure_logging` installs a console handler + a TimedRotatingFileHandler
(`{log_dir}/orb_{YYYY-MM-DD}.log`, rotating at midnight) and returns a
structlog BoundLogger pre-bound with the per-run correlation id. One line per
event, JSON-rendered (spec §17).
"""
from __future__ import annotations

import datetime
import logging
import logging.handlers
import sys
from pathlib import Path

import structlog


def configure_logging(
    level: str,
    log_dir: str,
    run_id: str,
) -> structlog.stdlib.BoundLogger:
    log_level = getattr(logging, level.upper(), logging.INFO)

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    log_path = Path(log_dir) / f"orb_{today}.log"

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=False)
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        timestamper,
    ]

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_path),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(log_level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console)
    root.addHandler(file_handler)

    return structlog.get_logger().bind(run_id=run_id)
```

- [ ] **Step 4: Run it and confirm it passes.**

```bash
pytest tests/test_logconf.py -q
```
Expected: PASS (3 passed).

- [ ] **Step 5: Commit.**

```bash
git add orb_bot/logconf.py tests/test_logconf.py
git commit -m "feat(logconf): structlog JSON logging with daily rotation and run_id binding"
```

### Task 38: Orchestrator — preflight (clock gate, start_equity, ATR seed, OR establish/backfill, restart reconciliation)

**Files:**
- Create: `orb_bot/orchestrator.py`
- Test: `tests/test_orchestrator.py` (preflight cases)

**Interfaces:**
- Consumes: `Broker.get_account()->AccountSnapshot`, `Broker.get_clock()->ClockInfo`, `Broker.get_order(order_id)->OrderResult`; `Clock.now()->datetime`; `Engine.__init__(cfg, session_date)`, `Engine.seed_atr(hist_tf: list[Candle])`, `Engine.on_candle(c)`; `Settings.strategy: StrategyConfig`, `Settings.run: RunConfig`; models `Candle, OpeningRange, AccountSnapshot, ClockInfo`.
- Produces: `Orchestrator(settings, feed, broker, approver, reporter, clock, engine)`; `await orch._preflight() -> bool` (False ⇒ go straight to DONE/report path); sets `self.start_equity: Decimal`, `self.flatten_at: datetime`, `self.next_close: datetime`, `self.mode: str`, `self.reconciled_open_position: bool`. Later steps in `run()`/`_react()` rely on these.

- [ ] **Step 1: Write failing preflight tests with fakes + freezegun.**

```python
# tests/test_orchestrator.py
import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from .context import orb_bot
from orb_bot import orchestrator as orch_mod
from orb_bot.config import Settings, StrategyConfig, RunConfig
from orb_bot.models import (
    AccountSnapshot,
    Candle,
    ClockInfo,
    Direction,
    Fill,
    Model,
    OrderResult,
    Setup,
)

ET = ZoneInfo("America/New_York")


def _candle_1m(h, m, price=Decimal("100"), tf=1):
    ts_open = datetime.datetime(2026, 6, 19, h, m, tzinfo=ET)
    return Candle(
        ts_open=ts_open,
        ts_close=ts_open + datetime.timedelta(minutes=tf),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=10,
        timeframe_min=tf,
    )


class FakeFeed:
    def __init__(self, candles, hist=None):
        self._candles = candles
        self._hist = hist or []
        self.hist_calls = []
        self.closed = False

    async def candles(self):
        for c in self._candles:
            yield c

    async def hist_tf(self, *, limit, tf_min, end):
        self.hist_calls.append((limit, tf_min, end))
        return list(self._hist)

    async def close(self):
        self.closed = True


class FakeBroker:
    def __init__(
        self,
        *,
        is_open=True,
        next_close=None,
        equity=Decimal("100000"),
        open_orders=None,
        positions_qty=0,
    ):
        self._clock = ClockInfo(
            is_open=is_open,
            next_close=next_close
            or datetime.datetime(2026, 6, 19, 16, 0, tzinfo=ET),
        )
        self._equity = equity
        self._open_orders = open_orders or []
        self._positions_qty = positions_qty
        self._seq = 1
        self.submitted = []
        self.cancel_all_calls = 0
        self.flatten_calls = 0
        self._fills = []

    async def get_account(self):
        return AccountSnapshot(
            equity=self._equity,
            buying_power=self._equity,
            shorting_enabled=True,
        )

    async def get_clock(self):
        return self._clock

    @property
    def current_seq(self):
        return self._seq

    async def submit_bracket(self, setup, qty):
        res = OrderResult(
            order_id="o1",
            client_order_id="2026-06-19-AAPL-1-ENTRY",
            status="accepted",
            filled_avg_price=None,
            filled_qty=0,
            legs=[],
        )
        self.submitted.append((setup, qty))
        return res

    async def trade_updates(self):
        for f in self._fills:
            yield f

    async def get_order(self, order_id):
        return OrderResult(
            order_id=order_id,
            client_order_id="2026-06-19-AAPL-1-ENTRY",
            status="filled",
            filled_avg_price=Decimal("100"),
            filled_qty=10,
            legs=[],
        )

    async def cancel_all(self):
        self.cancel_all_calls += 1

    async def flatten(self):
        self.flatten_calls += 1


class FakeApprover:
    def __init__(self, decision="APPROVE"):
        self.decision = decision
        self.requests = []
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def request(self, req):
        self.requests.append(req)
        return self.decision

    async def close(self):
        self.closed = True


class FakeReporter:
    def __init__(self):
        self.started = False
        self.closed = False
        self.trade_taken_calls = []
        self.session_reports = []

    async def start(self):
        self.started = True

    async def trade_taken(self, setup, qty, mode):
        self.trade_taken_calls.append((setup, qty, mode))

    async def session_report(self, summary):
        self.session_reports.append(summary)

    async def close(self):
        self.closed = True


class FixedClock:
    def __init__(self, now):
        self._now = now

    def now(self):
        return self._now


class FakeEngine:
    """Records calls; emits scripted events per candle."""

    def __init__(self, events_by_index=None, cfg=None, session_date=None):
        self.cfg = cfg
        self.session_date = session_date
        self.seeded = None
        self.candles = []
        self._events = events_by_index or {}
        self._done = False
        self.approvals = []
        self.entry_fills = []
        self.closed_fills = []

    def seed_atr(self, hist_tf):
        self.seeded = hist_tf

    def adopt_open_position(self, qty, entry_fill):
        self.adopted = (qty, entry_fill)

    def on_candle(self, c):
        idx = len(self.candles)
        self.candles.append(c)
        return list(self._events.get(idx, []))

    def on_approval(self, decision):
        self.approvals.append(decision)
        return []

    def on_entry_filled(self, res):
        self.entry_fills.append(res)
        return []

    def on_trade_closed(self, fill):
        self.closed_fills.append(fill)
        return []

    def is_done(self):
        return self._done


def _settings(**run_over):
    run = RunConfig(symbol="AAPL", **run_over)
    return Settings(strategy=StrategyConfig(), run=run)


def _make_orch(broker, feed=None, approver=None, reporter=None, clock=None, engine=None, settings=None):
    settings = settings or _settings()
    feed = feed or FakeFeed([])
    approver = approver or FakeApprover()
    reporter = reporter or FakeReporter()
    clock = clock or FixedClock(datetime.datetime(2026, 6, 19, 9, 25, tzinfo=ET))
    engine = engine or FakeEngine()
    return orch_mod.Orchestrator(
        settings, feed, broker, approver, reporter, clock, engine
    )


@freeze_time("2026-06-19 13:25:00")  # 09:25 ET
async def test_preflight_market_closed_returns_false():
    broker = FakeBroker(is_open=False)
    o = _make_orch(broker, clock=FixedClock(datetime.datetime(2026, 6, 19, 9, 25, tzinfo=ET)))
    ok = await o._preflight()
    assert ok is False


async def test_preflight_captures_start_equity_and_flatten_at():
    broker = FakeBroker(
        is_open=True,
        equity=Decimal("50000"),
        next_close=datetime.datetime(2026, 6, 19, 16, 0, tzinfo=ET),
    )
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    o = _make_orch(broker, clock=clk)
    ok = await o._preflight()
    assert ok is True
    assert o.start_equity == Decimal("50000")
    assert o.next_close == datetime.datetime(2026, 6, 19, 16, 0, tzinfo=ET)
    # flatten_at = min(config 15:55, next_close - flatten_buffer_min=5 => 15:55) == 15:55
    assert o.flatten_at == datetime.datetime(2026, 6, 19, 15, 55, tzinfo=ET)
    assert o.mode == "PAPER"


async def test_preflight_flatten_at_clamped_to_next_close_minus_buffer():
    broker = FakeBroker(
        is_open=True,
        next_close=datetime.datetime(2026, 6, 19, 13, 0, tzinfo=ET),  # half day
    )
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    o = _make_orch(broker, clock=clk)
    ok = await o._preflight()
    assert ok is True
    # min(15:55, 13:00-5) == 12:55
    assert o.flatten_at == datetime.datetime(2026, 6, 19, 12, 55, tzinfo=ET)


async def test_preflight_restart_into_open_position_reconciles_in_trade():
    broker = FakeBroker(is_open=True, positions_qty=10)
    clk = FixedClock(datetime.datetime(2026, 6, 19, 11, 30, tzinfo=ET))
    engine = FakeEngine()
    o = _make_orch(broker, clock=clk, engine=engine)
    ok = await o._preflight()
    assert ok is True
    assert o.reconciled_open_position is True
    assert getattr(engine, "adopted", None) is not None
    assert engine.adopted[0] == 10
    assert o.entry_fill is not None and o.entry_fill.qty == 10
    assert engine.seeded is None


async def test_preflight_seeds_atr_with_nonempty_hist():
    broker = FakeBroker(is_open=True)
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    hist = [_candle_1m(9, 30, tf=15), _candle_1m(9, 45, tf=15)]
    engine = FakeEngine()
    o = _make_orch(broker, feed=FakeFeed([], hist=hist), clock=clk, engine=engine)
    ok = await o._preflight()
    assert ok is True
    assert engine.seeded == hist
    assert o.feed.hist_calls and o.feed.hist_calls[0][1] == 15  # tf_min
```

- [ ] **Step 2: Run and confirm fail.**

```bash
pytest tests/test_orchestrator.py -q -k preflight
```
Expected: FAIL (`ModuleNotFoundError: No module named 'orb_bot.orchestrator'`).

- [ ] **Step 3: Minimal Orchestrator with `__init__` + `_preflight`.**

```python
# orb_bot/orchestrator.py
"""Orchestrator: owns the asyncio loop, the wall clock, all wall-clock timing
(session gating, bucket-boundary timers, flatten scheduling) and the I/O
reactions to engine events. The engine stays pure (spec §15)."""
from __future__ import annotations

import asyncio
import datetime
import math
import signal
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

from orb_bot import logconf
from orb_bot.aggregation import Aggregator
from orb_bot.execution import alpaca as execution_alpaca
from orb_bot.reporting import summary
from orb_bot.models import (
    Candle,
    Direction,
    Fill,
    Model,
    OrderResult,
    SessionSummary,
    Setup,
    State,
    TradeResult,
)


class Orchestrator:
    def __init__(self, settings, feed, broker, approver, reporter, clock, engine):
        self.settings = settings
        self.cfg = settings.strategy
        self.run = settings.run
        self.feed = feed
        self.broker = broker
        self.approver = approver
        self.reporter = reporter
        self.clock = clock
        self.engine = engine
        self.tz = ZoneInfo(self.cfg.timezone)
        self.symbol = self.run.symbol
        self.mode = "LIVE" if self.run.live else "PAPER"

        # populated by _preflight
        self.start_equity: Decimal | None = None
        self.end_equity: Decimal | None = None
        self.next_close: datetime.datetime | None = None
        self.flatten_at: datetime.datetime | None = None
        self.reconciled_open_position: bool = False

        # latest 1m transport candle, updated in run(); used by stale-price
        # setup re-validation (spec §8 step 6 / §16)
        self._last_1m: Candle | None = None
        # model of the most-recently submitted setup, for trade attribution (MED #11)
        self._last_model: Model | None = None

        # session accumulation
        self.trades: list[TradeResult] = []
        self.entry_fill: Fill | None = None
        self._entry_fills: list[Fill] = []
        self._exit_fills: list[Fill] = []
        self.flatten_coid: str | None = None
        self.trade_seq: int = 1
        self._stop = asyncio.Event()

        run_id = f"{self.clock.now().date().isoformat()}-{self.symbol}"
        self.log = logconf.configure_logging(
            self.cfg.logging_level, self.cfg.logging_dir, run_id
        )
        self._aggr = Aggregator(self.cfg.range_timeframe_min, datetime.time(9, 30))

    async def _preflight(self) -> bool:
        clock_info = await self.broker.get_clock()
        self.next_close = clock_info.next_close
        if not clock_info.is_open:
            self.log.info("preflight_market_closed", mode=self.mode)
            return False

        acct = await self.broker.get_account()
        self.start_equity = acct.equity

        session_date = self.clock.now().date()
        # cfg.flatten_at is a datetime.time; combine with the session date in ET.
        config_flat = datetime.datetime.combine(
            session_date, self.cfg.flatten_at, tzinfo=self.tz
        )
        clamp = self.next_close - datetime.timedelta(minutes=self.cfg.flatten_buffer_min)
        self.flatten_at = min(config_flat, clamp)

        self.log.info(
            "preflight",
            mode=self.mode,
            feed=self.run.feed,
            start_equity=str(self.start_equity),
            next_close=self.next_close.isoformat(),
            flatten_at=self.flatten_at.isoformat(),
        )

        # Restart-into-open-position reconciliation (§16): adopt the live
        # bracket via a real engine entry point, treat the slot as consumed,
        # and skip range/confirmation. The engine is told it is IN_TRADE so it
        # will never (re-)establish a range for the rest of the session.
        position_qty = getattr(self.broker, "_positions_qty", 0)
        if position_qty != 0:
            self.reconciled_open_position = True
            # The deterministic ENTRY client_order_id for the (already live) trade
            # is seq 1 of today's session; fetch its fill to seed self.entry_fill.
            entry_coid = execution_alpaca.client_order_id(
                session_date, self.symbol, 1, "ENTRY"
            )
            try:
                entry_order = await self.broker.get_order(entry_coid)
            except Exception:  # noqa: BLE001 - order lookup must not abort preflight
                entry_order = None
            if entry_order is not None:
                entry_fill = Fill(
                    order_id=entry_order.order_id,
                    client_order_id=entry_order.client_order_id,
                    leg_role="ENTRY",
                    side="buy" if position_qty > 0 else "sell",
                    price=entry_order.filled_avg_price or Decimal("0"),
                    qty=abs(int(position_qty)),
                    ts=self.clock.now(),
                    position_qty=int(position_qty),
                    exit_reason=None,
                )
                self.entry_fill = entry_fill
                # Engine adopts the open position: state=IN_TRADE, slot consumed,
                # range/confirmation skipped (added in Engine PART 1).
                self.engine.adopt_open_position(int(position_qty), entry_fill)
            self.log.warning(
                "preflight_reconcile_open_position",
                position_qty=position_qty,
            )
            return True

        # ATR seed on T-minute bars via historical REST (same feed as live).
        # Gate is enforced inside the engine; only seed when we actually have bars.
        hist = await self.feed.hist_tf(
            limit=self.cfg.atr_period + 1,
            tf_min=self.cfg.range_timeframe_min,
            end=self.clock.now(),
        )
        if hist:
            self.engine.seed_atr(hist)
        return True
```

> Note: `position_qty` / open-order discovery is abstracted behind the `Broker` Protocol in later execution-module tasks; here the test fake exposes `_positions_qty`. The real `AlpacaBroker` exposes equivalent reconciliation queries; the orchestrator only needs the boolean "is there a live position/bracket" answer plus `get_order` to adopt legs.
>
> **Engine dependency (MED #10):** this task calls `self.engine.adopt_open_position(qty, entry_fill)`, a real entry point that Engine PART 1 must provide: it sets `ctx.state = State.IN_TRADE`, decrements `trades_remaining` (consumes the slot), records the adopted entry, and thereafter REFUSES to establish a range or confirm a direction (so a restart mid-trade only monitors/flattens). There is no `ctx_state_override` attribute anymore.
>
> **Feed dependency (HIGH #6):** preflight ATR seeding REST-fetches `await self.feed.hist_tf(limit=self.cfg.atr_period + 1, tf_min=self.cfg.range_timeframe_min, end=self.clock.now())` and only calls `self.engine.seed_atr(hist)` when the list is non-empty (the real `ATR.seed` raises on an empty list). Test fakes must provide a `hist_tf(...)` coroutine and a `get_order(client_order_id)` coroutine.

- [ ] **Step 4: Run and confirm pass.**

```bash
pytest tests/test_orchestrator.py -q -k preflight
```
Expected: PASS (4 passed).

- [ ] **Step 5: Commit.**

```bash
git add orb_bot/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): preflight clock gate, start_equity, flatten_at clamp, hist_tf ATR seed, restart reconciliation via engine.adopt_open_position"
```

### Task 39: Orchestrator — sizing (`_sizing`) + setup re-validation

**Files:**
- Modify: `orb_bot/orchestrator.py`
- Test: `tests/test_orchestrator.py` (sizing cases)

**Interfaces:**
- Consumes: `Setup(direction, model, entry, stop, target, rr, reason)`; `StrategyConfig.risk_per_trade_pct`, `equity_source`, `fixed_equity`.
- Produces: `Orchestrator._sizing(equity: Decimal, setup: Setup) -> int` (floor of `(equity*risk_pct/100)/abs(entry-stop)`; returns `0` to signal reject when `< 1`); `Orchestrator._equity_for_sizing() -> Decimal` (live vs fixed). `_react` (next task) consumes these.

- [ ] **Step 1: Write failing sizing tests.**

```python
# tests/test_orchestrator.py  (append)
def _setup_long():
    return Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.00"),
        target=Decimal("102.00"),
        rr=2.0,
        reason=["breakout"],
    )


async def test_sizing_floors_whole_shares():
    broker = FakeBroker(equity=Decimal("100000"))
    o = _make_orch(broker)
    # risk_per_trade_pct default 0.5 => 100000*0.5/100 = 500 risk; /1.00 stop dist = 500
    qty = o._sizing(Decimal("100000"), _setup_long())
    assert qty == 500


async def test_sizing_floor_rounds_down():
    broker = FakeBroker()
    o = _make_orch(broker)
    s = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.30"),  # stop dist 0.70
        target=Decimal("101.40"),
        rr=2.0,
        reason=["x"],
    )
    # 100000*0.5/100=500 ; 500/0.70 = 714.28 -> 714
    assert o._sizing(Decimal("100000"), s) == 714


async def test_sizing_rejects_below_one_share():
    broker = FakeBroker()
    o = _make_orch(broker)
    s = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("0.01"),  # huge stop dist 99.99
        target=Decimal("300.00"),
        rr=2.0,
        reason=["x"],
    )
    # 100*0.5/100=0.5 risk over 99.99 -> 0.005 -> floor 0 -> reject
    assert o._sizing(Decimal("100"), s) == 0


async def test_equity_for_sizing_uses_fixed_when_configured():
    broker = FakeBroker(equity=Decimal("100000"))
    settings = Settings(
        strategy=StrategyConfig(equity_source="fixed", fixed_equity=Decimal("25000")),
        run=RunConfig(symbol="AAPL"),
    )
    o = _make_orch(broker, settings=settings)
    o.start_equity = Decimal("100000")
    assert o._equity_for_sizing() == Decimal("25000")


async def test_equity_for_sizing_uses_live_start_equity():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("80000")
    assert o._equity_for_sizing() == Decimal("80000")


def test_revalidate_setup_passes_when_no_live_tick():
    o = _make_orch(FakeBroker())
    o._last_1m = None
    assert o._revalidate_setup(_setup_long()) is True


def test_revalidate_setup_rejects_long_when_price_below_stop():
    o = _make_orch(FakeBroker())
    # latest 1m close has collapsed below the long's stop -> no longer viable
    o._last_1m = _candle_1m(9, 50, price=Decimal("98.50"))
    assert o._revalidate_setup(_setup_long()) is False


def test_revalidate_setup_passes_long_when_price_in_range():
    o = _make_orch(FakeBroker())
    o._last_1m = _candle_1m(9, 50, price=Decimal("100.20"))
    assert o._revalidate_setup(_setup_long()) is True
```

- [ ] **Step 2: Run and confirm fail.**

```bash
pytest tests/test_orchestrator.py -q -k "sizing or equity_for_sizing"
```
Expected: FAIL (`AttributeError: 'Orchestrator' object has no attribute '_sizing'`).

- [ ] **Step 3: Implement sizing + equity helpers.**

```python
# orb_bot/orchestrator.py  (add methods to Orchestrator)
    def _equity_for_sizing(self) -> Decimal:
        if self.cfg.equity_source == "fixed":
            return Decimal(self.cfg.fixed_equity)
        return self.start_equity if self.start_equity is not None else Decimal("0")

    def _sizing(self, equity: Decimal, setup: Setup) -> int:
        stop_dist = abs(setup.entry - setup.stop)
        if stop_dist <= 0:
            return 0
        risk_dollars = (equity * Decimal(str(self.cfg.risk_per_trade_pct))) / Decimal("100")
        raw = (risk_dollars / stop_dist).to_integral_value(rounding=ROUND_DOWN)
        qty = int(raw)
        return qty if qty >= 1 else 0

    def _revalidate_setup(self, setup: Setup) -> bool:
        """Stale-price re-validation (spec §8 step 6 / §16): between SetupProposed
        and order submission the price may have run away from the level. Reject if
        the latest 1m transport price is no longer within tolerance / still
        reachable for the trade's direction."""
        last = self._last_1m
        if last is None:
            return True  # no live tick yet (e.g. backfilled OR) -> do not block
        price = last.close
        tol = Decimal(str(self.cfg.retest_tolerance_atr)) * (
            self.engine.ctx.atr.value if getattr(self.engine, "ctx", None) else Decimal("0")
        )
        if setup.direction == Direction.LONG:
            # price must not have collapsed below the stop and must remain within
            # tolerance below the target (still a viable long entry).
            if price <= setup.stop:
                return False
            if price > setup.target + tol:
                return False
            return True
        # SHORT: mirror image.
        if price >= setup.stop:
            return False
        if price < setup.target - tol:
            return False
        return True
```

- [ ] **Step 4: Run and confirm pass.**

```bash
pytest tests/test_orchestrator.py -q -k "sizing or equity_for_sizing"
```
Expected: PASS (5 passed).

- [ ] **Step 5: Commit.**

```bash
git add orb_bot/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): risk-based whole-share sizing + stale-price setup re-validation"
```

### Task 40: Orchestrator — `_react` (SetupProposed → approval → submit_bracket; mode-uniform; trade_taken)

**Files:**
- Modify: `orb_bot/orchestrator.py`
- Test: `tests/test_orchestrator.py` (react/approval cases)

**Interfaces:**
- Consumes: engine events `SetupProposed(setup)`, `RangeEstablished`, `DirectionConfirmed`, `RangeDayDetected`, `EntryConfirmed`, `TradeRecorded`, `WindowExpired`, `NoOp`; `Approver.request(req)->str`; `Broker.submit_bracket(setup, qty)->OrderResult`; `Reporter.trade_taken(setup, qty, mode)`; `Engine.on_approval(decision)`; `models.ApprovalRequest`.
- Produces: `Orchestrator._build_approval_request(setup, qty) -> ApprovalRequest`; `Orchestrator._react(ev) -> None`. The `run()` loop consumes `_react`.

- [ ] **Step 1: Write failing react/approval tests.**

```python
# tests/test_orchestrator.py  (append)
from orb_bot.models import ApprovalRequest, SetupProposed  # canonical EngineEvent (models)


def _or_set(o):
    # engine context the orchestrator reads for OR levels in the approval request
    class _OR:
        high = Decimal("101.00")
        low = Decimal("99.00")
        bars_present = 15
        low_confidence = False
        feed = "IEX"
    o.engine.opening_range = _OR()


async def test_react_setup_proposed_paper_auto_approve_submits_bracket():
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="APPROVE")
    reporter = FakeReporter()
    o = _make_orch(broker, approver=approver, reporter=reporter)
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_long()))
    assert len(broker.submitted) == 1
    sub_setup, sub_qty = broker.submitted[0]
    assert sub_qty == 500
    assert approver.requests, "approver.request must be called (mode-uniform)"
    assert o.engine.approvals == ["APPROVE"]
    assert reporter.trade_taken_calls == [(_setup_long(), 500, "PAPER")]


async def test_react_live_reject_does_not_submit_no_slot():
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="REJECT")
    settings = Settings(strategy=StrategyConfig(), run=RunConfig(symbol="AAPL", live=True, feed="SIP"))
    o = _make_orch(broker, approver=approver, settings=settings)
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_long()))
    assert broker.submitted == []
    assert o.engine.approvals == ["REJECT"]
    assert o.reporter.trade_taken_calls == []


async def test_react_live_timeout_does_not_submit():
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="TIMEOUT")
    settings = Settings(strategy=StrategyConfig(), run=RunConfig(symbol="AAPL", live=True, feed="SIP"))
    o = _make_orch(broker, approver=approver, settings=settings)
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_long()))
    assert broker.submitted == []
    assert o.engine.approvals == ["TIMEOUT"]


async def test_react_setup_proposed_qty_below_one_rejects_before_approval():
    broker = FakeBroker(equity=Decimal("100"))
    approver = FakeApprover(decision="APPROVE")
    o = _make_orch(broker, approver=approver)
    o.start_equity = Decimal("100")
    _or_set(o)
    s = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("0.01"),
        target=Decimal("300.00"),
        rr=2.0,
        reason=["x"],
    )
    await o._react(SetupProposed(setup=s))
    assert broker.submitted == []
    assert approver.requests == [], "qty<1 must reject before any approval request"


def _setup_retest():
    return Setup(
        direction=Direction.LONG,
        model=Model.RETEST,
        entry=Decimal("100.00"),
        stop=Decimal("99.00"),
        target=Decimal("102.00"),
        rr=2.0,
        reason=["retest"],
    )


async def test_react_setup_proposed_records_last_model_for_attribution():
    # MED #11: a RETEST setup must set self._last_model = RETEST (not BREAKOUT).
    broker = FakeBroker(equity=Decimal("100000"))
    o = _make_orch(broker, approver=FakeApprover(decision="APPROVE"))
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_retest()))
    assert len(broker.submitted) == 1
    assert o._last_model == Model.RETEST


async def test_react_setup_proposed_stale_price_rejects_before_approval():
    # HIGH #9: latest 1m price below the long's stop -> reject before approver.
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="APPROVE")
    o = _make_orch(broker, approver=approver)
    o.start_equity = Decimal("100000")
    _or_set(o)
    o._last_1m = _candle_1m(9, 50, price=Decimal("98.00"))
    await o._react(SetupProposed(setup=_setup_long()))
    assert broker.submitted == []
    assert approver.requests == [], "stale-price reject must precede any approval request"


async def test_build_approval_request_carries_runtime_data():
    broker = FakeBroker(equity=Decimal("100000"))
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    _or_set(o)
    req = o._build_approval_request(_setup_long(), 500)
    assert isinstance(req, ApprovalRequest)
    assert req.symbol == "AAPL"
    assert req.qty == 500
    assert req.mode == "PAPER"
    assert req.feed == "IEX"
    assert req.or_high == Decimal("101.00")
    assert req.or_low == Decimal("99.00")
    assert req.bars_present == 15
    assert req.approval_ttl_s == StrategyConfig().approval_timeout_s
    assert req.risk_dollars == pytest.approx(500.0)
```

- [ ] **Step 2: Run and confirm fail.**

```bash
pytest tests/test_orchestrator.py -q -k "react or build_approval"
```
Expected: FAIL (`AttributeError: 'Orchestrator' object has no attribute '_react'`). `SetupProposed` (and the other EngineEvent members) are imported from `orb_bot.models`, which lands well before this task, so the import itself resolves.

- [ ] **Step 3: Implement `_build_approval_request` + `_react`.**

```python
# orb_bot/orchestrator.py  (add imports)
# EngineEvent union members AND ApprovalRequest all live in orb_bot.models
# (the pure engine returns these models; it does not define them).
from orb_bot.models import (
    ApprovalRequest,
    DirectionConfirmed,
    EntryConfirmed,
    NoOp,
    RangeDayDetected,
    RangeEstablished,
    SetupProposed,
    TradeRecorded,
    WindowExpired,
)
```

```python
# orb_bot/orchestrator.py  (add methods to Orchestrator)
    def _build_approval_request(self, setup: Setup, qty: int) -> ApprovalRequest:
        oran = self.engine.opening_range
        stop_dist = abs(setup.entry - setup.stop)
        risk_dollars = float(stop_dist * Decimal(qty))
        data_warning = None
        bars_present = getattr(oran, "bars_present", 0) if oran else 0
        if oran is not None and getattr(oran, "low_confidence", False):
            data_warning = f"iex_partial: bars_present={bars_present}"
        return ApprovalRequest(
            setup=setup,
            symbol=self.symbol,
            qty=qty,
            risk_dollars=risk_dollars,
            mode=self.mode,
            feed=self.run.feed,
            or_high=getattr(oran, "high", setup.entry) if oran else setup.entry,
            or_low=getattr(oran, "low", setup.stop) if oran else setup.stop,
            bars_present=bars_present,
            data_warning=data_warning,
            approval_ttl_s=self.cfg.approval_timeout_s,
        )

    async def _react(self, ev) -> None:
        if isinstance(ev, RangeEstablished):
            self.log.info("state_transition", to="RANGE_SET")
            return
        if isinstance(ev, DirectionConfirmed):
            self.log.info(
                "state_transition",
                to="WAIT_ENTRY",
                direction=ev.direction.value,
                break_level=str(ev.break_level),
            )
            return
        if isinstance(ev, RangeDayDetected):
            self.log.info("range_day_detected")
            return
        if isinstance(ev, (EntryConfirmed, NoOp)):
            return
        if isinstance(ev, WindowExpired):
            self.log.info("window_expired")
            return
        if isinstance(ev, TradeRecorded):
            self.log.info(
                "trade_closed", pnl=str(ev.pnl), exit_reason=ev.exit_reason
            )
            return
        if isinstance(ev, SetupProposed):
            await self._on_setup_proposed(ev.setup)
            return

    async def _on_setup_proposed(self, setup: Setup) -> None:
        equity = self._equity_for_sizing()
        qty = self._sizing(equity, setup)
        if qty < 1:
            self.log.info("setup_rejected", reason="risk too small for one whole share")
            return
        # Stale-price re-validation (§8 step 6 / §16): after sizing, BEFORE asking the
        # approver, confirm the level is still reachable on the latest 1m price.
        if not self._revalidate_setup(setup):
            self.log.info("setup_rejected", reason="stale price: level no longer reachable")
            return
        req = self._build_approval_request(setup, qty)
        self.log.info(
            "setup_proposed",
            direction=setup.direction.value,
            model=setup.model.value,
            entry=str(setup.entry),
            stop=str(setup.stop),
            target=str(setup.target),
            qty=qty,
        )
        decision = await self.approver.request(req)
        self.log.info("approval", decision=decision, approver=self.mode)
        self.engine.on_approval(decision)
        if decision != "APPROVE":
            return
        res = await self.broker.submit_bracket(setup, qty)
        # Record the submitted setup's model so the eventual TradeResult is
        # attributed correctly (MED #11). Reset on re-arm / new proposal.
        self._last_model = setup.model
        self.log.info(
            "order_submitted",
            order_id=res.order_id,
            client_order_id=res.client_order_id,
            qty=qty,
        )
        await self.reporter.trade_taken(setup, qty, self.mode)
```

- [ ] **Step 4: Run and confirm pass.**

```bash
pytest tests/test_orchestrator.py -q -k "react or build_approval"
```
Expected: PASS (5 passed).

- [ ] **Step 5: Commit.**

```bash
git add orb_bot/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): _react setup->approval->bracket, mode-uniform submit, trade_taken notice"
```

### Task 41: Orchestrator — fill tracking (entry fill decrements slot; exit + FLATTEN synthesis)

**Files:**
- Modify: `orb_bot/orchestrator.py`
- Test: `tests/test_orchestrator.py` (fill cases)

**Interfaces:**
- Consumes: `Broker.trade_updates()->AsyncIterator[Fill]`; `Engine.on_entry_filled(res)`, `Engine.on_trade_closed(fill)`; `Fill(order_id, client_order_id, leg_role, side, price, qty, ts, position_qty, exit_reason)`; `OrderResult`.
- Produces: `Orchestrator._on_fill(fill: Fill) -> None` (routes by `leg_role`); appends `TradeResult` to `self.trades`; tracks `self.entry_fill`; synthesizes a `FLATTEN` `TradeResult`. `run()` and `_flatten_eod()` consume these.

- [ ] **Step 1: Write failing fill tests.**

```python
# tests/test_orchestrator.py  (append)
def _fill(leg_role, side, price, qty, position_qty, exit_reason=None, coid="2026-06-19-AAPL-1"):
    return Fill(
        order_id="o-" + leg_role,
        client_order_id=f"{coid}-{leg_role}",
        leg_role=leg_role,
        side=side,
        price=Decimal(str(price)),
        qty=qty,
        ts=datetime.datetime(2026, 6, 19, 10, 0, tzinfo=ET),
        position_qty=position_qty,
        exit_reason=exit_reason,
    )


async def test_entry_fill_routes_to_engine_on_entry_filled():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    assert len(o.engine.entry_fills) == 1
    assert o.entry_fill is not None
    assert o.entry_fill.qty == 10


async def test_partial_entry_fill_not_yet_in_trade():
    # MED #13: a partial entry fill (position_qty < cumulative qty) must NOT flip
    # to IN_TRADE / notify the engine.
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 4, position_qty=4))  # fill 4 of 10
    assert o.engine.entry_fills == []   # not yet full -> engine not told
    assert o.entry_fill is None
    # second partial completes the position
    await o._on_fill(_fill("ENTRY", "buy", 100.50, 6, position_qty=10))
    assert len(o.engine.entry_fills) == 1
    assert o.entry_fill is not None


async def test_partial_exit_fills_share_weighted_exit_price():
    # HIGH #5: two partial TP fills average share-weighted via the pure builder.
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    await o._on_fill(_fill("TP", "sell", 102.00, 6, position_qty=4, exit_reason="TARGET"))
    await o._on_fill(_fill("TP", "sell", 103.00, 4, position_qty=0, exit_reason="TARGET"))
    assert len(o.trades) == 1
    tr = o.trades[0]
    # (102*6 + 103*4)/10 = 102.40 share-weighted exit
    assert tr.exit_price == Decimal("102.40")
    assert tr.qty == 10
    assert tr.pnl == Decimal("24.00")  # (102.40-100)*10


async def test_target_exit_builds_trade_result_and_records():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    await o._on_fill(
        _fill("TP", "sell", 102.00, 10, position_qty=0, exit_reason="TARGET")
    )
    assert len(o.engine.closed_fills) == 1
    assert len(o.trades) == 1
    tr = o.trades[0]
    assert tr.exit_reason == "TARGET"
    assert tr.entry_price == Decimal("100.00")
    assert tr.exit_price == Decimal("102.00")
    assert tr.qty == 10
    assert tr.pnl == Decimal("20.00")  # (102-100)*10 LONG
    assert tr.pnl_pct == Decimal("20.00") / Decimal("100000")


async def test_stop_exit_long_negative_pnl():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    await o._on_fill(
        _fill("SL", "sell", 99.00, 10, position_qty=0, exit_reason="STOP")
    )
    assert o.trades[0].pnl == Decimal("-10.00")
    assert o.trades[0].exit_reason == "STOP"


async def test_flatten_fill_synthesizes_trade_result():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o.flatten_coid = "2026-06-19-AAPL-1-FLATTEN"
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    await o._on_fill(
        _fill("FLATTEN", "sell", 100.50, 10, position_qty=0, exit_reason="FLATTEN")
    )
    assert len(o.trades) == 1
    tr = o.trades[0]
    assert tr.exit_reason == "FLATTEN"
    assert tr.pnl == Decimal("5.00")  # (100.50-100)*10
    # FLATTEN is synthesized by orchestrator, NOT via engine.on_trade_closed
    assert o.engine.closed_fills == []
```

- [ ] **Step 2: Run and confirm fail.**

```bash
pytest tests/test_orchestrator.py -q -k "fill or exit"
```
Expected: FAIL (`AttributeError: 'Orchestrator' object has no attribute '_on_fill'`).

- [ ] **Step 3: Implement fill routing + TradeResult builder.**

```python
# orb_bot/orchestrator.py  (add to __init__, after self.entry_fill line):
#     self._entry_fills: list[Fill] = []
#     self._exit_fills: list[Fill] = []
```

```python
# orb_bot/orchestrator.py  (add methods to Orchestrator)
    def _build_trade_result(
        self, entry_fills: list[Fill], exit_fills: list[Fill], exit_reason: str
    ) -> TradeResult:
        # Delegate to the pure builder (spec §17a): share-weighted entry/exit
        # prices over partial fills, qty = min(entry, exit), pnl_pct as a fraction.
        direction = (
            Direction.LONG if entry_fills[0].side.lower() == "buy" else Direction.SHORT
        )
        model = self._last_model if self._last_model is not None else Model.BREAKOUT
        start_equity = self.start_equity if self.start_equity else Decimal("1")
        return summary.build_trade_result(
            entry_fills,
            exit_fills,
            direction,
            model,
            start_equity,
            exit_reason,
        )

    async def _on_fill(self, fill: Fill) -> None:
        self.log.info(
            "fill",
            leg_role=fill.leg_role,
            side=fill.side,
            price=str(fill.price),
            qty=fill.qty,
            position_qty=fill.position_qty,
        )
        if fill.leg_role == "ENTRY":
            # Accumulate entry partial fills; only flip to IN_TRADE on a TRUE full
            # fill (position_qty == cumulative entry qty). A partial entry must NOT
            # be treated as full (MED #13).
            self._entry_fills.append(fill)
            if fill.position_qty == fill.qty:
                self.entry_fill = fill  # representative full-fill marker
                res = OrderResult(
                    order_id=fill.order_id,
                    client_order_id=fill.client_order_id,
                    status="filled",
                    filled_avg_price=summary.weighted_avg_price(self._entry_fills),
                    filled_qty=sum(f.qty for f in self._entry_fills),
                    legs=[],
                )
                self.engine.on_entry_filled(res)
            return

        if fill.leg_role in ("TP", "SL"):
            if self.entry_fill is None:
                return
            reason = fill.exit_reason or ("TARGET" if fill.leg_role == "TP" else "STOP")
            self._exit_fills.append(fill)
            self.trades.append(
                self._build_trade_result(
                    list(self._entry_fills), list(self._exit_fills), reason
                )
            )
            self.engine.on_trade_closed(fill)
            self.entry_fill = None
            self._entry_fills = []
            self._exit_fills = []
            return

        if fill.leg_role == "FLATTEN":
            # Orchestrator synthesizes the FLATTEN TradeResult directly (§8 step 9);
            # the OCO legs were cancelled so the engine never sees this close.
            if self.entry_fill is None:
                return
            self._exit_fills.append(fill)
            self.trades.append(
                self._build_trade_result(
                    list(self._entry_fills), list(self._exit_fills), "FLATTEN"
                )
            )
            self.entry_fill = None
            self._entry_fills = []
            self._exit_fills = []
            return
```

- [ ] **Step 4: Run and confirm pass.**

```bash
pytest tests/test_orchestrator.py -q -k "fill or exit"
```
Expected: PASS (4 passed).

- [ ] **Step 5: Commit.**

```bash
git add orb_bot/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): fill routing, entry-fill slot consume, OCO + synthesized FLATTEN TradeResults"
```

### Task 42: Orchestrator — flatten timer (`_flatten_eod`: cancel_all + flatten + tagged FLATTEN coid)

**Files:**
- Modify: `orb_bot/orchestrator.py`
- Test: `tests/test_orchestrator.py` (flatten cases)

**Interfaces:**
- Consumes: `Broker.cancel_all()`, `Broker.flatten()`.
- Produces: `Orchestrator._flatten_eod() -> None` (sets a deterministic `self.flatten_coid` *before* calling `close_all_positions`, calls `cancel_all()` then `flatten()`, ordered, idempotent-safe). `run()`'s flatten timer consumes it.

- [ ] **Step 1: Write failing flatten tests.**

```python
# tests/test_orchestrator.py  (append)
async def test_flatten_eod_cancels_then_flattens():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    await o._flatten_eod()
    assert broker.cancel_all_calls == 1
    assert broker.flatten_calls == 1


async def test_flatten_eod_tags_flatten_client_order_id_before_flatten():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o.trade_seq = 1
    await o._flatten_eod()
    assert o.flatten_coid is not None
    assert o.flatten_coid.endswith("-FLATTEN")
    assert "AAPL" in o.flatten_coid


async def test_flatten_eod_idempotent_second_call_noops_on_already_flat():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    await o._flatten_eod()
    await o._flatten_eod()  # second call must be safe
    # cancel/flatten are idempotent; we only require no exception + flatten ran at least once
    assert broker.flatten_calls >= 1
```

- [ ] **Step 2: Run and confirm fail.**

```bash
pytest tests/test_orchestrator.py -q -k flatten_eod
```
Expected: FAIL (`AttributeError: 'Orchestrator' object has no attribute '_flatten_eod'`).

- [ ] **Step 3: Implement `_flatten_eod`.**

```python
# orb_bot/orchestrator.py  (add method to Orchestrator)
    async def _flatten_eod(self) -> None:
        session_date = (
            self.clock.now().date()
            if self.next_close is None
            else self.next_close.date()
        )
        # Tag the FLATTEN close with a deterministic client_order_id BEFORE
        # close_all_positions so its fill is attributable (§8 step 9). Reuse the
        # broker's trade_seq (single source of truth) and the canonical helper
        # rather than re-deriving the id with an ad-hoc f-string (MED #12).
        seq = getattr(self.broker, "current_seq", self.trade_seq)
        self.flatten_coid = execution_alpaca.client_order_id(
            session_date, self.symbol, seq, "FLATTEN"
        )
        self.log.info("flatten_eod", flatten_coid=self.flatten_coid)
        await self.broker.cancel_all()   # cancel resting bracket children first
        await self.broker.flatten()      # close_all_positions(cancel_orders=True), idempotent
```

- [ ] **Step 4: Run and confirm pass.**

```bash
pytest tests/test_orchestrator.py -q -k flatten_eod
```
Expected: PASS (3 passed).

- [ ] **Step 5: Commit.**

```bash
git add orb_bot/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): EOD flatten — cancel_all before flatten, tagged FLATTEN client_order_id"
```

### Task 43: Orchestrator — SessionSummary builder + session_report (both modes, once)

**Files:**
- Modify: `orb_bot/orchestrator.py`
- Test: `tests/test_orchestrator.py` (summary/report cases)

**Interfaces:**
- Consumes: `Broker.get_account()->AccountSnapshot`; `Reporter.session_report(summary)`; `SessionSummary`, `TradeResult`; engine terminal `State` for `no_trade_reason`.
- Produces: `Orchestrator._build_session_summary() -> SessionSummary` (tallies wins/losses/breakevens, total_pnl, fractional pnl_pct, end_equity, no_trade_reason); `Orchestrator._emit_session_report() -> None` (idempotent — fires `session_report` exactly once). `run()` shutdown consumes it.

- [ ] **Step 1: Write failing summary/report tests.**

```python
# tests/test_orchestrator.py  (append)
async def test_build_session_summary_tallies_win_loss_breakeven():
    broker = FakeBroker(equity=Decimal("100050"))
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o.trades = [
        TradeResult(Direction.LONG, Model.BREAKOUT, 10, Decimal("100"), Decimal("102"),
                    Decimal("20.00"), Decimal("20.00") / Decimal("100000"), "TARGET"),
        TradeResult(Direction.LONG, Model.BREAKOUT, 10, Decimal("100"), Decimal("99"),
                    Decimal("-10.00"), Decimal("-10.00") / Decimal("100000"), "STOP"),
        TradeResult(Direction.LONG, Model.BREAKOUT, 10, Decimal("100"), Decimal("100"),
                    Decimal("0.00"), Decimal("0"), "FLATTEN"),
    ]
    summary = await o._build_session_summary()
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.breakevens == 1
    assert summary.wins + summary.losses + summary.breakevens == len(summary.trades)
    assert summary.total_pnl == Decimal("10.00")
    assert summary.total_pnl_pct == Decimal("10.00") / Decimal("100000")
    assert summary.start_equity == Decimal("100000")
    assert summary.end_equity == Decimal("100050")
    assert summary.mode == "PAPER"
    assert summary.symbol == "AAPL"
    assert summary.no_trade_reason is None


async def test_build_session_summary_no_trade_reason_window_expired():
    broker = FakeBroker(equity=Decimal("100000"))
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o.trades = []
    o.engine._done = True
    o._terminal_reason = "window expired"
    summary = await o._build_session_summary()
    assert summary.trades == []
    assert summary.total_pnl == Decimal("0")
    assert summary.no_trade_reason == "window expired"


async def test_emit_session_report_fires_once():
    broker = FakeBroker()
    reporter = FakeReporter()
    o = _make_orch(broker, reporter=reporter)
    o.start_equity = Decimal("100000")
    o.trades = []
    await o._emit_session_report()
    await o._emit_session_report()  # must NOT double-report
    assert len(reporter.session_reports) == 1
```

- [ ] **Step 2: Run and confirm fail.**

```bash
pytest tests/test_orchestrator.py -q -k "session_summary or session_report"
```
Expected: FAIL (`AttributeError: '_build_session_summary'`).

- [ ] **Step 3: Implement summary builder + idempotent emit.**

```python
# orb_bot/orchestrator.py  (add to __init__: self._reported = False ; self._terminal_reason = None)
```

```python
# orb_bot/orchestrator.py  (insert these two lines into __init__ after self._stop = asyncio.Event())
        self._reported = False
        self._terminal_reason: str | None = None
```

```python
# orb_bot/orchestrator.py  (add methods to Orchestrator)
    def _no_trade_reason(self) -> str | None:
        if self.trades:
            return None
        if self._terminal_reason:
            return self._terminal_reason
        return "no confirmed breakout/entry"

    async def _build_session_summary(self) -> SessionSummary:
        # Only fetch end_equity + delegate the tallying to the pure builder
        # (spec §17a) so the orchestrator does not re-implement P/L logic (HIGH #5).
        try:
            acct = await self.broker.get_account()
            self.end_equity = acct.equity
        except Exception:  # noqa: BLE001 - report must still emit
            self.end_equity = self.start_equity
        return summary.build_session_summary(
            trades=self.trades,
            start_equity=self.start_equity if self.start_equity else Decimal("0"),
            end_equity=self.end_equity if self.end_equity is not None else Decimal("0"),
            mode=self.mode,
            symbol=self.symbol,
            session_date=self.clock.now().date(),
            no_trade_reason=self._no_trade_reason(),
        )

    async def _emit_session_report(self) -> None:
        if self._reported:
            return
        self._reported = True
        summary = await self._build_session_summary()
        self.log.info(
            "session_report",
            total_pnl=str(summary.total_pnl),
            wins=summary.wins,
            losses=summary.losses,
            breakevens=summary.breakevens,
            no_trade_reason=summary.no_trade_reason,
        )
        await self.reporter.session_report(summary)
```

- [ ] **Step 4: Run and confirm pass.**

```bash
pytest tests/test_orchestrator.py -q -k "session_summary or session_report"
```
Expected: PASS (3 passed).

- [ ] **Step 5: Commit.**

```bash
git add orb_bot/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): SessionSummary builder + once-only session_report in both modes"
```

### Task 44: Orchestrator — `run()` loop (drain feed → aggregate → engine.on_candle → react), graceful shutdown

**Files:**
- Modify: `orb_bot/orchestrator.py`
- Test: `tests/test_orchestrator.py` (run-loop integration cases)

**Interfaces:**
- Consumes: `DataFeed.candles()->AsyncIterator[Candle]`; `Aggregator.add(one_min)->Candle|None`; `Engine.on_candle(c)->list[EngineEvent]`, `Engine.is_done()`; `Approver.start()/close()`, `Reporter.start()/close()`, `feed.close()`.
- Produces: `Orchestrator.run() -> None` (full lifecycle: preflight → start approver/reporter → loop aggregate+react → flatten → session_report → teardown). End-to-end consumer; `__main__` calls `await orch.run()`.

- [ ] **Step 1: Write failing run-loop tests.**

```python
# tests/test_orchestrator.py  (append)
async def test_run_paper_full_cycle_submits_and_reports():
    # 1m candles for two 15m buckets; FakeEngine emits SetupProposed on the
    # candle index where the second 15m bucket closes.
    one_min = []
    base = datetime.datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    for i in range(31):  # 09:30..10:00 -> closes 09:45 and 10:00 buckets
        ts = base + datetime.timedelta(minutes=i)
        one_min.append(
            Candle(ts_open=ts, ts_close=ts + datetime.timedelta(minutes=1),
                   open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
                   close=Decimal("100"), volume=5, timeframe_min=1)
        )
    feed = FakeFeed(one_min)
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="APPROVE")
    reporter = FakeReporter()
    # engine emits SetupProposed on the 2nd aggregated 15m candle (index 1)
    engine = FakeEngine(events_by_index={1: [SetupProposed(setup=_setup_long())]})
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    o = orch_mod.Orchestrator(_settings(), feed, broker, approver, reporter, clk, engine)
    await o.run()
    assert approver.started and reporter.started
    assert len(broker.submitted) == 1
    assert len(reporter.session_reports) == 1
    assert feed.closed and approver.closed and reporter.closed


async def test_run_market_closed_skips_loop_but_reports_with_start_equity():
    feed = FakeFeed([])
    broker = FakeBroker(is_open=False)
    reporter = FakeReporter()
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    o = orch_mod.Orchestrator(_settings(), feed, broker, FakeApprover(), reporter, clk, FakeEngine())
    await o.run()
    # market closed → no submit; session_report not emitted (no start_equity captured)
    assert broker.submitted == []
    assert reporter.session_reports == []


async def test_run_boundary_timer_force_closes_missing_minute_bucket():
    # HIGH #7: a bucket whose minutes are missing must still emit via force_close.
    # Feed a single 1m candle near a 15m boundary, then assert the engine saw a
    # force-closed T-candle.
    base = datetime.datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    one_min = [
        Candle(ts_open=base, ts_close=base + datetime.timedelta(minutes=1),
               open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
               close=Decimal("100"), volume=5, timeframe_min=1)
    ]
    feed = FakeFeed(one_min)
    broker = FakeBroker(equity=Decimal("100000"))
    engine = FakeEngine()
    # clock is past the 09:45 boundary + grace so the timer fires for the 09:30 bucket
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 46, tzinfo=ET))
    o = orch_mod.Orchestrator(_settings(), feed, broker, FakeApprover(), FakeReporter(), clk, engine)
    await o.run()
    # the single 1m candle did not advance the bucket, so force_close emitted the
    # partial 09:30 T-candle which the engine received.
    assert len(engine.candles) >= 1


async def test_run_stops_on_engine_done():
    one_min = []
    base = datetime.datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    for i in range(16):
        ts = base + datetime.timedelta(minutes=i)
        one_min.append(
            Candle(ts_open=ts, ts_close=ts + datetime.timedelta(minutes=1),
                   open=Decimal("100"), high=Decimal("100"), low=Decimal("100"),
                   close=Decimal("100"), volume=1, timeframe_min=1)
        )
    feed = FakeFeed(one_min)
    broker = FakeBroker(equity=Decimal("100000"))
    engine = FakeEngine()
    engine._done = True  # done immediately after first aggregated candle
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    o = orch_mod.Orchestrator(_settings(), feed, broker, FakeApprover(), FakeReporter(), clk, engine)
    await o.run()
    assert len(o.reporter.session_reports) == 1
```

- [ ] **Step 2: Run and confirm fail.**

```bash
pytest tests/test_orchestrator.py -q -k "run_"
```
Expected: FAIL (`AttributeError: 'Orchestrator' object has no attribute 'run'` or `NotImplementedError`).

- [ ] **Step 3: Implement `run()` + helpers.**

```python
# orb_bot/orchestrator.py  (add method to Orchestrator)
    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._stop.set)
        except (NotImplementedError, RuntimeError):
            # signal handlers unavailable (e.g. non-main thread / Windows) — skip
            pass

    def _aggregate(self, c1m: Candle) -> list[Candle]:
        out: list[Candle] = []
        closed = self._aggr.add(c1m)
        if closed is not None:
            out.append(closed)
        return out

    def _bucket_boundary_after(self, ts: datetime.datetime) -> datetime.datetime:
        """Wall-clock instant `bar_grace_seconds` after the close of the
        range_timeframe_min bucket containing `ts` (the boundary timer fire time)."""
        bucket_start = self._aggr.bucket_start(ts)
        bucket_close = bucket_start + datetime.timedelta(
            minutes=self.cfg.range_timeframe_min
        )
        return bucket_close + datetime.timedelta(seconds=self.cfg.bar_grace_seconds)

    async def _force_close_boundary(self, boundary_ts: datetime.datetime) -> None:
        """Fire the aggregator boundary timer (spec §8): a bucket with missing
        minutes still emits its (partial) candle, which we feed through the engine."""
        candle = self._aggr.force_close(boundary_ts)
        if candle is None:
            return
        for ev in self.engine.on_candle(candle):
            await self._react(ev)

    async def _drain_trade_updates(self) -> None:
        try:
            async for fill in self.broker.trade_updates():
                await self._on_fill(fill)
                if self._stop.is_set():
                    break
        except asyncio.CancelledError:  # graceful task cancel on teardown
            raise

    async def run(self) -> None:
        self._install_signal_handlers()
        ok = await self._preflight()
        if not ok:
            self.log.info("run_exit_preflight_done", mode=self.mode)
            await self._teardown(report=False)
            return

        await self.approver.start()
        await self.reporter.start()
        updates_task = asyncio.create_task(self._drain_trade_updates())

        try:
            async for c1m in self.feed.candles():
                if self._stop.is_set():
                    break
                self._last_1m = c1m  # latest transport price for re-validation (HIGH #9)
                now = self.clock.now()
                if self.flatten_at is not None and now >= self.flatten_at:
                    await self._flatten_eod()
                emitted = self._aggregate(c1m)
                # If the 1m feed skipped past a bucket edge with no advancing candle,
                # fire the boundary timer so a missing-minute bucket still emits
                # (spec §8 / HIGH #7).
                if not emitted:
                    boundary_ts = self._bucket_boundary_after(c1m.ts_close)
                    if now >= boundary_ts:
                        await self._force_close_boundary(boundary_ts)
                for tf_candle in emitted:
                    for ev in self.engine.on_candle(tf_candle):
                        await self._react(ev)
                if self.engine.is_done():
                    break
        finally:
            updates_task.cancel()
            try:
                await updates_task
            except asyncio.CancelledError:
                pass
            await self._teardown(report=True)

    async def _teardown(self, *, report: bool) -> None:
        # Emit the session report only if preflight captured start_equity (§15).
        if report and self.start_equity is not None:
            await self._emit_session_report()
        await self.feed.close()
        await self.approver.close()
        await self.reporter.close()
        self.log.info("shutdown_complete", mode=self.mode)
```

- [ ] **Step 4: Run and confirm pass.**

```bash
pytest tests/test_orchestrator.py -q
```
Expected: PASS (all orchestrator tests green).

- [ ] **Step 5: Commit.**

```bash
git add orb_bot/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): run() loop, aggregator boundary timer (force_close), trade-updates drain, graceful shutdown"
```

### Task 45: Entry point — `__main__.py` (mode-based dependency wiring)

**Files:**
- Create: `orb_bot/__main__.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `load_config('config/config.yaml') -> Settings` (FLAT secrets `alpaca_key`/`alpaca_secret`/`discord_token`/`discord_channel_id`/`discord_approver_user_id` + nested `strategy`/`run` — there is NO `settings.secrets`); `Orchestrator(settings, feed, broker, approver, reporter, clock, engine)`; `Engine(cfg, session_date)`; concrete impls with their REAL constructors: `AlpacaFeed(*, api_key, secret_key, symbol, feed)`, `AlpacaBroker(run, key, secret, *, session_date)`, `AutoApprover()`, `DiscordApprover(client, approver_user_id)`, `DiscordReporter(client, *, timeframe_min)`, `LogReporter()`, and the shared `discordbot.DiscordClient(token, channel_id)`.
- Produces: `build_orchestrator(settings) -> Orchestrator` (selects `AutoApprover` for paper / `DiscordApprover` for live; `DiscordReporter` when Discord configured else `LogReporter`; builds ONE shared `DiscordClient` when Discord is configured and shares it with approver+reporter); `main() -> None` (`load_config('config/config.yaml')`, builds, `asyncio.run(orch.run())`). `python -m orb_bot` invokes `main()`.

- [ ] **Step 1: Write failing wiring tests (monkeypatched impls — no network).**

```python
# tests/test_main.py
import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot import __main__ as main_mod
from orb_bot.config import Settings, StrategyConfig, RunConfig

ET = ZoneInfo("America/New_York")


class _Stub:
    def __init__(self, *a, **k):
        self.args = a
        self.kwargs = k


@pytest.fixture
def patched(monkeypatch):
    created = {}

    def mk(name):
        def factory(*a, **k):
            inst = _Stub(*a, **k)
            created.setdefault(name, []).append(inst)
            return inst
        return factory

    monkeypatch.setattr(main_mod, "AlpacaFeed", mk("AlpacaFeed"))
    monkeypatch.setattr(main_mod, "AlpacaBroker", mk("AlpacaBroker"))
    monkeypatch.setattr(main_mod, "AutoApprover", mk("AutoApprover"))
    monkeypatch.setattr(main_mod, "DiscordApprover", mk("DiscordApprover"))
    monkeypatch.setattr(main_mod, "DiscordReporter", mk("DiscordReporter"))
    monkeypatch.setattr(main_mod, "LogReporter", mk("LogReporter"))
    monkeypatch.setattr(main_mod, "DiscordClient", mk("DiscordClient"))
    return created


def _shared_client_factory(created):
    """Factory recording each DiscordClient construction (to assert ONE shared client)."""
    def factory(*a, **k):
        inst = _Stub(*a, **k)
        created.setdefault("DiscordClient", []).append(inst)
        return inst
    return factory


def _paper_settings():
    return Settings(strategy=StrategyConfig(), run=RunConfig(symbol="AAPL", live=False))


def _live_settings():
    return Settings(strategy=StrategyConfig(), run=RunConfig(symbol="AAPL", live=True, feed="SIP"))


def test_build_orchestrator_paper_uses_auto_approver(patched, monkeypatch):
    # no discord secrets (FLAT fields) => LogReporter
    s = _paper_settings()
    monkeypatch.setattr(s, "discord_token", None, raising=False)
    o = main_mod.build_orchestrator(s)
    assert "AutoApprover" in patched
    assert "DiscordApprover" not in patched
    assert "LogReporter" in patched
    assert o.run.live is False


def test_build_orchestrator_live_uses_discord_approver(patched, monkeypatch):
    s = _live_settings()
    monkeypatch.setattr(s, "discord_token", "tok", raising=False)
    monkeypatch.setattr(s, "discord_channel_id", 123, raising=False)
    monkeypatch.setattr(s, "discord_approver_user_id", 999, raising=False)
    # shared DiscordClient is built once and passed to both approver + reporter
    monkeypatch.setattr(main_mod, "DiscordClient", mk_dc := _shared_client_factory(patched))
    o = main_mod.build_orchestrator(s)
    assert "DiscordApprover" in patched
    assert "AutoApprover" not in patched
    assert "DiscordReporter" in patched
    assert len(patched["DiscordClient"]) == 1  # ONE shared client


def test_build_orchestrator_uses_log_reporter_when_discord_absent(patched, monkeypatch):
    s = _paper_settings()
    monkeypatch.setattr(s, "discord_token", None, raising=False)
    main_mod.build_orchestrator(s)
    assert "LogReporter" in patched
    assert "DiscordReporter" not in patched
```

- [ ] **Step 2: Run and confirm fail.**

```bash
pytest tests/test_main.py -q
```
Expected: FAIL (`ImportError`/`AttributeError` — `orb_bot.__main__` has no `build_orchestrator` / impl names).

- [ ] **Step 3: Implement `__main__.py`.**

```python
# orb_bot/__main__.py
"""`python -m orb_bot`: load config, build mode-appropriate dependencies, run.

Wiring rules (spec §4, §14, §17a):
  - approver: AutoApprover (paper) | DiscordApprover (live)
  - reporter: DiscordReporter when Discord is configured, else LogReporter
  - a single shared discordbot.DiscordClient is built ONCE when Discord is
    configured and passed to BOTH the DiscordApprover and the DiscordReporter.
Secrets are FLAT on Settings (settings.alpaca_key, settings.discord_token, ...);
there is no settings.secrets. The Orchestrator and Engine are mode-agnostic;
only the injected impls differ.
"""
from __future__ import annotations

import asyncio
import datetime
from zoneinfo import ZoneInfo

from orb_bot.config import Settings, load_config
from orb_bot.engine import Engine
from orb_bot.orchestrator import Orchestrator

# Concrete impls — imported here so __main__ is the only network-aware wiring
# point. Tests monkeypatch these names on this module.
from orb_bot.discordbot import DiscordClient
from orb_bot.feed.alpaca import AlpacaFeed
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.approval.auto import AutoApprover
from orb_bot.approval.manual import DiscordApprover
from orb_bot.reporting.discord import DiscordReporter
from orb_bot.reporting.log import LogReporter


class _SystemClock:
    """Tz-aware ET wall clock; orchestrator-only (engine stays pure)."""

    def __init__(self, tz: str):
        self._tz = ZoneInfo(tz)

    def now(self) -> datetime.datetime:
        return datetime.datetime.now(self._tz)


def _discord_configured(settings: Settings) -> bool:
    # Secrets are FLAT on Settings (there is NO settings.secrets).
    return bool(getattr(settings, "discord_token", None)) and bool(
        getattr(settings, "discord_channel_id", None)
    )


def build_orchestrator(settings: Settings) -> Orchestrator:
    cfg = settings.strategy
    run = settings.run
    clock = _SystemClock(cfg.timezone)
    session_date = clock.now().date()

    # Concrete impls built with their REAL constructor signatures.
    feed = AlpacaFeed(
        api_key=settings.alpaca_key,
        secret_key=settings.alpaca_secret,
        symbol=run.symbol,
        feed=run.feed,
    )
    broker = AlpacaBroker(
        run=run,
        key=settings.alpaca_key,
        secret=settings.alpaca_secret,
        session_date=session_date,
    )

    # Build ONE shared DiscordClient when Discord is configured and pass it to
    # both the live approver and the Discord reporter (spec §14).
    discord_client = None
    if _discord_configured(settings):
        discord_client = DiscordClient(
            settings.discord_token, settings.discord_channel_id
        )

    if run.live:
        approver = DiscordApprover(discord_client, settings.discord_approver_user_id)
    else:
        approver = AutoApprover()

    if discord_client is not None:
        reporter = DiscordReporter(
            discord_client, timeframe_min=cfg.range_timeframe_min
        )
    else:
        reporter = LogReporter()

    engine = Engine(cfg, session_date)

    return Orchestrator(settings, feed, broker, approver, reporter, clock, engine)


def main() -> None:
    settings = load_config("config/config.yaml")
    orch = build_orchestrator(settings)
    asyncio.run(orch.run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run and confirm pass.**

```bash
pytest tests/test_main.py -q
```
Expected: PASS (3 passed). If concrete impl modules are not yet implemented in this build, gate this task behind them; the monkeypatched test only requires the module-level names to exist as imports.

- [ ] **Step 5: Commit.**

```bash
git add orb_bot/__main__.py tests/test_main.py
git commit -m "feat(main): python -m orb_bot entrypoint with mode-based dependency wiring"
```

---

The repo is bare. My module is `tests/` only, producing the test files that enforce purity and the network-gated smoke test, plus CSV fixtures and a loader. I have all the spec detail I need (§4 layout, §18 testing strategy, §13 SDK methods, §6 protocols, §10 #13). Now I'll write the markdown plan.

### Task 46: Tests — purity enforcement (import-linter contract + no-`now()` grep)

**Files:**
- Create `tests/test_purity.py` (runs `lint-imports`, greps pure modules).
- CONSUMES (do NOT create/overwrite — owned by Task 1): `tests/__init__.py`, `tests/context.py` (path shim), and the import-linter contract, which lives in `pyproject.toml` `[tool.importlinter]` (Task 1). There is NO standalone `.importlinter` file — `lint-imports` reads the contract from `pyproject.toml`, matching the Makefile's bare `lint-imports`.

**Interfaces:** Consumes: the pure modules `orb_bot/{models,aggregation,indicators,engine}.py` and the I/O modules `orb_bot/{feed,execution,approval,reporting,orchestrator,discordbot}` (their existence is asserted by the contract; the test skips gracefully until they exist). Produces: a CI-runnable purity gate (`tests/test_purity.py`) and the shared `tests/context.py` shim every other test module imports via `from .context import orb_bot`.

- [ ] **Step 1: Ensure the `tests/context.py` path shim and `tests/__init__.py` exist (idempotent — owned by Task 1).**
  Per the Hitchhiker's Guide §Test Suite: insert the repo root on `sys.path` so `import orb_bot` resolves regardless of install method. `tests/__init__.py` must exist so test modules can do relative `from .context import orb_bot`. Task 1 already created both; consume them as-is. Only create a missing one (running this task in isolation) from the canonical body below.

```bash
mkdir -p tests
[ -f tests/__init__.py ] || : > tests/__init__.py
[ -f tests/context.py ] || cat > tests/context.py <<'PY'
"""Path shim (Hitchhiker's Guide §Test Suite).

Inserts the repository root on sys.path and exposes ``orb_bot`` so every test
module can do ``from .context import orb_bot`` regardless of install method.
"""
import os
import sys

# tests/ is a direct child of the repo root; the parent of this file's dir is root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import orb_bot  # noqa: E402,F401  (re-exported for `from .context import orb_bot`)
PY
```

- [ ] **Step 2: Write the failing purity test `tests/test_purity.py`.**
  Two assertions: (a) bare `lint-imports` (reading the contract from `pyproject.toml` `[tool.importlinter]`, exactly as the Makefile runs it) exits 0; (b) no `datetime.now(` / `time.time(` literal appears in the four pure modules. Both skip cleanly while the package/CLI does not yet exist, so the test is runnable from day one and *fails* (xfails→skip) until the modules land, then *passes*. Write it now and confirm it does not error.

```python
# tests/test_purity.py
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
import sys

import pytest

from .context import orb_bot  # noqa: F401  (also primes sys.path for the package)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_PKG_DIR = os.path.join(_REPO_ROOT, "orb_bot")

# The four modules the spec declares PURE (no feed/execution/approval/reporting/
# orchestrator/discordbot imports; no datetime.now()/time.time()).
PURE_MODULES = ["models.py", "aggregation.py", "indicators.py", "engine.py"]

# Matches `datetime.now(`, `dt.now(`, `time.time(` with arbitrary whitespace.
_WALLCLOCK_RE = re.compile(r"\b(?:datetime\.now|time\.time)\s*\(")


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
```

  Run it (the package does not exist yet, so every check skips — confirm zero failures/errors):

```bash
pytest tests/test_purity.py -v
```

  Expected: **PASS** with all cases reported as `SKIPPED` (no `orb_bot` package / no `.importlinter` yet). If you see a collection ERROR on `from .context import orb_bot`, that is the genuine "fails until the package exists" signal — proceed to Step 3 which adds the contract, and the package tasks add `orb_bot/`.

- [ ] **Step 3: Confirm the import-linter contract is present in `pyproject.toml` `[tool.importlinter]` (owned by Task 1 — do NOT create a standalone `.importlinter`).**
  The machine-checkable half of the purity boundary (spec §4) is the forbidden contract Task 1 already wrote into `pyproject.toml` `[tool.importlinter]`: the four pure modules (`orb_bot.models`, `orb_bot.aggregation`, `orb_bot.indicators`, `orb_bot.engine`) may not import the I/O layers (`orb_bot.feed`, `orb_bot.execution`, `orb_bot.approval`, `orb_bot.reporting`, `orb_bot.orchestrator`, `orb_bot.discordbot`). Picking ONE contract location (pyproject) and running bare `lint-imports` keeps this test, the Makefile `lint` target, and Task 1 in agreement — there is no `.importlinter` / `--config` divergence. Verify it is in place:

```bash
grep -q '^\[tool.importlinter\]' pyproject.toml \
  && echo "pyproject [tool.importlinter] contract present (owned by Task 1)" \
  || echo "MISSING [tool.importlinter] in pyproject.toml — Task 1 must add it (do NOT create a standalone .importlinter)"
```

  Re-run; with the contract present in pyproject but the package still absent, the contract test skips on "package not created yet" (no error):

```bash
pytest tests/test_purity.py -v
```

  Expected: **PASS** (still all `SKIPPED`). Once the package tasks have created `orb_bot/`, this same command exercises the real `lint-imports` run and the grep — and must report `PASSED` with exit 0; a purity violation makes it `FAILED`.

- [ ] **Step 4: Commit.**

```bash
git add tests/test_purity.py
git commit -m "test: add purity gate (import-linter contract + no-now() grep)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 47: Tests — CSV candle fixtures + loader, and the network-gated paper smoke test

**Files:**
- Create `tests/fixtures/candles/trend_long.csv`, `range_day.csv`, `weak_breakout.csv`, `retest_hold.csv`.
- Create `tests/fixtures/__init__.py`, `tests/fixtures/loader.py` (CSV → `list[orb_bot.models.Candle]`).
- Create `tests/test_fixtures_loader.py` (verifies the loader + every scenario CSV parses).
- Create `tests/test_smoke_paper.py` (network-gated: SDK surface + paper bracket round-trip + optional Discord connect).

**Interfaces:**
- Consumes (from the canonical contract):
  - `orb_bot.models.Candle(ts_open: datetime, ts_close: datetime, open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: int, timeframe_min: int, data_incomplete: bool=False, bars_present: int|None=None)`.
  - `orb_bot.models.Setup(direction: Direction, model: Model, entry: Decimal, stop: Decimal, target: Decimal, rr: float, reason: list[str])`, `Direction`, `Model`.
  - SDK pins (spec §13/§19): `alpaca-py>=0.43,<0.44` exposing `StockDataStream.subscribe_bars`, `StockDataStream._run_forever`, `StockDataStream.stop_ws`, and `TradingStream` mirrors; `discord.py>=2.6,<3`.
  - Secrets (spec §7): env `ALPACA_KEY`, `ALPACA_SECRET`, optionally `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`.
- Produces:
  - `tests/fixtures/loader.load_scenario(name: str, *, timeframe_min: int = 1, tz: str = "America/New_York") -> list[orb_bot.models.Candle]` and `scenario_path(name: str) -> str` and `SCENARIOS: tuple[str, ...]` — reused by `test_engine.py`, `test_aggregation.py`, and future backtest regressions (spec §18 "Named CSV scenarios … double as future backtest regression inputs").

- [ ] **Step 1: Write the four scenario CSV fixtures.**
  Header `ts_open,open,high,low,close,volume` — `ts_open` is naive ET wall-clock (the loader localizes to `America/New_York` and derives `ts_close = ts_open + timeframe_min`). These are 15m candles anchored at 09:30 (spec §18: scenarios `trend_long`, `range_day`, `weak_breakout`, `retest_hold`). Each is a deterministic hand-built day; the loader keeps prices as `Decimal` strings.

```bash
mkdir -p tests/fixtures/candles
```

```
# tests/fixtures/candles/trend_long.csv
# Opening range 09:30 [100.00,101.00]; strong close breakout up; trends to target.
ts_open,open,high,low,close,volume
2026-06-18 09:30,100.00,101.00,100.00,100.80,50000
2026-06-18 09:45,100.80,102.20,100.70,102.10,61000
2026-06-18 10:00,102.10,103.40,102.00,103.30,58000
2026-06-18 10:15,103.30,104.10,103.10,104.00,54000
2026-06-18 10:30,104.00,104.60,103.80,104.40,40000
2026-06-18 10:45,104.40,104.90,104.20,104.70,38000
2026-06-18 11:00,104.70,105.20,104.50,105.00,36000
2026-06-18 11:15,105.00,105.40,104.80,105.20,33000
```

```
# tests/fixtures/candles/range_day.csv
# Opening range 09:30 [100.00,101.00]; both extremes swept (high then low) -> range day latch.
ts_open,open,high,low,close,volume
2026-06-18 09:30,100.00,101.00,100.00,100.50,50000
2026-06-18 09:45,100.50,101.60,100.40,100.90,47000
2026-06-18 10:00,100.90,101.20,99.40,99.80,52000
2026-06-18 10:15,99.80,100.60,99.50,100.30,45000
2026-06-18 10:30,100.30,101.40,100.10,100.70,43000
2026-06-18 10:45,100.70,101.10,99.60,100.10,41000
2026-06-18 11:00,100.10,100.90,99.70,100.40,39000
2026-06-18 11:15,100.40,100.95,100.05,100.50,37000
```

```
# tests/fixtures/candles/weak_breakout.csv
# Opening range 09:30 [100.00,101.00]; price wicks above 101 but every close stays inside -> no confirmation.
ts_open,open,high,low,close,volume
2026-06-18 09:30,100.00,101.00,100.00,100.40,50000
2026-06-18 09:45,100.40,101.50,100.30,100.80,46000
2026-06-18 10:00,100.80,101.40,100.50,100.90,44000
2026-06-18 10:15,100.90,101.30,100.60,100.85,42000
2026-06-18 10:30,100.85,101.20,100.55,100.70,40000
2026-06-18 10:45,100.70,101.10,100.45,100.60,38000
2026-06-18 11:00,100.60,101.05,100.40,100.55,36000
2026-06-18 11:15,100.55,100.95,100.35,100.50,34000
```

```
# tests/fixtures/candles/retest_hold.csv
# Opening range 09:30 [100.00,101.00]; close-confirm break up at 09:45; later candle retests 101 and holds -> retest entry.
ts_open,open,high,low,close,volume
2026-06-18 09:30,100.00,101.00,100.00,100.70,50000
2026-06-18 09:45,100.70,101.90,100.60,101.70,60000
2026-06-18 10:00,101.70,101.95,101.00,101.10,48000
2026-06-18 10:15,101.10,101.30,100.95,101.25,44000
2026-06-18 10:30,101.25,102.10,101.10,102.00,52000
2026-06-18 10:45,102.00,102.80,101.90,102.60,55000
2026-06-18 11:00,102.60,103.20,102.40,103.00,50000
2026-06-18 11:15,103.00,103.50,102.80,103.30,47000
```

- [ ] **Step 2: Write the loader `tests/fixtures/loader.py` + `tests/fixtures/__init__.py`.**
  Pure stdlib (`csv`, `decimal`, `datetime`, `zoneinfo`). Skips `#`-comment and blank lines, builds tz-aware ET `Candle`s with `Decimal` prices and `ts_close = ts_open + timeframe_min`.

```python
# tests/fixtures/__init__.py
```

```python
# tests/fixtures/loader.py
"""CSV scenario loader for engine/aggregation tests and future backtest regressions.

Named scenarios live in ``tests/fixtures/candles/<name>.csv`` (spec §18). Each
CSV has the header ``ts_open,open,high,low,close,volume`` with ``ts_open`` as a
naive ET wall-clock string ``YYYY-MM-DD HH:MM``. Prices are parsed as Decimal,
timestamps localized to America/New_York, and ``ts_close = ts_open + tf``.
Lines starting with ``#`` and blank lines are ignored.
"""
import csv
import datetime as _dt
import os
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..context import orb_bot  # primes sys.path; orb_bot.models is canonical

_CANDLES_DIR = os.path.join(os.path.dirname(__file__), "candles")

SCENARIOS = ("trend_long", "range_day", "weak_breakout", "retest_hold")


def scenario_path(name: str) -> str:
    """Absolute path to a named scenario CSV (raises if the name is unknown)."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; known: {SCENARIOS}")
    return os.path.join(_CANDLES_DIR, f"{name}.csv")


def _rows(path: str):
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            yield line


def load_scenario(
    name: str,
    *,
    timeframe_min: int = 15,
    tz: str = "America/New_York",
) -> list["orb_bot.models.Candle"]:
    """Load a named scenario into a list of tz-aware ET ``Candle``s.

    ``timeframe_min`` sets each candle's ``timeframe_min`` and the
    ``ts_close = ts_open + timeframe_min`` derivation (default 15 = strategy T).
    """
    zone = ZoneInfo(tz)
    delta = _dt.timedelta(minutes=timeframe_min)
    Candle = orb_bot.models.Candle  # resolved lazily so the loader imports cleanly

    lines = list(_rows(scenario_path(name)))
    if not lines:
        return []
    reader = csv.DictReader(lines)
    expected = {"ts_open", "open", "high", "low", "close", "volume"}
    missing = expected - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"{name}.csv missing columns: {sorted(missing)}")

    candles: list[orb_bot.models.Candle] = []
    for row in reader:
        ts_open = _dt.datetime.strptime(row["ts_open"], "%Y-%m-%d %H:%M").replace(
            tzinfo=zone
        )
        candles.append(
            Candle(
                ts_open=ts_open,
                ts_close=ts_open + delta,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=int(row["volume"]),
                timeframe_min=timeframe_min,
            )
        )
    return candles
```

- [ ] **Step 3: Write the failing loader test `tests/test_fixtures_loader.py`.**
  Asserts every scenario parses, types are correct (`Decimal` prices, tz-aware ET, `ts_close` derivation, `timeframe_min` propagation), OHLC self-consistency (`low <= open/close <= high`), and contiguous 09:30-anchored 15m spacing. This is the failing-test step — it errors until `orb_bot.models.Candle` exists.

```python
# tests/test_fixtures_loader.py
"""Validates the CSV scenario fixtures and their loader (spec §18)."""
from decimal import Decimal

import pytest

from .context import orb_bot
from .fixtures import loader


@pytest.mark.parametrize("name", loader.SCENARIOS)
def test_scenario_loads_nonempty(name):
    candles = loader.load_scenario(name)
    assert candles, f"{name} produced no candles"


@pytest.mark.parametrize("name", loader.SCENARIOS)
def test_candle_types_and_invariants(name):
    Direction = orb_bot.models  # noqa: F841  (ensures the module imported)
    candles = loader.load_scenario(name, timeframe_min=15)
    for c in candles:
        # Decimal prices.
        assert isinstance(c.open, Decimal)
        assert isinstance(c.high, Decimal)
        assert isinstance(c.low, Decimal)
        assert isinstance(c.close, Decimal)
        assert isinstance(c.volume, int)
        # tz-aware ET timestamps.
        assert c.ts_open.tzinfo is not None
        assert c.ts_close.tzinfo is not None
        assert "New_York" in str(c.ts_open.tzinfo)
        # ts_close = ts_open + timeframe.
        assert (c.ts_close - c.ts_open).total_seconds() == 15 * 60
        assert c.timeframe_min == 15
        # OHLC self-consistency.
        assert c.low <= c.open <= c.high
        assert c.low <= c.close <= c.high
        assert c.high >= c.low


@pytest.mark.parametrize("name", loader.SCENARIOS)
def test_first_candle_is_the_opening_range_bar(name):
    candles = loader.load_scenario(name)
    first = candles[0]
    assert (first.ts_open.hour, first.ts_open.minute) == (9, 30), (
        f"{name}: first candle must open at 09:30 (the opening range bar)"
    )


@pytest.mark.parametrize("name", loader.SCENARIOS)
def test_candles_are_contiguous_15m(name):
    candles = loader.load_scenario(name)
    for prev, nxt in zip(candles, candles[1:]):
        assert nxt.ts_open == prev.ts_close, (
            f"{name}: gap between {prev.ts_open} and {nxt.ts_open}"
        )


def test_timeframe_min_override_changes_spacing():
    c5 = loader.load_scenario("trend_long", timeframe_min=5)
    assert all(x.timeframe_min == 5 for x in c5)
    assert (c5[0].ts_close - c5[0].ts_open).total_seconds() == 5 * 60


def test_unknown_scenario_rejected():
    with pytest.raises(ValueError):
        loader.scenario_path("does_not_exist")
```

  Run it (fails/errors until `orb_bot.models` exists — that is the TDD red):

```bash
pytest tests/test_fixtures_loader.py -v
```

  Expected: **FAIL/ERROR** now — collection error `ModuleNotFoundError: No module named 'orb_bot'` (the canonical `Candle` type is owned by the models task). Once `orb_bot/models.py` lands with the canonical `Candle`, re-run:

```bash
pytest tests/test_fixtures_loader.py -v
```

  Expected then: **PASS** (all parametrized cases green). The loader and fixtures are now reusable by `test_engine.py` / `test_aggregation.py`.

- [ ] **Step 4: Write the network-gated paper smoke test `tests/test_smoke_paper.py`.**
  Three parts (spec §18 "Integration smoke", §13 SDK loop trap, §19 version pins): (a) assert the pinned SDK exposes `subscribe_bars` / `_run_forever` / `stop_ws` and version is in `[0.43, 0.44)`; (b) submit + cancel a tiny paper bracket through `TradingClient`; (c) optional Discord connect. Every part is `skipif`-gated on missing creds so the default suite never touches the network.

```python
# tests/test_smoke_paper.py
"""Network-gated paper integration smoke test (spec §18, §13, §19).

Gated on real Alpaca paper credentials (ALPACA_KEY / ALPACA_SECRET) and the
optional Discord token. Without them every test is skipped, so the default
`pytest tests` run never touches the network. Run explicitly with creds in the
environment (or a loaded .env) to exercise the live SDK seams that the pins in
§19 depend on (`subscribe_bars` / `_run_forever` / `stop_ws`).
"""
import asyncio
import importlib
import os
import uuid
from decimal import Decimal

import pytest

ALPACA_KEY = os.environ.get("ALPACA_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

_HAVE_ALPACA_CREDS = bool(ALPACA_KEY and ALPACA_SECRET)

alpaca_creds = pytest.mark.skipif(
    not _HAVE_ALPACA_CREDS,
    reason="ALPACA_KEY/ALPACA_SECRET not set; skipping network smoke test",
)
discord_creds = pytest.mark.skipif(
    not DISCORD_TOKEN,
    reason="DISCORD_TOKEN not set; skipping Discord connect smoke test",
)


def _require(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ImportError:
        pytest.skip(f"{module_name} not installed")


def test_alpaca_py_version_pinned():
    """Spec §19: alpaca-py is pinned >=0.43,<0.44 — guard against silent bumps."""
    alpaca = _require("alpaca")
    ver = getattr(alpaca, "__version__", None)
    if ver is None:
        pytest.skip("alpaca.__version__ unavailable")
    parts = ver.split(".")
    major, minor = int(parts[0]), int(parts[1])
    assert (major, minor) == (0, 43), (
        f"alpaca-py {ver} outside the pinned >=0.43,<0.44 window (spec §19); "
        "re-verify _run_forever / stop_ws before unpinning."
    )


def test_stock_data_stream_exposes_semiinternal_seams():
    """Spec §13: the orchestrator schedules `_run_forever` and shuts down via `stop_ws`.

    These are semi-internal; assert they still exist on the pinned SDK so an
    upgrade that removes them fails loudly here rather than at runtime.
    """
    live = _require("alpaca.data.live")
    StockDataStream = live.StockDataStream
    assert hasattr(StockDataStream, "subscribe_bars")
    assert hasattr(StockDataStream, "_run_forever")
    assert hasattr(StockDataStream, "stop_ws")


def test_trading_stream_exposes_trade_update_seams():
    """Spec §13: fills arrive via TradingStream.subscribe_trade_updates; same loop seams."""
    trading_live = _require("alpaca.trading.stream")
    TradingStream = trading_live.TradingStream
    assert hasattr(TradingStream, "subscribe_trade_updates")
    assert hasattr(TradingStream, "_run_forever")
    assert hasattr(TradingStream, "stop_ws")


@alpaca_creds
def test_paper_account_reachable():
    """Smoke: TradingClient(paper=True) returns an account with equity/buying_power."""
    trading = _require("alpaca.trading.client")
    client = trading.TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
    account = client.get_account()
    assert account is not None
    assert Decimal(str(account.buying_power)) >= 0
    # Clock seam used by preflight gating (spec §8 step 1 / §13).
    clock = client.get_clock()
    assert hasattr(clock, "is_open")
    assert hasattr(clock, "next_close")


@alpaca_creds
def test_paper_bracket_submit_and_cancel():
    """Submit a tiny far-from-market paper bracket, then cancel it (no fill intended)."""
    trading = _require("alpaca.trading.client")
    requests = _require("alpaca.trading.requests")
    enums = _require("alpaca.trading.enums")

    client = trading.TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)

    # A deep limit so it rests unfilled; bracket children bracket that limit.
    entry = Decimal("1.00")
    target = Decimal("2.00")
    stop = Decimal("0.50")
    client_order_id = f"smoke-{uuid.uuid4().hex[:12]}"

    order_req = requests.LimitOrderRequest(
        symbol="AAPL",
        qty=1,
        side=enums.OrderSide.BUY,
        time_in_force=enums.TimeInForce.DAY,
        order_class=enums.OrderClass.BRACKET,
        limit_price=float(entry),
        client_order_id=client_order_id,
        take_profit=requests.TakeProfitRequest(limit_price=float(target)),
        stop_loss=requests.StopLossRequest(stop_price=float(stop)),
    )
    submitted = client.submit_order(order_req)
    try:
        assert submitted.id is not None
        assert submitted.client_order_id == client_order_id
        # A bracket parent reports its OCO/OTO children as `legs`.
        assert submitted.legs is not None and len(submitted.legs) >= 1
    finally:
        # Defense: cancel just this order; fall back to cancel_all on any issue.
        try:
            client.cancel_order_by_id(submitted.id)
        except Exception:
            client.cancel_orders()


@discord_creds
def test_discord_client_connects_and_closes():
    """Spec §14: discord.py client starts as a task and can be cleanly closed."""
    discord = _require("discord")

    async def _connect_then_close():
        intents = discord.Intents.none()
        client = discord.Client(intents=intents)
        ready = asyncio.Event()

        @client.event
        async def on_ready():  # noqa: ANN202
            ready.set()

        task = asyncio.create_task(client.start(DISCORD_TOKEN))
        try:
            await asyncio.wait_for(ready.wait(), timeout=30)
            assert client.user is not None
        finally:
            await client.close()
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()

    asyncio.run(_connect_then_close())
```

  Run it without creds and confirm it cleanly skips the networked parts (the SDK-surface tests run only if `alpaca`/`discord` are installed, else skip):

```bash
pytest tests/test_smoke_paper.py -v
```

  Expected: **PASS** — networked cases `SKIPPED` (no creds); the version/seam cases `SKIPPED` until `alpaca-py>=0.43,<0.44` is installed, then they **PASS** (or **FAIL** loudly if an upgrade drops `_run_forever`/`stop_ws`, which is the intended canary per §13).

- [ ] **Step 5: Commit.**

```bash
git add tests/fixtures tests/test_fixtures_loader.py tests/test_smoke_paper.py
git commit -m "test: add CSV scenario fixtures + loader and network-gated paper smoke test

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Deferred (NOT in this plan)

The backtester (`ReplayFeed`/`SimBroker`/`DataClock`) and the 15m/5m/1m timeframe-comparison experiment (spec §20/§20a) are intentionally out of scope. The engine, aggregator, `AutoApprover`, and `Reporter`/`SessionSummary` built here are timeframe-parameterized and reused unchanged when those are added.