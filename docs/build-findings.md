# orb-bot — Resolved-during-build findings (audit trail)

Findings surfaced by the per-task code+security reviews during the 47-task TDD build, **already
fixed** in the named commits. Moved out of `CLAUDE.md` to keep that file's per-session context lean.
The OPEN, still-pending items live in `CLAUDE.md` → "Production (live-trading) backlog".
Per-task detail: `.superpowers/sdd/progress.md` (gitignored).

- [x] **[FINAL whole-branch review] SHOWSTOPPER: transports were never started — now fixed.** The Orchestrator never
  called `feed.start()`, `broker.start_stream()`, or `DiscordClient.start_in_background()`, so in production (paper AND
  live) the bot was inert: `feed.candles()` blocked forever (no bars), no fills ever arrived, and the live approval gate
  stayed dead (every trade REJECTED). The per-task fakes (pre-seeded iterators / monkeypatched client) hid all three.
  Fixed: added `start()` to the DataFeed Protocol + `start_stream()`/`stop_stream()` to the Broker Protocol; `run()` starts
  the feed + broker streams before the drain task/candle loop (guarded by `_transports_started` so teardown only stops what
  started); the shared DiscordClient is started/closed (idempotently) via the Discord approver+reporter `start()/close()`;
  and `_emit_session_report` now stashes `engine.opening_range` on the reporter's client (fixes the always-LOW-CONFIDENCE
  Discord embed — the long-deferred opening_range item). +transport-start integration tests. Fixed in the final-integration commit.
- [x] **[T45 __main__ wiring] secret resolution + live-gate verified.** Review confirmed the security-critical parts are
  sound: SecretStr is resolved to plain str via `_reveal_str` and never reaches the Alpaca/Discord SDK; no secret is
  logged; the live-gate is two-layer (config model-validator + a wiring `RuntimeError`) so AutoApprover is provably
  unreachable in live (closes the T32 AutoApprover-never-live item). Fixed: replaced a `# type: ignore` with a narrowing
  `assert discord_approver_user_id is not None`; +a test proving resolved creds are plain `str` not `SecretStr`. Fixed in
  T45 commit. (DiscordReporter `opening_range` stash for the bars_present/T caveat remains deferred backlog.)
- [x] **[T44 run loop] capstone async-safety hardening.** Fixed: entry-fill RACE (`_pending_entry_qty`/`_last_model` now
  armed BEFORE `await submit_bracket`, reset on failure — fast fills were lost); drain-task crash no longer skips teardown
  (`await updates_task` catches Exception); a **fail-safe flatten** in `finally` flattens any still-open position on ANY
  exit (crash/SIGINT/engine-bug); `_drain_trade_updates` resilient (per-fill + stream-level guards, sets `_stop` on stream
  death so the bot doesn't trade blind); boundary force_close off-by-one fixed (uses `ts_open`). +race/fail-safe/boundary
  tests. Also renamed `self.run`(RunConfig)→`self.run_cfg` (shadowed `run()`), added pure `Aggregator.bucket_start()`.
  Fixed in T44 commit. (Feed-STALL ticker remains the CRITICAL backlog item above.)
- [x] **[T43 session report] builder + once-only emit hardening.** Relaxed the pure `build_session_summary` guard to
  reject `start_equity<=0` only when trades exist (no-trade/market-closed start_equity=0 → 0% return, no crash — closes
  the T38 start_equity-None concern for the report path). Fixed: `_react(WindowExpired)` now sets the `"window expired"`
  no-trade reason (was always the default); `_emit_session_report` sets `_reported=True` only AFTER a successful report
  (a failed report can retry); end_equity/start_equity None-handling. +get_account-failure & window-expired tests.
  Fixed in T43 commit. (DiscordReporter `opening_range` stash + EOD partial-close reconcile remain backlog.)
- [x] **[T42 flatten] EOD close always flattens.** Review (HIGH): if `cancel_all()` raised, `flatten()` never ran →
  position held open at EOD. Now `cancel_all` is best-effort (try/except + log) and `flatten` always runs (the critical
  EOD safety action); a `flatten` failure still propagates. +test (cancel raises → flatten still runs). Fixed in T42 commit.
- [x] **[T41 fill tracking] P&L core: fixed 2 brief bugs + 3 review-criticals.** The brief's full-fill detection
  (`position_qty==fill.qty`) and build-on-every-exit were wrong; reimplemented as intended-qty tracking + build-only-on-
  `position_qty==0` (share-weighted partial exits). Then review-fixes: SHORT entries (negative `position_qty`) now detected
  via `abs(position_qty) >= pending` (was a CRITICAL — bot was blind to its own shorts); entry idempotency (reset pending→0,
  no double slot-decrement); adopted-position close no longer crashes on empty `_entry_fills` (seeded in preflight + guard);
  exit-with-no-open-trade now logs. +SHORT/overshoot/duplicate/adopted tests. Fixed in T41 commit.
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
