# CLAUDE.md — orb-bot

Single-symbol Opening-Range-Breakout (ORB) trading bot on Alpaca. Single-process
asyncio: a **pure, deterministic engine** + I/O adapters behind Protocols.
Authoritative design: `docs/superpowers/specs/2026-06-19-orb-alpaca-design.md`;
source strategy: `orb_strategy_spec.md`.

## Environment (non-obvious)
- **Python 3.11+** (`requires-python = ">=3.11"`, matching README); the `.venv` ships 3.12.
  A **uv-managed venv at `.venv`** (the system Python has no pip).
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
- **Build COMPLETE** (47/47 tasks, subagent-driven TDD). On `main`; full diff in PR #1
  (`feature/orb-bot-impl` → `base`). Gate green: 415 passed, 7 skipped, 0 errors
  (PR #1 review fixes added 15 tests).
- TDD: failing test → minimal impl → pass → commit; run the whole-tree gate green before each commit.
- **Next work = the Production backlog below** (gates `run.live`). Per-task history: `.superpowers/sdd/progress.md` (gitignored).
- Deferred (not built): the backtester + the 15m/5m/1m timeframe-comparison experiment (spec §20/§20a).

## Production (live-trading) backlog — MUST resolve before flipping `run.live`
Gaps/risks found **during** the build that the 47-task plan does **not** cover. Keep this list
current: add items as reviews surface them, check them off only when fixed + tested. Detailed
write-ups live in the `.superpowers/sdd/progress.md` roll-up. (Beyond this list, finishing the
47-task plan itself is a prerequisite.)

- [ ] **[CRITICAL] Wire restart-into-open-position reconciliation to the REAL broker (spec §16).** The orchestrator
  `_preflight` detects an open position via `getattr(self.broker, "_positions_qty", 0)` — a **test-fake-only** attr;
  the real `AlpacaBroker`/`Broker` Protocol have **no position accessor**, so reconciliation is **dead in production**:
  on a mid-session restart with a live bracket the engine never adopts it, tries to re-establish a range, and can
  **submit a SECOND trade** against the open position (esp. paper/AutoApprove). Also `_preflight` looks the ENTRY order
  up by `client_order_id` via `broker.get_order` → `get_order_by_id` (which expects the broker UUID, not a coid → 404).
  Fix before live: add `Broker.get_position()->int` (signed qty; AlpacaBroker via `get_open_position`, 404→0) AND
  `Broker.get_order_by_client_id`; have `_preflight` use them instead of the placeholders (incl. deriving the real
  ENTRY seq from the open order's coid, not the hardcoded `1`). The orchestrator-side logic is already hardened to
  adopt unconditionally / not fabricate a $0 entry price — only the broker primitives + wiring remain. *(Found: T38
  code+security review; consolidates the T28 get_position item.)*
- [ ] **[HIGH] Late-start flatten fail-safe (run loop, Task 42).** `_preflight` computes `flatten_at = min(config, next_close−buffer)`;
  if the bot starts after that (or `flatten_at` is already in the past) with a position open, the run loop must flatten
  **immediately** (not schedule a negative sleep) and also hard-gate on `next_close` itself, so a position is never held
  overnight. *(Found: T38 security review.)*
- [ ] **[MED] Market-closed preflight leaves `start_equity=None` — caller must early-exit (run loop Task 44 / report Task 43).**
  `_preflight()` returns False on a closed market without setting `start_equity`; the run loop must not proceed to any
  equity use, and the session-report path must guard `start_equity is None`. *(Found: T38 security review.)*
- [ ] **[HIGH] Cumulative-vs-increment `qty` contract mismatch on partial fills (execution ↔ orchestrator).** Task 29's
  `_on_trade_update` sets `Fill.qty = int(order.filled_qty)` — Alpaca's `filled_qty` is the order's **cumulative** total,
  but `_on_fill`/`summary` **sum** `f.qty` across fills assuming each is the per-fill **increment**. So two partials of 6
  then 10(cum) get summed to 16 shares and mis-weight the average price → corrupted reported P&L/qty on ANY partially-filled
  trade (reporting-only; the submitted bracket qty is still correct). Pick ONE convention end-to-end: emit the delta in the
  adapter (track prev cumulative per order) OR use last-cumulative in the builder. Add a partial-fill integration test.
  *(Found: T41 review.)*
- [ ] **[HIGH] EOD reconciliation for a partial close that never reaches `position_qty==0` (orchestrator, Task 42/43).**
  If an exit only partially closes and the position is then flattened/EOD'd without a final `position_qty==0` fill, the
  accumulated `_exit_fills` never build a TradeResult → the trade is **silently lost from the session P&L**. Add an
  end-of-session step that, if `entry_fill` is still set with exit fills accumulated, forces a TradeResult build (same
  4-field reset). *(Found: T41 security review.)*
- [ ] **[MED] Adopted/reconciled trade is mis-attributed to `BREAKOUT`.** `_build_trade_result` defaults `model` to
  BREAKOUT when `_last_model` is None — true for a restart-adopted position (no `_react` ran). Attribute adopted trades
  with an explicit RECONCILED/UNKNOWN model (or recover the original) so session stats aren't skewed. *(Found: T41 review;
  part of the reconciliation feature.)*
- [ ] **[CRITICAL] Feed-stall fail-safe — a wall-clock ticker (run loop).** `run()`'s EOD-flatten and shutdown are driven
  ENTIRELY by `feed.candles()`: if the 1m feed STALLS after `flatten_at` (websocket dies but `anext` just blocks, never
  raises), the loop never wakes → EOD flatten never fires → the position is **held overnight**, and a SIGINT is observed
  only lazily (a blocked `anext`/`queue.get` won't see `_stop`). The `finally` fail-safe flatten only triggers on loop
  EXIT, not a silent stall. Fix: drive the loop off a feed-independent wall-clock ticker — race `anext(feed)` against an
  `asyncio.sleep`/timeout so each wakeup re-checks `flatten_at`/`_stop` even with no candle (also fixes SIGINT-during-block).
  *(Found: T44 security review.)*
- [x] **[HIGH] Quantize `Setup` stop/target to `tick_size` (engine, pure core).** RESOLVED (PR #1
  review): `engine._quantize_price` rounds the stop AWAY from entry (LONG floors / SHORT ceils) and
  the projected target AWAY from entry inside `_setup_from`, BEFORE the rr is recomputed, so
  `validate_setup`'s RR floor stays consistent and Alpaca never sees a sub-penny TP/SL leg. Done in
  the pure core (not the broker). Covered by `tests/test_engine_quantization.py`. *(Found: T27 security review.)*
- [x] **[HIGH] `flatten`/`cancel_all` are ACCOUNT-WIDE, not symbol-scoped.** RESOLVED via Option (b)
  (sign-off: keep the brief-mandated account-wide SDK calls, add a startup hard-gate). `_preflight` now,
  **in live mode only**, calls the new `Broker.list_position_symbols()` and **aborts with a CRITICAL log +
  RuntimeError** if the account holds any position outside `run.symbol`, so the account-wide EOD flatten can
  never liquidate an unrelated holding. NOTE still open: `cancel_all` remains account-wide for resting *orders*
  (non-destructive — cancels, no liquidation); symbol-scoping orders is deferred. *(Found: T28 security review.)*
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
- [x] **[LOW] Harden `whole_share_qty` once the position-sizer lands.** RESOLVED (PR #1 review):
  `whole_share_qty` now rejects non-`int` (and `bool`) with `TypeError` before the `qty < 1` check,
  so a sizing bug fails loud instead of silently truncating `1.7→1`. Covered by
  `test_whole_share_qty_rejects_non_int_types`. *(Found: T27 review.)*
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
- [x] **[MED] Trade-updates `asyncio.Queue()` is unbounded.** RESOLVED (PR #1 review): now
  `asyncio.Queue(maxsize=1024)` (matching the SDK's internal buffering). Policy = block-on-full, so a
  stalled consumer applies natural back-pressure through `put()` to the SDK handler rather than growing
  the queue without limit. *(Found: T29 security review.)*
- [ ] **[LOW] `leg_role_for(coid, parent_coid)` ignores `parent_coid` (dead param).** Classification is suffix-only
  (intentional for single-symbol). Drop the unused second arg or document why it's retained (future multi-symbol).
  *(Found: T29 review; Task 25 code.)*

- [ ] **[MED-sec] Pin the Discord approval channel to a known guild.** `resolve_channel` now validates the
  channel is messageable, but does NOT verify it belongs to the intended private server. Add a `DISCORD_GUILD_ID`
  config field and assert `ch.guild.id == expected` so the money-approval UI can't be redirected to an
  attacker-influenced/misconfigured `channel_id`. Pair with restricting the bot's OAuth invite to that one server.
  *(Found: T31 security review; needs a config field.)*
- [ ] **[MED] DiscordApprover (Task 33) MUST fail closed.** Hard-bound `request()` with `approval_timeout_s`,
  and treat `DiscordClient.is_ready == False` / any send failure as a NON-approval (never auto-approve a trade
  because the channel is dead). The `is_ready` liveness property now exists for this. *(Found: T31 security review;
  requirement for Task 33.)*
- [ ] **[LOW-ops] Restrict the bot's OAuth invite to the single intended Discord server** so the `guilds` intent
  can't broaden visibility if the bot is added elsewhere. Deployment/operational, not code. *(Found: T31 review.)*

- [ ] **[HIGH-sec] Enforce the "AutoApprover never in live mode" invariant in the wiring (tasks 40/45).**
  `AutoApprover.request()` auto-APPROVEs every trade with no human gate — correct for paper/backtest, catastrophic
  if it ever backs live trading. The class can't self-gate; the orchestrator/`__main__` MUST hard-assert that
  `run.live` ⇒ the approver is `DiscordApprover` (and paper ⇒ AutoApprover), failing fast otherwise. *(Found: T32
  security review.)*

- [x] **[LOW-ops] Make log timestamps timezone-explicit.** RESOLVED (PR #1 review): `logconf` now uses
  `TimeStamper(fmt="iso", utc=True)` (Z-suffixed, unambiguous). *(Found: T37 review.)*

- [ ] **[HIGH] Gate SHORT setups on `AccountSnapshot.shorting_enabled` (orchestrator `_react`, Task 40).** Nothing
  checks `shorting_enabled` before submitting a SHORT bracket; on a cash/non-margin account Alpaca rejects it and the
  setup silently fails. `_react` (where direction + the account snapshot are both in scope) must reject/skip a SHORT
  when `shorting_enabled` is False. *(Found: T39 security review.)*
- [x] **[LOW] Expose a clean engine ATR accessor instead of `getattr(self.engine, "ctx", None)`.** RESOLVED
  (PR #1 review): added `Engine.current_atr() -> Decimal | None` (returns None before the ATR is ready instead
  of raising); `_revalidate_setup` now calls `self.engine.current_atr() or Decimal("0")`. *(Found: T39 review.)*

- [ ] **[HIGH] Reconcile an AMBIGUOUS `submit_bracket` failure (orchestrator `_react`).** `_react` now logs+returns on a
  submit exception (no false trade_taken, and the engine's WAIT_ENTRY window guard expires the un-filled setup), but if
  the request actually reached Alpaca and only the RESPONSE failed, a **real resting bracket is left untracked**. Before
  live: on a submit exception, look the ENTRY order up by its deterministic `client_order_id` (via the same
  `get_order_by_client_id` primitive the CRITICAL reconciliation item needs) to decide adopt-vs-cancel. *(Found: T40 security review.)*

> Resolved-during-build findings (the audit trail of already-fixed items) live in
> `docs/build-findings.md` — history, not per-session guidance. The OPEN list above is what
> still gates live trading.
