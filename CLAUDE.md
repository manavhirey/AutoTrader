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
- [ ] **[HIGH] `flatten`/`cancel_all` are ACCOUNT-WIDE, not symbol-scoped — needs a decision before live.**
  `flatten` → `close_all_positions(cancel_orders=True)` and `cancel_all` → `cancel_orders()` both hit
  **every symbol in the Alpaca account**, not just `run.symbol`. If the account also holds unrelated
  positions (manual trades, another bot, long-term holdings), the EOD flatten timer **market-liquidates
  the entire account** automatically. The brief mandates these account-wide SDK calls (so a fix needs
  sign-off): either (a) make them symbol-scoped — `close_position(run.symbol)` + cancel only orders whose
  symbol/coid-prefix matches — or (b) add a startup hard-gate asserting the account holds no non-bot
  positions/orders + a CRITICAL "flatten liquidates the WHOLE account" warning. *(Found: T28 security review.)*
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
- [ ] **[MED] `flatten` can't tag the FLATTEN close with a deterministic `client_order_id` (spec §8 step 9 / §16).**
  Alpaca's `close_all_positions` issues broker-initiated closes with auto-generated ids, so the spec's
  "orchestrator records the FLATTEN coid before flattening, fill is attributable" is unenforceable through
  this primitive — attribution falls back to the `leg_role_for` unknown-coid⇒FLATTEN heuristic. Either
  implement flatten as a tagged single-symbol closing order (deterministic FLATTEN coid) or update the
  spec to document the heuristic as the contract. *(Found: T28 security review; spec/SDK mismatch.)*
- [ ] **[MED] No partial-failure handling on `flatten`; missing `get_position` reconciliation primitive (orchestrator).**
  `flatten` ignores `close_all_positions`'s 207 multi-status body, so a position that **failed** to close
  is silently treated as flat; transient errors (429/422/network) propagate raw into the EOD timer with no
  retry/reconcile. The spec's mitigation (reconcile via `get_position()==0` at `next_close`) is currently
  unimplementable — no `get_position`/`get_open_position(symbol)` primitive exists on the broker. Add it and
  have flatten surface partial-failure signal so the orchestrator can detect a still-open position before
  market close. *(Found: T28 security review; orchestrator-scoped, tasks 38-45.)*

- [ ] **[HIGH] Missed/dropped trade-update fill is silent — needs orchestrator reconciliation + a stream-liveness watchdog.**
  `_on_trade_update` now wraps its body in `try/except` so a malformed event can't kill the stream (good) —
  but a dropped event is a **silently missed fill**: a missed EXIT fill leaves the bot believing it's still in
  a position. The stdlib logger records it, but nothing surfaces it. Before live: add orchestrator-side
  reconciliation (poll `get_order`/positions to confirm fills) and a stream-liveness watchdog (expected-fill
  timeout) so a stalled/lossy stream is detected. *(Found: T29 security review; orchestrator scope, tasks 38-45.)*
- [ ] **[MED] Trade-updates `asyncio.Queue()` is unbounded — pick a `maxsize` + full-policy once the consumer is wired.**
  No backpressure; if the orchestrator consumer stalls, the queue grows unbounded. Decide block-vs-drop-with-alert
  and set a sane `maxsize` (the SDK itself caps at ~1024). Deferred until the orchestrator wires the consumer so
  the policy can be chosen with the consumer in view. *(Found: T29 security review.)*
- [ ] **[LOW] `leg_role_for(coid, parent_coid)` ignores `parent_coid` (dead param).** Classification is suffix-only
  (intentional for single-symbol). Drop the unused second arg or document why it's retained (future multi-symbol).
  *(Found: T29 review; Task 25 code.)*

### Resolved-during-build findings log (audit trail; fixed in the named commit)
- [x] **[T29 High×3 + robustness] `_on_trade_update` real-SDK-boundary + stream-survival.** `side=str(enum)` →
  `'OrderSide.BUY'` (now `.value`); `int(filled_qty)`/`int(position_qty)` crashed on float-shaped strings (now
  `Decimal`-coerced); event-enum `str()` mismatch (now `.value`-normalized); `Decimal(str(None))` price crash
  (now None-guarded); and — most important — the callback is now wrapped in `try/except Exception` so one bad
  event can't permanently kill fill processing. Plus `start_stream` double-start guard + `create_task`, and a
  real-`OrderSide`/`TradeEvent`-enum regression test so the fakes stop masking the boundary. Fixed in T29 commit.
- [x] **[T28 Nit] `get_order` test didn't verify the `order_id` was forwarded** to the SDK (fake discarded
  it). Fake now records `requested_order_id`; test asserts it. Fixed in T28 commit.
- [x] **[T28 Low] Unguarded account-mutating primitives lacked warnings.** Added docstrings to
  `get_order`/`cancel_all`/`flatten` noting they are thin account-mutating primitives and ordering/safety
  is the orchestrator's responsibility. Fixed in T28 commit.
- [x] **[T27 High] `_to_order_result` status used `str(enum)` → `'OrderStatus.ACCEPTED'`** instead of the
  wire string; **filled_qty `int('10.0')` crashed.** Fixed via `.value` + `Decimal` coercion in commit `9098f66`.
