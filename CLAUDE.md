# CLAUDE.md — orb-bot

Single-symbol Opening-Range-Breakout (ORB) trading bot on Alpaca. Single-process
asyncio: a **pure, deterministic engine** + I/O adapters behind Protocols.
Authoritative design: `docs/superpowers/specs/2026-06-19-orb-alpaca-design.md`;
source strategy: `orb_strategy_spec.md`.

## Environment (non-obvious)
- Python 3.12 in a **uv-managed venv at `.venv`** (the system Python has no pip).
  Activate per shell — state doesn't persist between commands: `source .venv/bin/activate`.
- Deps are pre-installed. To recreate: `uv venv .venv && source .venv/bin/activate && uv pip install -e ".[dev]"`.
  (`make init` uses bare `pip` — works only inside the venv.)

## Commands (run inside the venv)
- `make test` → `pytest tests`
- `make lint` → `ruff check orb_bot tests` + `mypy orb_bot` + `lint-imports`
- `make run`  → `python -m orb_bot`
- **Pre-commit gate (run the whole thing):** `ruff check orb_bot tests && mypy orb_bot && lint-imports && pytest`

## Architecture — the purity boundary (load-bearing)
- **Pure core** — `orb_bot/{models,aggregation,indicators,engine}.py` import NOTHING from
  `feed`/`execution`/`approval`/`reporting`/`orchestrator`/`discordbot`, and NEVER call
  `datetime.now()`/`time.time()`. The engine times off `candle.ts_close` (event time).
  Enforced by `lint-imports` (import-linter contract) + a no-`now()` test. Don't even put the
  literal `datetime.now()` in a pure module's comment — the grep flags it.
- **Single timeframe `T`** (default 15m): only `T`-candles reach the engine; the orchestrator
  aggregates Alpaca's native 1-minute stream up to `T`. Validator pins `range==confirm==entry==atr_timeframe_min`.
- **Protocol seam** (`orb_bot/interfaces.py`): `DataFeed`/`Broker`/`Approver`/`Reporter`/`Clock`.
  The engine imports none; the orchestrator injects concrete impls (backtester drops in later).

## Conventions & gotchas
- **Secrets are `pydantic.SecretStr`** (`settings.alpaca_key`/`alpaca_secret`/`discord_token`) —
  call `.get_secret_value()` before handing to SDK clients; never log a `Settings`' secret fields.
  Feed/broker take plain `str`; `__main__` resolves them.
- **Tests import the package directly** (`from orb_bot.X import ...`) — it's editable-installed.
  Do NOT add `from .context import orb_bot` (unused → ruff F401).
- **Prices are `decimal.Decimal`; timestamps tz-aware `America/New_York`** (`zoneinfo`). Convert SDK floats via `Decimal(str(x))`.
- **mypy `warn_unused_ignores=true`** → never leave a stray `# type: ignore`.
- **alpaca-py loop trap**: never call `StockDataStream.run()`/`TradingStream.run()` (they call
  `asyncio.run()` and block). Schedule `stream._run_forever()` as a task; shut down via
  `await stream.stop_ws()` (NOT sync `stop()`). Pinned `alpaca-py>=0.43,<0.44`; keep alpaca-py
  imports inside `orb_bot/feed` & `orb_bot/execution` only.
- **Bracket orders require whole-share qty** (Alpaca rejects fractional/notional on brackets).
- **Live trading is hard-gated**: paper by default; `run.live` needs `feed=SIP` (or explicit
  `allow_live_iex`). Config validators fail fast at load.
- **`displacement_model=TRUE_GAP`** is config-selectable but a no-op (deferred) → silently
  disables breakout entries; default is `IMPULSE`.

## Build status / workflow
- Built via subagent-driven TDD on branch `feature/orb-bot-impl`. Current task status + resume
  pointer: `.superpowers/sdd/progress.md` (gitignored). Plan: `docs/superpowers/plans/2026-06-19-orb-alpaca-bot.md`.
- TDD: failing test → minimal impl → pass → commit; `make lint` (whole tree) + `pytest` green before each commit.
- Deferred (not yet built): the backtester + the 15m/5m/1m timeframe-comparison experiment (spec §20/§20a).

## Production (live-trading) backlog — MUST resolve before flipping `run.live`
Gaps/risks found **during** the build that the 47-task plan does **not** cover. Keep this list
current: add items as reviews surface them, check them off only when fixed + tested. Detailed
write-ups live in the `.superpowers/sdd/progress.md` roll-up. (Beyond this list, finishing the
47-task plan itself is a prerequisite.)

- [ ] **[HIGH] Quantize `Setup` stop/target to `tick_size` (engine, pure core).** `engine.py:160`
  `_buffer()` = `max(stop_buffer_ticks*tick, stop_buffer_atr*ATR)`; the ATR term is **not** rounded
  to `tick_size`, so stop/target can be sub-penny. `submit_bracket` forwards them verbatim and
  **Alpaca rejects sub-penny prices on ≥$1 equities → the whole bracket fails to enter** whenever
  the ATR buffer dominates the tick buffer (common). Fix in engine setup-construction (round to
  `tick_size`, correct direction, before `validate_setup` so RR math stays consistent) — NOT in the
  broker (would mutate engine prices behind its back / break the purity boundary). Add an
  ATR-buffer test asserting penny-aligned prices. *(Found: T27 security review.)*
- [ ] **[MED] Order-submission idempotency + single-flight guard (orchestrator, tasks 38-45).**
  `submit_bracket` advances `_seq`/`_parent_coid` before the `to_thread` submit. Seq always advances
  so two ENTRY orders never share a coid, but: (a) a submit that reached Alpaca but timed out on the
  response → a naive retry places a **second distinct live bracket** Alpaca's dedup can't catch —
  the orchestrator must reconcile via `get_order` before retrying (or use a per-logical-setup
  deterministic coid); (b) `_parent_coid` is single-valued broker-wide state with no single-flight
  guard — safe at `max_trades_per_day=1` default, but >1 in-flight bracket misclassifies legs in
  `leg_role_for`. Enforce/document the single-in-flight-bracket invariant. *(Found: T27 review.)*
- [ ] **[MED] Reject `displacement_model=TRUE_GAP` at config load, or implement it.** It's
  config-selectable but `detect_displacement` returns `None` (deferred) → **silently disables
  breakout entries**. Before live, fail-fast in a config validator or finish the impl. *(Known
  deferred gap; see Conventions.)*
- [ ] **[LOW] Harden `whole_share_qty` once the position-sizer lands.** Currently only checks
  `qty < 1` then `int(qty)` — silently truncates a float `qty>1` (1.7→1) and accepts `True` (bool ⊂
  int). Reject non-int qty so a sizing bug fails loud rather than placing a wrong quantity. *(Found:
  T27 review; defense-in-depth, not a current defect — `qty` is `int`-typed.)*
