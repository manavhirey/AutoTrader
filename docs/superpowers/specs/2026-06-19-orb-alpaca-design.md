# ORB → Alpaca Trading Bot — Design Spec

**Date:** 2026-06-19
**Status:** Approved for planning
**Source strategy:** [`orb_strategy_spec.md`](../../../orb_strategy_spec.md) (Opening Range Breakout, formal rule spec)

---

## 1. Overview & goals

Build a Python program that runs the ORB (Opening Range Breakout) strategy from
`orb_strategy_spec.md` against **Alpaca**. The bot trades a **single symbol per run** on a
**single 15-minute timeframe**, detecting setups with the spec's deterministic state machine
during the post-open trading window, and places an Alpaca **bracket (OCO) order** per setup.
(Alpaca streams only 1-minute bars; those are **transport only** — the orchestrator aggregates
them to 15m and the strategy never sees 1m.)
Approval is **mode-dependent**: in **paper** mode orders **auto-fire** (no gate); in **live**
mode each setup requires a **human approval** via Discord before the order is sent. After the
market closes, the bot posts an **end-of-session P/L report** (in both modes).

**Primary goal of the first build:** a correct, observable, real-time engine wired to
Alpaca **paper** trading, with live trading available behind an explicit config flag.

**Design north star:** the strategy logic (`engine`) is a **pure, I/O-free, clock-free**
state machine. Everything that touches the network, the wall clock, or the human lives
outside it. This makes the engine exhaustively unit-testable candle-by-candle and lets a
future backtester reuse the *identical* engine by swapping the data feed and broker.

---

## 2. Scope

**In scope (this build):**
- Real-time engine: stream 1-minute bars (transport) → aggregate to 15m → run the spec's state machine.
- Alpaca integration: paper by default, live behind a flag; IEX feed by default, SIP switchable.
- **Mode-dependent approval:** **paper auto-fires** (no human gate — `AutoApprover`); **live
  keeps the Discord human-approval gate** (standalone bot: its own token + channel + approver user).
- **End-of-session P/L report (both modes):** after market close, post a session summary
  (per-trade results + total realized profit/loss in $ and %) to Discord, and log it.
- Alpaca bracket/OCO order placement, fill tracking, and EOD flatten.
- Single symbol, single trading day per process run.
- Full unit/async test suite; network-gated paper smoke test.
- Clean Protocol seam (`DataFeed` / `Broker` / `Approver` / `Reporter` / `Clock`) for future reuse.

**Out of scope (deferred):**
- Backtester implementation (`ReplayFeed` / `SimBroker`). Only the interface seam is built now;
  the concrete doubles come later. (`AutoApprover` is now built — it is the paper-mode approver.)
- **Timeframe-comparison experiment (§20a):** a parallel sweep running the strategy at 15m/5m/1m
  and comparing results. Deferred — it builds on the backtester. The design keeps the engine and
  aggregator **timeframe-parameterized** now so this needs no engine changes later.
- Multi-symbol / portfolio operation. The engine is per-instrument; we keep config and
  interfaces single-symbol to minimize complexity now.
- Parameter optimization / strategy validation. The spec warns defaults are unvalidated;
  calibration happens after the backtester exists.
- 24/7 crypto (the spec's 09:30 NY session assumptions don't apply).

---

## 3. Locked decisions

From the brainstorming dialogue (2026-06-19):

| Decision | Choice |
|---|---|
| Execution target | **Both, config-switchable**; paper by default, live behind `run.live`. |
| Instruments | **Single symbol** per run. |
| Timeframe model | **Single, configurable timeframe `T` (default 15m).** Opening range = first bar; confirmation **and** entry on subsequent `T`-closes. The source's 5m-confirm / 1m-entry layers are collapsed to `T`. Alpaca's native 1m stream is **transport only** — aggregated to `T`; the strategy never sees 1m. Engine is timeframe-parameterized so the §20a backtest experiment can sweep `T ∈ {15m, 5m, 1m}`. |
| Approval gate | **Mode-dependent.** Paper: **auto-fire** (`AutoApprover`, no human gate). Live: **Discord** human gate — posts setup, awaits Approve/Reject. |
| Session reporting | **Both modes:** after market close, post realized **P/L report** (per-trade + session total $/%) to Discord + logs. Paper also posts a non-blocking "trade taken" notice. |
| Backtest scope | **Live/paper now**; keep the Protocol seam, defer the backtester (`AutoApprover` built now). |
| Data feed | **IEX free** by default, config-switchable to SIP. |
| Concurrency model | **Approach 1** — single-process `asyncio`, layered, pure engine. |
| Displacement model (default) | **IMPULSE** (FVG/TRUE_GAP implemented behind config). |
| Trade direction | **Long and short**; preflight checks `shorting_enabled` + shortability, warns/gates if unavailable. |
| Sizing equity | **Live account equity** per setup by default; fixed override via `equity_source="fixed"`. |
| Live feed gate | Live trading **hard-gated to SIP** unless `allow_live_iex=True`. Paper on IEX always allowed. |
| Runtime | One long-running process launched per trading day: `python -m orb_bot`. |

---

## 4. Architecture & repository layout

Single `asyncio` event loop, layered modules with one responsibility each. The **purity
boundary** is the central design rule. The repository follows the *Hitchhiker's Guide to
Python* — "Structuring Your Project" (docs.python-guide.org/writing/structure): a **flat
layout** with the package at the repository **root** (no `src/`), `tests/` and `docs/` as
siblings, a `Makefile` for management tasks, and `LICENSE` + packaging metadata at the top.

```
orb-strat/                     # repository root
  README.md                    # overview, setup, run instructions
  LICENSE                      # full license text (choosealicense.com) — at root
  pyproject.toml               # packaging + deps + tool config (modern equivalent of the guide's setup.py)
  requirements.txt             # dev/runtime deps mirror (guide convention; `make init`)
  Makefile                     # init / test / lint / run management tasks (.PHONY)
  .env.example                 # template for secrets; real .env is gitignored
  config/
    config.yaml                # strategy + run flags (committed; no secrets)
  orb_bot/                     # ← THE PACKAGE, at the repo root (not src/)
    __init__.py
    __main__.py                # `python -m orb_bot`: load config, build deps, run orchestrator
    config.py                  # pydantic: StrategyConfig (spec §1) + RunConfig + Secrets
    models.py                  # frozen dataclasses/enums: Candle, OpeningRange, Setup, events…
    interfaces.py              # Protocols: DataFeed, Broker, Approver, Reporter, Clock ← swap seam
    aggregation.py             # PURE: 1m transport bars → 15m candles, anchored to 09:30 buckets
    indicators.py              # PURE: Wilder ATR (15m), strong-close, displacement, fractal swings
    engine.py                  # PURE: the single-timeframe ORB state machine (T, default 15m; spec §3–§9) — reusable core
    orchestrator.py            # owns loop + wall clock + I/O reactions; accumulates SessionSummary
    discordbot.py              # shared discord.py client/connection (used by approval.manual + reporting.discord)
    logconf.py                 # structured JSON logging config, daily rotation
    feed/
      __init__.py
      alpaca.py                # DataFeed impl: StockDataStream 1m (IEX/SIP) → Candle queue (transport)
    execution/
      __init__.py
      alpaca.py                # Broker impl: TradingClient brackets + TradingStream fills + flatten
    approval/
      __init__.py
      auto.py                  # AutoApprover (PAPER): instant APPROVE, no gate (reused by backtest)
      manual.py                # DiscordApprover (LIVE): embed + Approve/Reject buttons via discordbot
    reporting/
      __init__.py
      discord.py               # DiscordReporter: "trade taken" notices + end-of-session P/L summary
      log.py                   # LogReporter: report to logs/console when Discord isn't configured
  docs/
    index.md
    superpowers/specs/2026-06-19-orb-alpaca-design.md   # this spec
  tests/
    __init__.py
    context.py                 # path shim (guide §Test Suite): inserts repo root, `import orb_bot`
    test_engine.py  test_aggregation.py  test_indicators.py  test_config.py
    test_orchestrator.py  test_reporting.py
    fixtures/candles/          # named CSV scenarios (double as future backtest regressions)
```

