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

- [ ] **[LOW-ops] Make log timestamps timezone-explicit.** `logconf` uses `TimeStamper(fmt="iso", utc=False)`,
  which can emit naive (no-offset) ISO timestamps depending on the host TZ — ambiguous for trade-log forensics.
  Use `utc=True` (Z suffix) or an explicit `%z` offset. *(Found: T37 review; Low, current behavior is defensible.)*

- [ ] **[HIGH] Gate SHORT setups on `AccountSnapshot.shorting_enabled` (orchestrator `_react`, Task 40).** Nothing
  checks `shorting_enabled` before submitting a SHORT bracket; on a cash/non-margin account Alpaca rejects it and the
  setup silently fails. `_react` (where direction + the account snapshot are both in scope) must reject/skip a SHORT
  when `shorting_enabled` is False. *(Found: T39 security review.)*
- [ ] **[LOW] Expose a clean engine ATR accessor instead of `getattr(self.engine, "ctx", None)`.** `_revalidate_setup`
  attribute-sniffs `engine.ctx.atr.value`; a rename would silently make `tol=0` (toothless stale-price gate). Add an
  `Engine.current_atr -> Decimal | None` accessor and call it. *(Found: T39 review; pragmatic today, real engine always has ctx.)*

- [ ] **[HIGH] Reconcile an AMBIGUOUS `submit_bracket` failure (orchestrator `_react`).** `_react` now logs+returns on a
  submit exception (no false trade_taken, and the engine's WAIT_ENTRY window guard expires the un-filled setup), but if
  the request actually reached Alpaca and only the RESPONSE failed, a **real resting bracket is left untracked**. Before
  live: on a submit exception, look the ENTRY order up by its deterministic `client_order_id` (via the same
  `get_order_by_client_id` primitive the CRITICAL reconciliation item needs) to decide adopt-vs-cancel. *(Found: T40 security review.)*

### Resolved-during-build findings log (audit trail; fixed in the named commit)
- [x] **[T40 _react robustness] approval→submit money-path hardening.** Review verified the path is correct (reject-before-
  approval ordering, mode-uniform approver call, single `on_approval`, strict `== "APPROVE"` submit gate, no secret logging).
  Fixed: `submit_bracket` and `get_account` wrapped in try/except (log `type(exc).__name__` only, fail safe — no false
  trade_taken); `_react` typed `EngineEvent` + terminal `unhandled_engine_event` warning; clarifying comments; +submit-failure /
  account-failure / unexpected-decision tests. Closed the T39 SHORT `shorting_enabled` gate here too. Fixed in T40 commit.
- [x] **[T39 sizing] buying-power cap + coverage.** Core risk-sizing math verified correct (floor, reject<1, LONG/SHORT
  revalidate symmetry). Added a buying-power cap (`qty = min(risk_qty, floor(buying_power/entry))`) so a wide-stop/large-equity
  size can't exceed buying power → silent Alpaca rejection; zero-equity now logs; +SHORT-sizing / SHORT-revalidate /
  overshoot / start_equity-None tests. Fixed in T39 commit.
- [x] **[T38 reconciliation correctness] engine + orchestrator hardening.** Fixed money-safety bugs in the §16
  restart path: P/L no longer computed against a fabricated `$0` entry (`filled_avg_price or Decimal('0')` removed;
  unknown entry → engine `_entry_price=None` → ~0 P/L, not fictitious); the engine is now adopted UNCONDITIONALLY on a
  non-zero position (was "reconciled=True but engine NOT adopted" → double-trade desync); `adopt_open_position` guards
  `qty==0` and forces `trades_remaining=0` (kills the re-arm-into-no-range crash at max_trades>1); `flatten_at` is
  tz-normalized; tests dropped global `os.environ` mutation for `monkeypatch`. (Real broker get_position wiring remains
  a CRITICAL backlog item.) Fixed in T38 commit.
- [x] **[T37 follow-up] logconf test teardown made robust.** The autouse fixture's `h.flush()` on a console handler
  bound to capsys's closed stdout raised "I/O operation on closed file" (3 teardown errors masked by the rtk summary at
  T37 commit time). Now removes the handler first, then flush/close in try/except. Fixed in T38 commit.
- [x] **[T37 logging] logconf hardening.** Review found no critical bugs / no secret leakage / JSON-injection safe.
  Fixed: log dir/file restricted to owner-only (0o700/0o600 — trade data); filename includes run_id (avoids same-day
  two-run collision/truncation); invalid level now raises ValueError (was silent INFO fallback); autouse teardown
  fixture closes root handlers so the global-logging tests don't contaminate the 293-test suite. Fixed in T37 commit.
- [x] **[T36 reporting] DiscordReporter hardening + integration gap.** Added the missing `DiscordClient.send_embed`
  (reporter would have AttributeError'd in production — tests only had a fake). Fixed: embed "Trades" field guarded
  against Discord's 1024-char limit (was a silent EOD-report drop at high trade counts, High); `_range_meta` absent
  default → low-confidence (was falsely reporting full "15/15"); Discord sends made best-effort (try/except+log) so a
  post failure can't crash EOD reporting; spec Unicode arrow `→`; +tests (overflow, send-failure, zero-P/L, no-trade).
  Note for orchestrator (Task 43): it must stash `client.opening_range` for the bars_present/T caveat. Fixed in T36 commit.
- [x] **[T34 reporting] P/L math verified correct + edge guards.** Review confirmed LONG/SHORT sign, share-weighting,
  qty=min, pnl_pct-as-fraction, and wins+losses+breakevens==len(trades) are all correct, and the module is pure.
  Added `start_equity <= 0` guards (clear ValueError instead of cryptic DivisionByZero) and tests (SHORT
  end-to-end, start_equity guard, empty-fills guards, single-fill avg). Non-terminating averages round (no crash);
  display-formatting is the reporters' job (Tasks 35/36). Fixed in T34 commit.
- [x] **[T33 approval gate] DiscordApprover fail-closed hardening.** Security review confirmed NO path returns
  APPROVE without a genuine authorized click (interaction_check blocks non-approvers at discord.py's dispatch
  level; stale/replayed views are inert; no secret leakage; downstream gates on exact `== "APPROVE"`). Fixed:
  `is_ready` guard default flipped True→False (fail closed for a missing attr); unauthorized clicks now logged
  (audit trail); load-bearing `done()`-guard comment; +tests for missing-is_ready⇒REJECT, the wait_for TIMEOUT
  backstop, and the non-approver-can't-resolve-the-future contract. Fixed in T33 commit.
- [x] **[T31 High + hardening] DiscordClient startup/fail-closed.** `start_in_background` no longer hangs forever on
  a failed `bot.start()` (bad token/network) — it races readiness against task completion and re-raises the start
  error; a crashed background task is now logged via a done-callback; `resolve_channel` validates the channel is
  messageable and raises (removed the `# type: ignore`); `close()` narrowed to log instead of swallow; added an
  `is_ready` liveness property and dropped the token reference after start. +start-failure & non-messageable tests.
  Fixed in T31 commit.
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