**Conventions adopted from the guide:**
- **Package at the repo root**, named after the project — not in an "ambiguous `src`" dir.
- **Test suite** in `tests/` with a `tests/context.py` shim (`sys.path` insert + `import orb_bot`);
  test modules import via `from .context import orb_bot`, so tests pass regardless of install
  method. (`pyproject` editable install also works; the shim is the guide's portable default.)
- **`Makefile`** holds generic tasks (`init` → `pip install -r requirements.txt`, `test` →
  `pytest tests`, plus `lint`/`run`), declared `.PHONY`.
- **Short, lowercase module names**, leaning on **subpackages instead of underscore suffixes**
  (e.g. `feed/alpaca.py`, `approval/manual.py` — not `alpaca_feed.py` / `discord_approver.py`).
  Two deliberate exceptions for clarity / to avoid shadowing top-level libraries or stdlib:
  `discordbot.py` (not a `discord` module/pkg that would mask the `discord` library) and
  `logconf.py` (not `logging.py`, which would mask stdlib `logging`). Absolute imports keep
  `orb_bot.reporting.discord` distinct from the `discord` library.
- **Explicit imports only** — never `from module import *`; prefer `import modu` / `modu.func()`.
- **`LICENSE`** at root; **`docs/`** for documentation (this spec lives there); **`pyproject.toml`**
  for packaging (the guide predates pyproject and shows `setup.py`; pyproject is its modern form).
- **No circular dependencies** — the layering below is a strict DAG (enforced by import-linter).

### Purity boundary (enforced)

- `models.py`, `aggregation.py`, `indicators.py`, `engine.py` import **nothing** from
  `feed` / `execution` / `approval` / `orchestrator`, and never call `datetime.now()`.
- The engine drives **all timing off `bar.ts_close`** (event time), never the host clock.
- Enforcement: an `import-linter` contract + a grep test asserting no `datetime.now()` /
  `time.time()` in the pure modules.

This is what guarantees deterministic replays and live/backtest parity.

---

## 5. Domain model (`models.py`)

Frozen dataclasses + enums; the shared vocabulary across layers. All prices are `Decimal`;
all timestamps are timezone-aware `America/New_York`.

```python
@dataclass(frozen=True)
class Candle:
    ts_open: datetime            # tz-aware ET; bar start
    ts_close: datetime           # ts_open + timeframe
    open: Decimal; high: Decimal; low: Decimal; close: Decimal
    volume: int
    timeframe_min: int           # 1 (transport, never reaches the engine) | T = range_timeframe_min (strategy; default 15)
    data_incomplete: bool = False  # set when the T-bucket had missing 1m children
    bars_present: int | None = None  # count of 1m children in the T-bucket (0–T)

class Direction(Enum): LONG; SHORT
class Model(Enum): BREAKOUT; RETEST; REVERSAL
class State(Enum): IDLE; BUILDING_RANGE; RANGE_SET; WAIT_CONFIRMATION; WAIT_ENTRY; IN_TRADE; DONE

@dataclass(frozen=True)
class OpeningRange:
    high: Decimal; low: Decimal; established_at: datetime
    width: Decimal; feed: str; bars_present: int; low_confidence: bool

@dataclass(frozen=True)
class Setup:
    direction: Direction; model: Model
    entry: Decimal; stop: Decimal; target: Decimal
    rr: float; reason: list[str]

# Built by the ORCHESTRATOR (NOT the engine) — carries live-account/runtime data the pure
# engine cannot know (equity-derived qty, risk $, run mode, feed, data warning):
@dataclass
class ApprovalRequest:
    setup: Setup; symbol: str; qty: int; risk_dollars: float
    mode: str            # 'PAPER' | 'LIVE'
    feed: str; or_high: Decimal; or_low: Decimal
    bars_present: int; data_warning: str | None
    approval_ttl_s: int  # = config approval_timeout_s, copied in by the orchestrator

# EngineEvent: a closed union (typing.Union of small frozen dataclasses) the engine returns
# from each call. The engine is PURE, so SetupProposed carries ONLY engine-knowable data
# (the Setup); the orchestrator builds the ApprovalRequest above from it + live account/config.
#   RangeEstablished, DirectionConfirmed, RangeDayDetected, SetupProposed(setup),
#   EntryConfirmed, TradeRecorded(pnl, exit_reason), WindowExpired, NoOp

@dataclass
class OrderResult: order_id; client_order_id; status; filled_avg_price; filled_qty; legs

# leg_role lets fill handling key off the bracket parent vs its TP/SL children by order id,
# instead of fragile fill ordering — robust across reconnects / out-of-order delivery (§16).
# Trades are grouped for P/L by the client_order_id PREFIX `{date}-{symbol}-{trade_seq}`
# shared across a trade's legs (suffix = leg_role); see §17a multi-trade pairing.
@dataclass
class Fill:
    order_id: str; client_order_id: str
    leg_role: str          # 'ENTRY' | 'TP' | 'SL' | 'FLATTEN'
    side: str; price: Decimal; qty: int; ts: datetime
    position_qty: int; exit_reason: str | None           # exit_reason None on entry

# Live values returned by Broker.get_account() / get_clock() (orchestrator reads these):
@dataclass(frozen=True)
class AccountSnapshot: equity: Decimal; buying_power: Decimal; shorting_enabled: bool
@dataclass(frozen=True)
class ClockInfo: is_open: bool; next_close: datetime

# Output of indicators.detect_displacement (used by the engine's breakout model):
@dataclass(frozen=True)
class Displacement: type: str; upper_candle: Candle; lower_candle: Candle; size: Decimal

# Accumulated by the orchestrator across the session; delivered by the Reporter after close.
@dataclass(frozen=True)
class TradeResult:
    direction: Direction; model: Model; qty: int
    entry_price: Decimal; exit_price: Decimal            # share-weighted avg of partial fills (§17a)
    pnl: Decimal; pnl_pct: Decimal                       # pnl_pct STORED as a FRACTION (pnl/start_equity); ×100 only at display
    exit_reason: str                                     # 'TARGET' | 'STOP' | 'FLATTEN'
@dataclass(frozen=True)
class SessionSummary:
    session_date: date; symbol: str; mode: str           # 'PAPER' | 'LIVE'
    trades: list[TradeResult]
    total_pnl: Decimal; total_pnl_pct: Decimal           # realized; total_pnl_pct = total_pnl/start_equity (fraction)
    wins: int; losses: int; breakevens: int              # pnl>0 / pnl<0 / pnl==0; wins+losses+breakevens == len(trades)
    start_equity: Decimal; end_equity: Decimal           # start_equity = live equity at preflight (ALWAYS, even if equity_source='fixed')
    no_trade_reason: str | None                          # set when trades == [] (mapping in §17a)
```

---

## 6. Interfaces (`interfaces.py`)

PEP-544 `Protocol`s. The **engine imports none of these** — only the orchestrator does.
This is the seam the future backtester drops into (`ReplayFeed` + `SimBroker` +
`AutoApprover` satisfy the same Protocols and feed the identical engine).

```python
class DataFeed(Protocol):
    def candles(self) -> AsyncIterator[Candle]: ...   # 1m candles (transport); orchestrator aggregates to 15m
    async def close(self) -> None: ...

class Broker(Protocol):
    async def get_account(self) -> AccountSnapshot: ...     # equity, buying_power, shorting_enabled
    async def get_clock(self) -> ClockInfo: ...             # is_open, next_close
    async def submit_bracket(self, setup: Setup, qty: int) -> OrderResult: ...
    def trade_updates(self) -> AsyncIterator[Fill]: ...     # pushed fills
    async def get_order(self, order_id: str) -> OrderResult: ...  # reconciliation fallback
    async def cancel_all(self) -> None: ...
    async def flatten(self) -> None: ...

class Approver(Protocol):                                       # AutoApprover (paper) | DiscordApprover (live)
    async def start(self) -> None: ...
    async def request(self, req: ApprovalRequest) -> str: ...   # 'APPROVE' | 'REJECT' | 'TIMEOUT'
    async def close(self) -> None: ...

class Reporter(Protocol):                                       # DiscordReporter | LogReporter
    async def start(self) -> None: ...
    async def trade_taken(self, setup: Setup, qty: int, mode: str) -> None: ...  # non-blocking notice
    async def session_report(self, summary: SessionSummary) -> None: ...         # post-close P/L
    async def close(self) -> None: ...

class Clock(Protocol):
    def now(self) -> datetime: ...    # tz-aware ET; used ONLY by the orchestrator
```

> **SDK adapter note (callbacks → async iterators).** `alpaca-py` delivers bars and trade
> updates via **registered async callbacks** (`subscribe_bars(handler, *symbols)`,
> `subscribe_trade_updates(handler)`), not async iterators. The `DataFeed.candles()` and
> `Broker.trade_updates()` async generators are therefore **adapters**: the SDK handler
> enqueues onto an internal `asyncio.Queue` and the generator drains it. This makes
> queue backpressure and duplicate-event idempotency (reconnects) explicit design points
> in the concrete impls; the backtest doubles produce the same async iterators directly.

---

## 7. Configuration (`config.py`)

Typed `pydantic` models. Strategy + run flags load from `config/config.yaml`; secrets load
from `.env` via environment variables. Validation runs at load time so a bad config fails
**before any network connection**.

**Secrets (`.env`, gitignored):**
`ALPACA_KEY`, `ALPACA_SECRET` (always required); `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID` (int),
`DISCORD_APPROVER_USER_ID` (int — only this user's button clicks count). **Discord is required
in live mode** (the approval gate); in **paper** it is optional — if omitted, the bot uses the
`LogReporter` and skips the trade/session Discord posts. The config validator enforces:
`run.live` requires all three Discord values.

**Run flags (`run.*`):**
`symbol` (required), `live` (default `False`), `feed` (`IEX`|`SIP`, default `IEX`),
`allow_live_iex` (default `False`).

**Strategy (`strategy.*`)** — mirrors spec §1, plus the parameters the spec left implicit:

```yaml
# session / timing
timezone: "America/New_York"
session_open: "09:30"
range_timeframe_min: 15       # the single strategy timeframe T (configurable; default 15, T ∈ {1,5,15})
confirm_timeframe_min: 15     # SINGLE-TIMEFRAME build: must == range (see §10 #14)
entry_timeframe_min: 15       # must == range; the §20a experiment sweeps all three together to 5 and 1
trading_window_min: 120       # = 8 fifteen-minute candles at T=15 (OR is candle #1)
flatten_at: "15:55"          # orchestrator uses min(flatten_at, next_close - flatten_buffer_min)
flatten_buffer_min: 5        # minutes before next_close to flatten (TIME offset; distinct from the price `buffer`)
bar_grace_seconds: 3         # delay after the 15m bucket boundary before force-closing the aggregate

# risk / sizing
risk_reward_ratio: 2.0
risk_per_trade_pct: 0.5
max_trades_per_day: 1
allow_long: true
allow_short: true
equity_source: "live"        # "live" | "fixed"
fixed_equity: null           # used when equity_source == "fixed"
rearm_opposite_only: true    # on re-arm (max_trades>1) allow only the opposite direction

# entry-model toggles
enable_breakout: true
enable_retest: true
enable_reversal: false       # DEFERRED in this build (see §10 #10); config validator rejects `true`

# strong close (spec §5)
strong_close_body_ratio: 0.60
strong_close_location: 0.70

# displacement
displacement_model: "IMPULSE"   # "IMPULSE" | "FVG" | "TRUE_GAP"  (spec default was FVG)
fvg_min_size_ticks: 2
impulse_atr_mult: 1.5

# retest  (candle counts are now 15m candles — retuned from the source's 1m basis; calibrate)
retest_tolerance_atr: 0.25
retest_max_wait_candles: 4    # 4×15m = 60 min; the source's 15 was 1m candles. Must fit the 8-candle window.
retest_confirm_body_ratio: 0.50

# stops / structure  (NEWLY NAMED — spec left "buffer" undefined)
tick_size: 0.01              # US equities >= $1
stop_buffer_atr: 0.10
stop_buffer_ticks: 2         # buffer = max(stop_buffer_ticks*tick_size, stop_buffer_atr*ATR)
min_stop_distance: 0.02      # floor on abs(entry-stop)
swing_fractal_k: 1           # 15m bars required EACH side for a fractal pivot (1 ⇒ 3-bar pivot; coarse 15m basis)
swing_lookback: 4            # entry-window buffer length in 15m candles; must be >= 2*swing_fractal_k + 1 (fits 8-candle window)

# day-type filter
range_day_sweep_both: true
range_day_disables: ["breakout", "retest"]
range_day_enables: []        # reversal DEFERRED, so a range day disables trading (no model re-enabled) this build
sweep_buffer: 0.0
require_higher_tf_bias: false   # DEFERRED (no higher-TF bias computed); validator rejects `true`

# ATR  (NEWLY NAMED atr_timeframe_min — spec left TF unspecified)
atr_period: 14
atr_timeframe_min: 15        # MUST == range_timeframe_min (T); validator-pinned so ATR always tracks the run's timeframe
or_min_bars: 15             # required 1m children in the opening T-bar (<= T); defaults to range_timeframe_min; below it ⇒ low_confidence

# approval
approval_timeout_s: 90       # View timeout; expiry => TIMEOUT (non-approval), never auto-approve

# logging
logging_level: "INFO"
logging_dir: "logs/"
```

**Validators (fail fast):** `flatten_at > session_open`; `flatten_buffer_min >= 0`;
`risk_reward_ratio > 0`; `0 < risk_per_trade_pct <= 100`; **`run.live and run.feed != "SIP"`
is rejected unless `run.allow_live_iex` is `True`**; **single-timeframe gate** —
`confirm_timeframe_min == entry_timeframe_min == range_timeframe_min` (the common value `T` is
**configurable, default 15**; multi-timeframe is deferred, §10 #14), `T ∈ {1, 5, 15}` and `T`
divides the session-open–anchored grid; `equity_source == "fixed"`
requires `fixed_equity`; `swing_lookback >= 2 * swing_fractal_k + 1`; `retest_max_wait_candles`
should fit the window (`<= trading_window_min / range_timeframe_min`);
`trading_window_min % range_timeframe_min == 0` (whole-candle window). **Timeframe-scaling gates
(so `T`-runs stay coherent):** `atr_timeframe_min == range_timeframe_min` (ATR always on the run's
timeframe) and `1 <= or_min_bars <= range_timeframe_min` (the opening `T`-bar holds at most `T`
one-minute children; `or_min_bars` defaults to `range_timeframe_min`). **Deferred-feature gates:**
reject `enable_reversal: true` and reject `require_higher_tf_bias: true` (neither is implemented
this build — see §10 #10); a non-empty `range_day_enables` is rejected unless every listed model
is implemented (so `["reversal"]` is rejected while reversal is deferred).

---

## 8. End-to-end data flow

```
preflight → stream 1m (transport) → aggregate → 15m → engine event → approval → bracket → fill → flatten/DONE
```

1. **Preflight (orchestrator, wall clock).** `broker.get_clock()`: if market closed /
   holiday / half-day past window, exit `DONE`. Compute
   `flatten_at = min(config flatten_at, next_close − flatten_buffer_min)`. Seed `ATR(14)` on
   **`T`-minute** bars (`= range_timeframe_min`, 15m in this build) via historical REST (same feed
   as live); gate trading on `atr_ready`.
   **Establish the opening range (deterministic, event-time cutoff):** if the process starts
   **before the first 09:30 1m bar**, accumulate the range live from the stream (preferred —
   avoids IEX's ~15-min historical withholding); if it starts after the OR window has fully
   closed **and** REST can serve it (`now ≥ OR_close + IEX_withhold`, ≈ 10:00 on IEX),
   REST-backfill 09:30–`OR_close`; in the in-between window (range partially observed but not
   yet REST-available), go `IDLE → DONE` with a logged reason — **never trade a
   partially-observed or fabricated range**. (Restart-into-open-position handling: §16.)
2. **Stream.** `AlpacaFeed.candles()` yields 1-minute `Candle`s (`ts_open` = Alpaca bar
   start ET, `ts_close = ts_open + 1m`). A 09:30 bar arrives ~09:31.
3. **Aggregate (pure).** Each 1m transport candle feeds a **single 15m `Aggregator`** **anchored
   at 09:30 wall-clock buckets** (not bar-count). A bucket closes and emits when the next
   bucket's first 1m bar arrives **or** the orchestrator's boundary timer fires
   (`bar_grace_seconds` after the boundary) — whichever first — so zero-trade IEX minutes
   never stall the machine. Emitted `T`-candles carry `bars_present` (0–`T`) + `data_incomplete`.
   **Only `T`-candles (15m in this build) reach the engine.**
4. **Engine transitions (pure, time = `bar.ts_close`; every candle is `T`, 15m here).** `IDLE →
   BUILDING_RANGE` at session open. On the first `T`-candle whose `ts_open == 09:30`, set `OpeningRange`
   (write-once; records `feed`, `bars_present`, `low_confidence` if `bars_present < or_min_bars`)
   → `RANGE_SET → WAIT_CONFIRMATION`. In `WAIT_CONFIRMATION`, on each subsequent **15m close**, in
   this fixed order: `track_sweeps` → `is_range_day` (latch + `apply_day_type_filter` if both
   sides swept) → `confirmed_breakout` (close-based strong close beyond range) → set `direction`
   + `break_level` → `WAIT_ENTRY`. (A detected range day suppresses a same-bar breakout.)
5. **Setup (same timeframe `T`).** Each `T`-candle is buffered (`entry_window`,
   N = `swing_lookback` `T`-candles) and `try_build_entry` runs the enabled models. **Because
   confirm and entry share the timeframe `T`, the confirmation candle is itself eligible for a
   same-candle breakout entry** (enter at its close if displacement is present); the **retest**
   model (preferred) instead waits for a *later* 15m candle to return to `break_level` and hold.
   (Reversal deferred — §10 #10.) `validate_setup` enforces correct stop/target sides, realized
   `rr ≥ risk_reward_ratio − EPS`, and `abs(entry−stop) ≥ min_stop_distance`; failure → reasons
   logged → stay `WAIT_ENTRY`. A valid `Setup` → `SetupProposed` event.
6. **Sizing + approval (orchestrator).** The engine emitted only `SetupProposed(setup)`; the
   **orchestrator** now builds the full `ApprovalRequest`. `equity` = live account equity when
   `equity_source == "live"`, else `fixed_equity`;
   `qty = floor((equity × risk_per_trade_pct / 100) / abs(entry−stop))` (the `/100` reifies the
   source's `account * risk_per_trade_pct` as a *percentage* — see §10 #11); reject if `qty < 1`
   ("risk too small for one whole share"). Assemble `ApprovalRequest(...)`. **Re-validate
   entry/stop/target against the latest 1m transport bar** (freshest price within the just-closed
   15m candle; price may have run); abort if entry is no longer reachable within tolerance. Then `decision = await approver.request(req)`, where the
   approver is **mode-injected**: in **paper** the `AutoApprover` returns `APPROVE` immediately
   (no human gate); in **live** the `DiscordApprover` posts the embed + buttons and awaits your
   decision (`APPROVE`/`REJECT`/`TIMEOUT`). The flow below is identical for both.
7. **Engine feedback.** `engine.on_approval(decision)`. `REJECT`/`TIMEOUT` (live only) → back
   to `WAIT_ENTRY`, **no trade slot consumed** (with a short re-submit cooldown to avoid
   re-proposing the same breakout immediately). `APPROVE` → `broker.submit_bracket(setup, qty)`:
   `MarketOrderRequest(order_class=BRACKET, time_in_force=DAY, take_profit.limit_price=target,
   stop_loss.stop_price=stop)`, whole-share qty. The orchestrator then fires a non-blocking
   `reporter.trade_taken(setup, qty, mode)` notice (useful in paper, where there was no gate).
8. **Fill tracking.** `broker.trade_updates()` (`TradingStream` push, separate endpoint from
   the data ws so no contention) yields `Fill`s keyed by `order_id`/`leg_role`. The **entry**
   leg's fill → `engine.on_entry_filled` → `IN_TRADE`, and **this is where `trades_remaining`
   decrements** (the trade slot is consumed when the entry fills, matching source §9). The
   bracket's TP/SL legs are an Alpaca-managed OCO: when one fills the other auto-cancels → the
   exit `fill` → `engine.on_trade_closed` records the result and triggers re-arm **without
   re-decrementing**. `get_order` polling is a reconciliation fallback only after reconnects.
9. **Flatten / DONE.** At `flatten_at`, `broker.cancel_all()` then `broker.flatten()`
   (`close_all_positions(cancel_orders=True)`, idempotent) so a resting bracket child can't
   block the close. **The flatten close order is tagged with a deterministic `client_order_id`
   (leg_role `FLATTEN`) that the orchestrator records before calling `close_all_positions`**, so
   its fill is attributable. Because flatten cancels the TP/SL legs (the only fills that trigger
   `engine.on_trade_closed`), a flatten-closed trade would otherwise produce no `TradeResult`:
   the **orchestrator synthesizes the `FLATTEN` `TradeResult` directly** from the tracked entry
   fill + the recognized flatten close fill (exit_reason `FLATTEN`), independent of the engine's
   OCO-only path. If the flatten push fill is missed, reconcile via `get_position() == 0` at
   `next_close`. `trades_remaining == 0` or window expired or flattened → `DONE`. **Re-arm**
   (only if `max_trades_per_day > 1` and within window): `on_trade_closed` → `WAIT_CONFIRMATION`
   (range/ATR/sweep/range-day latches carry over; direction/break_level/retest counters reset;
   opposite direction only when `rearm_opposite_only`).
10. **Session report (both modes).** Each `TradeRecorded(pnl, exit_reason)` (engine) +
    the matching entry/exit `Fill`s are accumulated by the orchestrator into a `SessionSummary`
    (per-trade `TradeResult`s, total realized P/L in $ and % of start equity, win/loss counts;
    `no_trade_reason` if nothing traded). At **market close** (`next_close`, after flatten and
    final-fill reconciliation), the orchestrator calls `reporter.session_report(summary)` —
    posting the P/L summary to Discord and logging it — then drains logs and exits. See §17a.

---

## 9. The engine state machine (`engine.py`)

Implements spec §3–§9 as a deterministic function of bar events. Holds a `Context`
(state, range, direction, break_level, swept_high/low, range_day latch, retest_wait/dead,
trades_remaining, entry_window buffer, atr). **Zero I/O, no `await`, no logging.**

```python
class Engine:
    def __init__(self, cfg: StrategyConfig, session_date: date): ...
    def seed_atr(self, hist_tf: list[Candle]) -> None: ...      # ATR on the strategy timeframe T (== range_timeframe_min)
    def on_candle(self, c: Candle) -> list[EngineEvent]: ...   # only 15m candles reach here
    def on_approval(self, decision: str) -> list[EngineEvent]: ...
    def on_entry_filled(self, res: OrderResult) -> list[EngineEvent]: ...
    def on_trade_closed(self, fill: Fill) -> list[EngineEvent]: ...
    def is_done(self) -> bool: ...
```

This is a **single-timeframe** engine: every `Candle` it receives is the strategy timeframe `T`
(15m in this build), so `on_candle` does not dispatch by timeframe — each call advances
range-building, confirmation, and entry on the one `T`-clock. (The orchestrator does the 1m→`T`
aggregation; the engine never sees 1m. `T` is config, not hardcoded — §10 #14.)

Internal pure helpers mirror the spec: `confirmed_breakout`, `is_strong_close`,
`try_build_entry`, `entry_breakout` / `entry_retest`, `project_target`, `track_sweeps`,
`is_range_day`, `apply_day_type_filter`, `validate_setup`. (`entry_reversal` and its
`failed_breakout` / `failed_direction` / HOD-LOD context are **deferred** with the reversal
model — see §10 #10.) `is_first_session_candle := (tf == range_timeframe_min and ts_open ==
session_open of session_date)`.

**Entry-model eligibility in `WAIT_ENTRY` (resolves the source's undefined `breakout_armed`).**
There is no separate "armed" flag. On each **15m** candle in `WAIT_ENTRY`, `try_build_entry`
tries the enabled models in priority order: **breakout** is eligible on every candle —
**including the confirmation candle itself** (same-timeframe), since confirm and entry are both
15m — but only fires once a displacement is detected in the entry window (no displacement → no
breakout entry); **retest** (preferred) waits for a *later* 15m candle that returns to
`break_level` and holds, eligible each candle until the retest wait cap. The first model to
return a valid `Setup` wins; otherwise the engine stays in `WAIT_ENTRY` and the retest wait
counter advances by one 15m candle (§10 #3). If `find_swing` returns `None` for a retest stop,
fall back to `break_level ± buffer` (§10 #12).

### Guardrails (spec §9), enforced in the engine

- Never act before `session_open` or after `trading_window_min` (event-time check).
- At most `max_trades_per_day`; **decrement on entry fill** (`on_entry_filled`) — the single
  point a slot is consumed (matches §8 step 8). Approval/rejection alone consumes nothing.
- One opening range per day — **write-once**; never recompute after `RANGE_SET`.
- Every setup goes through an `Approver` (structural: the engine only emits a proposal; only
  the orchestrator places an order, and only after the approver returns `APPROVE`). The gate is
  **mode-dependent** — **live requires human approval** (`DiscordApprover`); **paper auto-fires**
  (`AutoApprover`). This deliberately overrides the source spec's "no auto-fire" invariant for
  paper only (no real money at risk); live retains the hard human gate.
- Confirmation requires a **close**, never an intrabar touch/wick.
- Reject any setup where `rr < risk_reward_ratio − EPS` (the `EPS` slack absorbs `Decimal`
  rounding; same threshold used in §8 step 5) or entry/stop are inverted.

---

## 10. Spec-ambiguity resolutions

The source spec is intentionally qualitative in places. Each is reified into a deterministic
rule here (this section is the authoritative record of *why* the code deviates from a naive
reading of the spec):

1. **`buffer` (undefined in spec)** → `buffer = max(stop_buffer_ticks × tick_size,
   stop_buffer_atr × ATR)`.
2. **ATR timeframe (unspecified)** → computed on the **strategy timeframe `T`** (`atr_timeframe_min`
   is **validator-pinned to `range_timeframe_min`**, default 15m), seeded from REST `T`-minute bars
   before trading; trading gated on `atr_ready`. *(Confidence: medium — a design choice. Pinning to
   `T` keeps the §20a comparison apples-to-apples: only the interval varies, not the ATR basis.)*
3. **Retest abandonment bug.** The spec checks `within(level, …)` *before* the wait cap, so
   a price that never returns never increments the counter and never abandons. **Fix:** every
   `WAIT_ENTRY` entry-TF candle counts toward `retest_max_wait_candles` regardless of proximity.
4. **Same-bar precedence + range-day effect.** Fixed order on each confirm-TF close:
   `track_sweeps → is_range_day/apply_day_type_filter (latched) → confirmed_breakout`. Once a
   range day latches, `apply_day_type_filter` disables `breakout` + `retest`; with reversal
   deferred (`range_day_enables: []`) **no model is re-enabled, so the day produces no entry** —
   a same-bar or any later confirmation yields no tradeable setup. The filter does **not** cancel
   an already-approved/in-flight setup. Without this fixed order, replays would be non-deterministic.
5. **Whole-share sizing is mandatory.** Alpaca **rejects fractional/notional qty on bracket
   orders** (HTTP 400). Sizing floors to whole shares; sub-1-share setups are rejected. Hard
   constraint, not a tweak.
6. **Write-once range + closed-bar immutability.** Late "updated bar" (`u`) corrections apply
   only while a bucket is still open; after a bucket closes (and especially after `RANGE_SET`),
   they are a no-op + WARN log (an IEX-accuracy canary).
7. **Range-day detection for a single symbol** uses `swept_high AND swept_low` within the
   trading window (with `sweep_buffer` tolerance); the latch, once set, persists.
8. **Displacement default = IMPULSE** (not the spec's FVG) because FVGs on the partial 1m IEX
   tape are noisy; FVG/TRUE_GAP remain available via config. *(Confidence: medium.)*
9. **IEX accuracy is surfaced, never hidden:** `OpeningRange.low_confidence` when
   `bars_present < or_min_bars`; the Discord approval payload always shows feed +
   `bars_present/T` + a partial-tape warning.
10. **Reversal model + higher-TF bias are DEFERRED this build.** The source reversal model
    (§6c) needs `failed_breakout` / `broke_structure_back` / `failed_direction` /
    `high_of_day` / `low_of_day` context and a structural target whose interaction with the
    `rr` floor is unresolved; `require_higher_tf_bias` (§7) needs an HTF-draw computation that
    doesn't exist yet. Both are scoped out: `enable_reversal` and `require_higher_tf_bias`
    default `false` and the config validator **rejects `true`**, and `range_day_enables`
    defaults to `[]`. This also removes the `breakout_armed` ambiguity (see §9 eligibility).
11. **`risk_per_trade_pct` is a percentage.** Sizing uses
    `equity × risk_per_trade_pct / 100` (so `0.5` ⇒ 0.5% of equity), reifying the source's
    ambiguous `account * risk_per_trade_pct`.
12. **Swing-pivot window vs fractal-k + None fallback.** A fractal pivot with `swing_fractal_k`
    needs `k` bars on each side, so the entry-window buffer is sized `swing_lookback ≥
    2·swing_fractal_k + 1` (default 4 for k=1 on the coarse 15m basis). If `find_swing` still
    returns `None` when the preferred retest model needs a structural stop, fall back to
    `break_level ± buffer` (logged), so the preferred path never silently dead-ends.
13. **Zero-child force-close vs write-once range.** A timer `force_close` of the 09:30 15m
    bucket with `bars_present == 0` carries no usable OHLC; the engine **refuses to establish
    `OpeningRange`** from it and goes `IDLE → DONE` ("never fabricate a range"). This is
    distinct from a partial-but-nonzero range candle, which **does** set the range with
    `low_confidence = True`.
14. **Single timeframe (collapse of the source's 15m/5m/1m).** This build runs **one** timeframe:
    the opening range is the first bar, and confirmation **and** entry both occur on subsequent
    closes of that *same* timeframe (the source's separate 5m-confirm / 1m-entry layers are
    merged — the confirmation candle is itself eligible for a breakout entry, §9). The timeframe
    is **configurable** (`range_timeframe_min == confirm_timeframe_min == entry_timeframe_min`,
    **default 15**); the validator enforces the three-way equality, not a literal 15, so the
    engine is timeframe-parameterized — exactly what the deferred 15m/5m/1m comparison experiment
    (§20a) sweeps. Multi-timeframe operation (genuinely different confirm/entry TFs) is deferred.
    **Candle-count parameters are in units of the chosen timeframe** (`retest_max_wait_candles`,
    `swing_lookback`), so they are retuned per timeframe — at 15m the 120-min window holds only
    ~8 candles, so defaults were lowered (`retest_max_wait_candles 15→4`, `swing_lookback 12→4`,
    `swing_fractal_k 2→1`) and must be recalibrated for 5m/1m runs.

---

## 11. Aggregation (`aggregation.py`, pure)

A **single** `Aggregator` rolls the 1m transport stream up to the strategy timeframe (15m in
this build). It is **parameterized by `tf_min`**, so the same code produces 5m or 1m
(pass-through) candles when the §20a experiment runs other timeframes — no per-timeframe code.

```python
def bucket_start(ts, tf_min, anchor=time(9, 30)) -> datetime   # base + floor((ts-base)/tf)*tf
class Aggregator:
    def __init__(self, tf_min: int, anchor): ...        # tf_min = strategy timeframe (default 15)
    def add(self, one_min: Candle) -> Candle | None     # emits closed aggregate when bucket advances
    def force_close(self, boundary_ts) -> Candle | None # timer-driven; data_incomplete if 0 children
```

`open` = first child open, `close` = last child close, `high`/`low` = max/min over children.
Idempotent on duplicate 1m bars (reconnect-safe). Carries `bars_present` + `data_incomplete`
onto the emitted 15m candle. The orchestrator fires `force_close` on a boundary timer
(`bar_grace_seconds` after the wall-clock bucket edge) so missing IEX minutes can't stall. A
`force_close` with `bars_present == 0` emits a candle with no usable OHLC (`data_incomplete=True`)
and the engine must refuse to establish the opening range from it (§10 #13).

---

## 12. Indicators (`indicators.py`, pure)

```python
class ATR:                       # Wilder: TR=max(h-l,|h-prevC|,|l-prevC|); ATRn=(ATRn-1*(p-1)+TR)/p
    def seed(self, bars): ...; def update(self, bar) -> Decimal; ready: bool; value: Decimal
def is_strong_close(c, dir, body_ratio, location) -> bool        # spec §5
def detect_displacement(window, model, atr, cfg) -> Displacement | None
def find_impulse(window, atr, mult) -> Displacement | None       # single candle range >= atr*mult + strong close
def find_fvg(window, min_size_ticks, tick) -> Displacement | None# 3-candle imbalance
def within(c, level, tol) -> bool                                # c.low <= level+tol and c.high >= level-tol
def find_swing(candles, k, lookback, kind) -> Decimal | None     # fractal pivot for stop placement
```

All operate on **closed candles only**; never call `now()`.

---

## 13. Execution / Alpaca integration (`orb_bot/execution/alpaca.py`)

`alpaca-py` SDK. `TradingClient(key, secret, paper=not run.live)`. Every **sync**
`TradingClient` call is wrapped in `asyncio.to_thread` so it never blocks the loop.

- **Account:** `get_account()` → equity, buying_power, `shorting_enabled` (preflight checks
  this when `allow_short`).
- **Clock:** `get_clock()` → `is_open`, `next_close` (drives gating + flatten timing; never
  hardcode 09:30–16:00; also consult the calendar for half-days).
- **Bracket:** `MarketOrderRequest(symbol, qty, side=BUY/SELL, time_in_force=DAY,
  order_class=OrderClass.BRACKET, take_profit=TakeProfitRequest(limit_price=target),
  stop_loss=StopLossRequest(stop_price=stop))`. LONG: TP above / SL below entry.
  SHORT: side=SELL, TP below / SL above entry (requires `shorting_enabled` + shortable symbol).
- **Fills:** `TradingStream.subscribe_trade_updates(handler)` (callback push), matching
  `TradeEvent` `fill` / `partial_fill` / `canceled` / `rejected`. The async handler **enqueues
  onto an internal `asyncio.Queue`** that `trade_updates()` drains as its async iterator (§6
  adapter note); each emitted `Fill` carries `order_id` + `leg_role` so the orchestrator
  attributes entry-vs-exit by order id, not arrival order. Separate endpoint from the data ws.
- **Flatten:** `cancel_all()` (cancel resting bracket children) then
  `close_all_positions(cancel_orders=True)` (idempotent).

**SDK loop trap (isolated here):** `StockDataStream.run()` / `TradingStream.run()` call
`asyncio.run()` and block — we instead schedule `stream._run_forever()` as a task on the
orchestrator's loop. **Shutdown:** `await stream.stop_ws()` then cancel the `_run_forever`
task; do **not** call the sync `stream.stop()` (it is meant for cross-thread use and
misbehaves against the same loop). `_run_forever`/`stop_ws` are semi-internal: **pin
`alpaca-py >=0.43,<0.44`**, isolate them in feed/broker, and assert their existence in the
paper smoke test on every upgrade.

---

## 14. Approval gate (`orb_bot/approval/`) + Discord client (`orb_bot/discordbot.py`)

Two `Approver` implementations; the orchestrator injects one by mode:

- **Paper → `AutoApprover`** (`approval/auto.py`): `request(req)` returns `APPROVE` immediately —
  no human gate, no Discord round-trip. (The same class is later reused as the backtest
  auto-approver.) Orders auto-fire; visibility comes from the `reporter.trade_taken` notice
  (§17a) and the end-of-session report.
- **Live → `DiscordApprover`** (`approval/manual.py`): the human gate, described below.

A single `discord.py` client (`discordbot.py`) is started with `bot.start(token)` **as an
asyncio task** (not `bot.run()`, which blocks) on the same loop as the Alpaca stream, and is
**shared** by the `DiscordApprover` (`approval/manual.py`, live) and the `DiscordReporter`
(`reporting/discord.py`, both modes). It is needed whenever Discord is configured — in paper
mode for reporting even though approval is automatic.

**`DiscordApprover` (live):**

- **Buttons, not message-reply:** a `discord.ui.View` with green **Approve** / red **Reject**
  buttons → **no privileged `message_content` intent** needed. `interaction_check` allows only
  `DISCORD_APPROVER_USER_ID`.
- `request(req)` posts a rich embed (symbol, direction, model, entry/stop/target, RR, risk $,
  qty, reasons, **feed + `bars_present/T` + IEX warning + PAPER/LIVE mode**), creates an
  `asyncio.Future`, and `await`s it.
- **Button callback must acknowledge the interaction within Discord's ~3s window.** Resolving
  the in-process `asyncio.Future` is invisible to Discord and is **not** an ack — without an
  HTTP ack the user sees "This interaction failed." So the callback first calls
  `await interaction.response.edit_message(content="✅ Approved"/"❌ Rejected", view=None)`
  (this both acks *and* disables the buttons in one HTTP call), **then** resolves the Future
  with the decision, guarded by `if not fut.done()`. (If order placement ever moves into the
  callback, `await interaction.response.defer()` first, then `followup.send()` after.)
- **Timeout/unauthorized/error → non-approval** (`TIMEOUT`/`REJECT`). **Never auto-approve.**
  The orchestrator does the (slower) order placement after `request()` returns `APPROVE`.

---

## 15. Orchestrator & lifecycle (`orchestrator.py`)

Owns the single loop, the `Clock`, **all wall-clock timing** (session gating, bucket-boundary
timers, flatten scheduling), and the I/O reactions to engine events. The engine stays pure;
the orchestrator logs every emitted event.

```python
class Orchestrator:
    def __init__(self, settings, feed, broker, approver, reporter, clock, engine): ...
    async def run(self):
        await self._preflight()                 # clock gate, capture start_equity, seed ATR, establish/backfill OR
        start approver + reporter + trade_updates tasks
        schedule flatten timer
        async for c1m in feed.candles():
            for tf_candle in self._aggregate(c1m):      # 1m → 15m (single TF), plus force_close on boundary timer
                for ev in engine.on_candle(tf_candle):
                    await self._react(ev)
    async def _react(self, ev):
        # SetupProposed → qty=sizing(equity,setup); re-validate; decision=await approver.request(req)
        #   → engine.on_approval(decision); if APPROVE → broker.submit_bracket(setup, qty)
        #                                              → await reporter.trade_taken(setup, qty, mode)
    def _sizing(self, equity, setup) -> int: floor((equity*risk_pct/100)/abs(entry-stop)) or reject<1
    async def _flatten_eod(self): await broker.cancel_all(); await broker.flatten()  # tags FLATTEN client_order_id
```

**Session report + shutdown:** on a normal session end (`DONE` at/after `next_close`), the
orchestrator builds the `SessionSummary` and calls `reporter.session_report(summary)` (§17a)
before tearing down. `SIGINT`/`SIGTERM` set a stop `Event`; `finally` cancels the feed task and
awaits `feed.close()` / `approver.close()` / `reporter.close()`, then drains logs. **Normal
`flatten_at` flatten happens during operation; manual Ctrl-C does NOT auto-flatten** (leave the
bracket stop/target to manage the position). On an early manual kill, a **partial** session
report (trades so far, realized-only) is emitted **only if preflight already captured
`start_equity`**; a SIGINT before preflight skips the report (the required `start_equity` /
`end_equity` are unset). When the book isn't flat at report time (Ctrl-C with an open position),
the report is labeled realized-only and the equity cross-check is skipped (§17a).

---

## 16. Error handling & safety

- **Approval (live) is absolute:** in live mode, timeout / unauthorized clicker / any error →
  non-approval; never auto-approve. **Paper auto-fires by design** (no real money) — but order
  placement is still mode-uniform (orchestrator only submits on `APPROVE`, which `AutoApprover`
  returns instantly), so the placement path is identical and equally testable.
- **Live guard:** `paper=True` by default; live requires explicit `run.live` **and**
  (`feed=SIP` or `allow_live_iex`). Mode shown in every embed/report and logged at startup.
- **`alpaca-py` loop trap & sync-blocking:** scheduled coroutine + `asyncio.to_thread`
  everywhere; pinned version; smoke-tested.
- **Missing/zero-trade IEX minutes:** boundary-timer aggregation (never count-driven) → can't stall.
- **Updated-bar (`u`) race:** apply only while the bucket is open; ignore after close; range is write-once.
- **Fractional/notional vs bracket:** whole-share qty; reject `qty < 1`; defense-in-depth
  re-check in the broker before submit.
- **EOD flatten ordering:** `cancel_all()` before `flatten()`; `close_all_positions(cancel_orders=True)` idempotent.
- **Market-hours gating:** driven off `get_clock().is_open` + `next_close` + calendar; never hardcoded.
- **Rate limits (200/min free):** prefer push streams over polling; cache `get_clock`/`get_account`; backoff on 429.
- **Order rejection / partial fill:** `rejected` → log + back to `WAIT_ENTRY` (no slot consumed);
  `partial_fill` → track `position_qty`, only `IN_TRADE` on full **entry** fill. **Exit legs
  (TP/SL/FLATTEN) can also partial-fill:** the P/L builder aggregates per leg —
  `exit_price` = share-weighted average of the leg's partial fills, `exit_qty` = summed
  `filled_qty` (mirroring Alpaca's `filled_avg_price`); assert `entry_qty == exit_qty` or compute
  on `min(entry_qty, exit_qty)` and surface a reconciliation warning (§17a).
- **Reconnects (websocket):** SDK auto-reconnects; on reconnect run reconciliation (`get_order`
  + position) to resync; idempotent transitions tolerate duplicate events.
- **Mid-session process restart (crash/OOM/manual relaunch).** `trades_remaining` and engine
  state are in-memory, but a position or resting bracket may already be live on Alpaca, so a
  naive relaunch could re-establish the range, re-propose, and breach `max_trades_per_day` or
  open a second position. **Policy:** at startup, after preflight, query account + open
  positions + open orders for the symbol. If an open position or resting bracket exists,
  **reconcile into `IN_TRADE`** (adopt the live bracket, treat the slot as consumed, monitor to
  exit/flatten) and **skip range/confirmation** for the day; if a clean range can no longer be
  established (past the §8 step 1 cutoff), go `IDLE → DONE`. The bot never starts a fresh trade
  cycle on top of a pre-existing position. (Each bracket leg uses a deterministic
  `client_order_id` so reconciliation can match legs after a restart.)
- **Stale approval price:** re-validate against the latest 1m bar at approval time; abort if price ran past entry.
- **DST / tz:** `zoneinfo America/New_York` everywhere; engine uses `bar.ts_close` (host-clock-independent).

---

## 17. Logging & observability (`orb_bot/logconf.py`)

Structured JSON, one line per event, with a per-run correlation id (`date+symbol`), rotating
daily (`logs/orb_YYYY-MM-DD.log`) plus console. Config snapshot + resolved paper/live + feed
logged at startup. Event keys: `state_transition`, `setup_proposed`, `approval`
(decision, approver, latency), `order_submitted`, `fill`, `trade_closed` (pnl, exit_reason),
`data_warning` (iex_partial, bars_present), `heartbeat` (DEBUG), `trade_taken`,
`session_report` (the full P/L summary, logged in both modes regardless of Discord).

---

## 17a. Session reporting (`orb_bot/reporting/`)

Two `Reporter` implementations; the orchestrator injects `DiscordReporter` (`reporting/discord.py`)
when Discord is configured, else `LogReporter` (`reporting/log.py`, logs/console only). Both run
in **paper and live**.

- **`trade_taken(setup, qty, mode)`** — a non-blocking notice posted when an order is submitted
  (entry/stop/target/qty/direction/model + PAPER/LIVE). Primarily for paper, where there was no
  approval prompt to signal activity.
- **`session_report(summary)`** — the end-of-session **P/L report**, posted after market close.

**Building the `SessionSummary` (orchestrator).** Realized P/L is computed from the tracked
`Fill`s per trade: `pnl = (exit_price − entry_price) × qty × (LONG ? +1 : −1)` (paper fills
incur no commission); `pnl_pct = pnl / start_equity` (stored as a **fraction**; ×100 only at
display). Per-trade exit prices are **share-weighted averages** of that leg's partial fills with
`qty = min(entry_qty, exit_qty)` (§16). The orchestrator builds a `TradeResult` for **every**
closed trade, from two sources: (a) OCO TP/SL exits via `engine.on_trade_closed`, and (b) the
**FLATTEN** close it synthesizes itself (§8 step 9) — both are summed into `total_pnl` /
`total_pnl_pct`. Trades are classified `pnl>0 → win`, `pnl<0 → loss`, `pnl==0 → breakeven`
(so `wins+losses+breakevens == len(trades)`).

**Trade grouping (multi-trade / re-arm).** A bracket's entry parent and TP/SL children have
different `order_id`s, so fills are paired into one trade by their **`client_order_id` prefix**
`{date}-{symbol}-{trade_seq}` (leg suffix = `leg_role`), the same deterministic scheme used for
restart reconciliation (§16). The builder groups all fills by that prefix to match entry↔exit.

**Equity & cross-check.** `start_equity` = live `get_account().equity` captured at preflight
**always** (independent of `equity_source`; `fixed_equity` affects *sizing* only), and is the
denominator for all `pnl_pct`. `end_equity` = `get_account().equity` after close. The identity
`start_equity + total_pnl ≈ end_equity` is asserted **only when the book is flat at report time**
(`get_position() == 0`); on the Ctrl-C/no-flatten path the position may be open, so `end_equity`
includes unrealized P/L while `total_pnl` is realized-only — there the cross-check is skipped and
the report is labeled **realized-only**. (`get_portfolio_history` is logged as a non-authoritative
secondary check.)

**No-trade reason.** When `trades == []`, the orchestrator sets `no_trade_reason` from the last
terminal engine state: `WindowExpired → "window expired"`; range-day latch with nothing
re-enabled (§10 #4) → `"range day — trading disabled"`; zero-child OR refusal (§10 #13) →
`"opening range not establishable"`; reached `next_close` still in `WAIT_CONFIRMATION`/`WAIT_ENTRY`
→ `"no confirmed breakout/entry"`. The report then states the reason with `$0.00` P/L.

**Timing.** The report fires at `next_close` (market close), after flatten and final-fill
reconciliation (`get_position() == 0`), so the day's full realized P/L — including any FLATTEN
close — is captured; then the process exits.

**Discord format.** A compact embed: date, symbol, mode, per-trade lines
(`LONG 10sh @ 100.00 → 102.00  +$20.00 (+0.40%)  [TARGET]`), then **total realized P/L $ and %**,
win/loss/breakeven counts, and the data-feed/`low_confidence` caveat if the opening range was
partial. (P/L percentages are formatted from the stored fraction × 100.)

---

## 18. Testing strategy

**Primary target: `engine.py`** (pure, deterministic; ~95% coverage gate) tested
candle-by-candle with synthetic fixtures and event-time derived from `candle.ts_close`
(no real clock needed). Helpers `make_candle(...)` and `feed_candles(engine, [...])` assert
`State` + emitted events after each candle.

- **Transition tests (spec §3):** `IDLE→BUILDING_RANGE` at open; `BUILDING_RANGE→RANGE_SET`
  only on the 09:30 15m candle; reject a late bar fabricating a range; strong-close breakout
  (long & short) → `WAIT_ENTRY`. **Negatives:** intrabar wick beyond range but close inside →
  no transition; weak close → no transition.
- **Setup/guardrail tests (§6, §9):** each model's exact entry/stop/target/rr + reasons;
  `validate_setup` rejects inverted/low-rr/sub-min-stop; sizing floor + `qty<1` rejection.
- **Edge cases:** retest counts every `WAIT_ENTRY` candle and abandons at the cap; range-day
  latch applies before confirmation on the same bar and persists; range-day filter doesn't
  cancel an in-flight approved setup; re-arm path keeps range/ATR/sweep latches and flips
  direction; write-once range no-ops a post-`RANGE_SET` update.
- **Aggregation:** 1m→15m bucketing anchored at 09:30; emit on advance; `force_close` with
  missing bars marks `data_incomplete` (and `bars_present==0` ⇒ no range, §10 #13); duplicate-bar
  idempotency; `bars_present` surfaced. Parameterized-`tf_min` check: same `Aggregator` yields
  correct 5m and 1m (pass-through) buckets (for the §20a experiment).
- **Indicators:** Wilder ATR vs hand-computed; `is_strong_close` truth table; `within`
  boundaries; fractal swings.
- **Config:** YAML + `.env` load + source precedence; validators (flatten_at, rr,
  live+non-SIP rejection, `enable_reversal`/`require_higher_tf_bias`/non-empty `range_day_enables`
  rejection, `swing_lookback ≥ 2·k+1`).
- **Async orchestrator (`pytest-asyncio`):** fake feed/broker/approver/reporter doubles.
  **Paper:** `AutoApprover` → `SetupProposed` auto-`APPROVE` → `submit_bracket` with whole-share
  qty, no human prompt, `reporter.trade_taken` fired. **Live:** `DiscordApprover` double →
  `REJECT`/`TIMEOUT` consumes no slot. Flatten timer triggers `cancel_all + flatten`; the slot
  decrements on the **entry** fill (not exit).
- **Reporting / P&L:** `SessionSummary` math — long/short per-trade `pnl` + `pnl_pct` (stored as
  a fraction), multi-trade totals, win/loss/breakeven counts summing to `len(trades)`; exit-side
  partial-fill share-weighted averaging; `TARGET`/`STOP`/**`FLATTEN`** exit attribution (incl. the
  orchestrator-synthesized flatten trade) via the `client_order_id` prefix grouping; the no-trade
  case sets each `no_trade_reason` mapping and reports `$0.00`; the equity cross-check fires only
  when flat and is skipped on the open-position Ctrl-C path; `session_report` invoked once at close
  in both modes; pre-preflight SIGINT skips the report; `LogReporter` fallback when Discord unconfigured.
- **Integration smoke (paper, network-gated):** assert the pinned SDK exposes
  `subscribe_bars` / `_run_forever` / `stop_ws`; submit + cancel a tiny paper bracket; Discord
  client connects and posts an embed (and, for the live path, a button click resolves the Future).
- **Purity enforcement:** `import-linter` contract + a no-`now()` grep test over pure modules.

**Layout & imports (per the guide).** Tests live in `tests/` (sibling of the package) and import
the package through `tests/context.py` (`sys.path` insert + `import orb_bot`), using
`from .context import orb_bot` — so the suite runs regardless of install method; `make test`
runs `pytest tests`. Named CSV scenarios under `tests/fixtures/candles/` (`trend_long`,
`range_day`, `weak_breakout`, `retest_hold`) double as future backtest regression inputs.

**Tooling:** `pytest>=8`, `pytest-asyncio` (`asyncio_mode=auto`), `pytest-cov`, `freezegun`
(orchestrator timing), `ruff`, `mypy`, `import-linter`.

---

## 19. Dependencies & runtime

- **Python:** 3.11+ (`zoneinfo`, modern typing).
- **Runtime deps:** `alpaca-py>=0.43,<0.44`, `discord.py>=2.6,<3`, `pydantic>=2`,
  `pydantic-settings`, `pyyaml`, `python-dotenv`, `structlog`.
- **Dev deps:** `pytest`, `pytest-asyncio`, `pytest-cov`, `freezegun`, `ruff`, `mypy`,
  `import-linter`.
- **Packaging & root files (per the guide):** `pyproject.toml` (packaging + deps + `ruff`/`mypy`/
  `pytest` config — the modern equivalent of the guide's `setup.py`); `requirements.txt` mirroring
  deps for `make init`; a `Makefile` (`init`/`test`/`lint`/`run`, `.PHONY`); `LICENSE` and
  `README.md` at root; `.env.example` template (real `.env` gitignored); `config/config.yaml`.
- **Run:** `python -m orb_bot` (or `make run`).
- **Daily operation:** launched manually per trading day now; `cron`/`systemd` noted as a
  later option (not built in this iteration).

> **Versions to verify before first run:** `alpaca-py` and `discord.py` are not yet installed
> in this repo; pin the versions above, then install and introspect `subscribe_bars` /
> `_run_forever` / `stop_ws` and the `discord.ui.View` signatures before the first live/paper run.

---

## 20. Future extension — the backtester seam

Deferred. `ReplayFeed` (reads 1m CSV/parquet → `Candle`s, event-time from `ts_close`),
`SimBroker` (simulates bracket OCO fills against subsequent bars), and `DataClock` (`now()` =
last candle `ts_close`) will satisfy the **same Protocols** and feed the **identical** `engine`
+ `aggregation`, giving live/backtest parity. `AutoApprover` (the paper-mode approver) and the
`Reporter`/`SessionSummary` machinery are **already built** this iteration and reused unchanged
by the backtester. Only the feed/broker/clock doubles remain deferred.

---

## 20a. Future experiment — timeframe comparison (15m / 5m / 1m)

**Depends on §20 (backtester must exist first).** Goal: run the *same* single-timeframe ORB
strategy at **`T ∈ {15m, 5m, 1m}`** over one historical window (same symbol, same dates, same
1m source data) and **compare results side by side** to see which interval performs best.

This is enabled "for free" by two existing design choices: the engine is **timeframe-parameterized**
(§10 #14 — `T` is config, not hardcoded), and the `Aggregator` is **`tf_min`-parameterized** (§11).
So one run = `ReplayFeed(1m history) → Aggregator(tf_min=T) → Engine(T) → SimBroker → SessionSummary`.

**Runner (`backtest/sweep.py`, deferred):**
- For each `T` in the sweep, load a config with `range/confirm/entry_timeframe_min = T`. A **per-`T`
  override block is the authoritative mechanism** and MUST set **every `T`-dependent param**:
  `atr_timeframe_min` (`= T`), `or_min_bars` (`<= T`, defaults to `T`), and the candle-count params
  `retest_max_wait_candles`, `swing_lookback`, `swing_fractal_k` (these are in *candles*).
  A `15/T` scaling of the candle-count params is only a **starting heuristic**, not a substitute
  for recalibration (§10 #14) — e.g. it can't sensibly turn `swing_fractal_k 1` into `15` at `T=1`.
  Everything else (risk %, RR, strong-close, displacement mult) is held constant so the **only**
  variable is the interval.
- The three runs are **independent → executed in parallel** (one engine instance each; the pure
  engine has no shared state). Each replays the same dates day-by-day, producing one
  `SessionSummary` per day; the runner aggregates those into a per-`T` **performance record**.
- **Comparison metrics** per `T`, over the window: total & avg P/L ($ and %), win/loss/breakeven
  counts and win rate, trade count & trades/day, average realized R-multiple / expectancy, max
  drawdown, and a no-trade-day count. Output a **side-by-side comparison table** (logged + an
  optional Discord/CSV/markdown report reusing the `Reporter` formatting).
- **Determinism & parity:** `ReplayFeed` derives event-time from `ts_close` and the engine is
  pure, so every `T`-run is reproducible and uses the *same* code path as live — the comparison
  reflects genuine strategy behavior, not backtest artifacts.

**Caveats to surface in the report:** results use the backtest data feed (prefer full SIP history;
IEX-sourced 1m understates volume/levels, §21); finer `T` (1m) yields more, smaller trades and is
most sensitive to per-`T` parameter tuning; commissions/slippage are modeled by `SimBroker` (state
the assumptions). The experiment **informs** which `T` to run live but doesn't change the live
default (15m) until reviewed.

---

## 21. Open risks to revisit before going live

- **IEX accuracy** (~2–3% of volume): OR extremes/sweeps from IEX can differ materially from
  SIP, especially 09:30–09:45. Live is hard-gated to SIP unless explicitly overridden.
- **`alpaca-py._run_forever` stability:** semi-internal; re-verify on every SDK upgrade.
- **Shortability:** confirm `shorting_enabled` and the symbol is shortable/easy-to-borrow at
  preflight; gate `allow_short=False` otherwise.
- **Strategy validity:** defaults are unvalidated (per the source spec). Calibrate after the
  backtester exists; until then, paper only is the safe posture.
- **Single data websocket** on the free tier: don't run a second data consumer on the same key.
```
