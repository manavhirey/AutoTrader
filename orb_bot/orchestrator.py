"""Orchestrator: owns the asyncio loop, the wall clock, all wall-clock timing
(session gating, bucket-boundary timers, flatten scheduling) and the I/O
reactions to engine events. The engine stays pure (spec §15)."""
from __future__ import annotations

import asyncio
import datetime
import signal
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

from orb_bot import logconf
from orb_bot.aggregation import Aggregator
from orb_bot.execution import alpaca as execution_alpaca
from orb_bot.models import (
    ApprovalRequest,
    Candle,
    Direction,
    DirectionConfirmed,
    EngineEvent,
    EntryConfirmed,
    Fill,
    Model,
    NoOp,
    OrderResult,
    RangeDayDetected,
    RangeEstablished,
    SessionSummary,
    Setup,
    SetupProposed,
    TradeRecorded,
    TradeResult,
    WindowExpired,
)
from orb_bot.reporting import summary


class Orchestrator:
    def __init__(self, settings, feed, broker, approver, reporter, clock, engine):
        self.settings = settings
        self.cfg = settings.strategy
        # NOTE: stored as ``run_cfg`` (not ``run``) so the run-config attribute does
        # not shadow the ``run()`` lifecycle coroutine below.
        self.run_cfg = settings.run
        self.feed = feed
        self.broker = broker
        self.approver = approver
        self.reporter = reporter
        self.clock = clock
        self.engine = engine
        self.tz = ZoneInfo(self.cfg.timezone)
        self.symbol = self.run_cfg.symbol
        self.mode = "LIVE" if self.run_cfg.live else "PAPER"

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
        self._pending_entry_qty: int = 0
        self.flatten_coid: str | None = None
        self.trade_seq: int = 1
        self._stop = asyncio.Event()
        self._reported = False
        # once-guard so EOD flatten fires exactly once (not on every 1m candle
        # once the wall clock passes flatten_at).
        self._flattened = False
        self._terminal_reason: str | None = None
        # lifecycle flag: set to True after feed.start() + broker.start_stream()
        # so _teardown only stops the broker stream if it was actually started.
        self._transports_started = False

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
        # tz-normalize next_close to the bot's configured tz so the stored/logged
        # flatten_at is unambiguously in self.tz (next_close may arrive in any tz).
        # clock_info.next_close is a non-optional datetime (ClockInfo contract).
        next_close_local = clock_info.next_close.astimezone(self.tz)
        clamp = next_close_local - datetime.timedelta(
            minutes=self.cfg.flatten_buffer_min
        )
        self.flatten_at = min(config_flat, clamp)

        self.log.info(
            "preflight",
            mode=self.mode,
            feed=self.run_cfg.feed,
            start_equity=str(self.start_equity),
            next_close=next_close_local.isoformat(),
            flatten_at=self.flatten_at.isoformat(),
        )

        # Restart-into-open-position reconciliation (§16): adopt the live
        # bracket via a real engine entry point, force the day's last trade, and
        # skip range/confirmation. The engine is told it is IN_TRADE so it will
        # never (re-)establish a range for the rest of the session.
        # BACKLOG GAP: `_positions_qty` is a test placeholder; a real
        # `broker.get_position(symbol)` is deferred (tracked in CLAUDE.md backlog).
        position_qty = getattr(self.broker, "_positions_qty", 0)
        if position_qty != 0:
            # Best-effort lookup of the original ENTRY fill to recover the real
            # entry price. If it fails we adopt WITHOUT a price (entry_fill=None)
            # rather than fabricate a $0 entry, which would corrupt P/L massively.
            # BACKLOG GAP: the ENTRY client_order_id is hard-coded to seq=1; a real
            # seq derivation (get_order_by_client_id over the session's order log)
            # is deferred (tracked in CLAUDE.md backlog).
            entry_coid = execution_alpaca.client_order_id(
                session_date, self.symbol, 1, "ENTRY"
            )
            try:
                entry_order = await self.broker.get_order(entry_coid)
            except Exception as exc:  # noqa: BLE001 - lookup must not abort preflight
                self.log.warning(
                    "preflight_entry_order_lookup_failed",
                    entry_coid=entry_coid,
                    error=type(exc).__name__,
                )
                entry_order = None

            entry_fill: Fill | None = None
            if entry_order is not None and entry_order.filled_avg_price is not None:
                entry_fill = Fill(
                    order_id=entry_order.order_id,
                    client_order_id=entry_order.client_order_id,
                    leg_role="ENTRY",
                    side="buy" if position_qty > 0 else "sell",
                    price=entry_order.filled_avg_price,
                    qty=abs(int(position_qty)),
                    ts=self.clock.now(),
                    position_qty=int(position_qty),
                    exit_reason=None,
                )

            # Adopt UNCONDITIONALLY on a non-zero position (entry_fill may be None):
            # state=IN_TRADE, trades_remaining forced to 0, range/confirmation
            # skipped. Set reconciled/entry_fill AFTER so we are never
            # "reconciled=True but engine NOT adopted".
            self.engine.adopt_open_position(int(position_qty), entry_fill)
            self.reconciled_open_position = True
            self.entry_fill = entry_fill
            # Seed _entry_fills so the eventual close has an entry leg to
            # weight against; leave _pending_entry_qty=0 (entry already happened
            # pre-restart; no ENTRY fill will arrive through _on_fill).
            if entry_fill is not None:
                self._entry_fills = [entry_fill]
            self.log.warning(
                "preflight_reconcile_open_position",
                position_qty=position_qty,
                entry_price=(
                    str(entry_fill.price) if entry_fill is not None else None
                ),
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

    def _equity_for_sizing(self) -> Decimal:
        if self.cfg.equity_source == "fixed":
            return Decimal(self.cfg.fixed_equity)
        return self.start_equity if self.start_equity is not None else Decimal("0")

    def _sizing(self, equity: Decimal, setup: Setup, buying_power: Decimal) -> int:
        if equity <= 0:
            self.log.warning("sizing_zero_equity")
            return 0
        stop_dist = abs(setup.entry - setup.stop)
        if stop_dist <= 0:
            return 0
        risk_dollars = (equity * Decimal(str(self.cfg.risk_per_trade_pct))) / Decimal("100")
        raw = (risk_dollars / stop_dist).to_integral_value(rounding=ROUND_DOWN)
        qty = int(raw)
        if setup.entry > 0:
            bp_qty = int(
                (buying_power / setup.entry).to_integral_value(rounding=ROUND_DOWN)
            )
            qty = min(qty, bp_qty)
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
            feed=self.run_cfg.feed,
            or_high=getattr(oran, "high", setup.entry) if oran else setup.entry,
            or_low=getattr(oran, "low", setup.stop) if oran else setup.stop,
            bars_present=bars_present,
            data_warning=data_warning,
            approval_ttl_s=self.cfg.approval_timeout_s,
        )

    async def _react(self, ev: EngineEvent) -> None:
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
            self._terminal_reason = "window expired"
            return
        if isinstance(ev, TradeRecorded):
            self.log.info(
                "trade_closed", pnl=str(ev.pnl), exit_reason=ev.exit_reason
            )
            return
        if isinstance(ev, SetupProposed):
            await self._on_setup_proposed(ev.setup)
            return
        else:
            self.log.warning("unhandled_engine_event", event=type(ev).__name__)

    async def _on_setup_proposed(self, setup: Setup) -> None:
        equity = self._equity_for_sizing()
        try:
            acct = await self.broker.get_account()
        except Exception as exc:  # noqa: BLE001
            self.log.warning(
                "setup_rejected", reason="account_fetch_failed", error=type(exc).__name__
            )
            return
        qty = self._sizing(equity, setup, acct.buying_power)
        if qty < 1:
            self.log.info("setup_rejected", reason="risk too small for one whole share")
            return
        # Stale-price re-validation (§8 step 6 / §16): after sizing, BEFORE asking the
        # approver, confirm the level is still reachable on the latest 1m price.
        if not self._revalidate_setup(setup):
            self.log.info("setup_rejected", reason="stale price: level no longer reachable")
            return
        # Gate SHORT setups on account's shorting_enabled flag.
        if setup.direction is Direction.SHORT and not acct.shorting_enabled:
            self.log.info("setup_rejected", reason="shorting not enabled on account")
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
        # The returned event list is intentionally NOT re-dispatched — the real
        # entry/IN_TRADE transition and slot consumption happen later via
        # on_entry_filled on the actual fill (spec §8 step 8), not off EntryConfirmed;
        # re-dispatching would double-fire.
        self.engine.on_approval(decision)
        if decision != "APPROVE":
            return
        # Arm the entry-fill guard BEFORE submitting (HIGH, entry-fill race): a fast
        # ENTRY fill can arrive on the trade-updates drain DURING the submit await
        # (same loop). If _pending_entry_qty were still 0 then, _on_fill would never
        # fire on_entry_filled and the IN_TRADE transition would be LOST. Also record
        # the submitted setup's model so the eventual TradeResult is attributed
        # correctly (MED #11); set on each successful submit, remains until the next.
        self._pending_entry_qty = qty
        self._last_model = setup.model
        try:
            res = await self.broker.submit_bracket(setup, qty)
        except Exception as exc:  # noqa: BLE001
            # Log only the exception type — never str/repr (SDK exceptions may echo
            # request or auth material). On failure, RESET the entry guard (no order
            # landed, so no ENTRY fill will arrive) and do NOT call reporter.trade_taken;
            # the engine's WAIT_ENTRY window guard will expire the un-filled setup
            # naturally. Reset _last_model too so a failed submit never leaves stale
            # model attribution for a non-existent order.
            # BACKLOG: a fully-robust handler would reconcile whether the bracket
            # actually landed via get_order_by_client_id (deterministic client_order_id
            # lookup) — that broker primitive is a CRITICAL backlog item (Task 41+).
            self._pending_entry_qty = 0
            self._last_model = None
            self.log.error("order_submit_failed", error=type(exc).__name__)
            return
        self.log.info(
            "order_submitted",
            order_id=res.order_id,
            client_order_id=res.client_order_id,
            qty=qty,
        )
        await self.reporter.trade_taken(setup, qty, self.mode)

    def _build_trade_result(
        self, entry_fills: list[Fill], exit_fills: list[Fill], exit_reason: str
    ) -> TradeResult:
        """Delegate to the pure builder (spec §17a): share-weighted entry/exit
        prices over partial fills, qty = min(entry, exit), pnl_pct as a fraction."""
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
            # fill (abs(position_qty) >= intended entry qty). A partial entry must
            # NOT be treated as full (MED #13). Use abs() so SHORT entries
            # (position_qty < 0) are correctly detected; >= handles overshoot.
            self._entry_fills.append(fill)
            if self._pending_entry_qty > 0 and abs(fill.position_qty) >= self._pending_entry_qty:
                self.entry_fill = fill  # representative full-fill marker
                self._pending_entry_qty = 0  # idempotency: prevent duplicate on_entry_filled
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
                self.log.warning(
                    "exit_fill_no_open_trade",
                    leg_role=fill.leg_role,
                    position_qty=fill.position_qty,
                )
                return
            if not self._entry_fills:
                self.log.warning(
                    "exit_without_entry_fills",
                    leg_role=fill.leg_role,
                )
                self.entry_fill = None
                self._entry_fills = []
                self._exit_fills = []
                self._pending_entry_qty = 0
                return
            reason = fill.exit_reason or ("TARGET" if fill.leg_role == "TP" else "STOP")
            self._exit_fills.append(fill)
            # Only build TradeResult when position is fully closed (MED bug fix)
            if fill.position_qty == 0:
                self.trades.append(
                    self._build_trade_result(
                        list(self._entry_fills), list(self._exit_fills), reason
                    )
                )
                self.engine.on_trade_closed(fill)
                self.entry_fill = None
                self._entry_fills = []
                self._exit_fills = []
                self._pending_entry_qty = 0
            return

        if fill.leg_role == "FLATTEN":
            # Orchestrator synthesizes the FLATTEN TradeResult directly (§8 step 9);
            # the OCO legs were cancelled so the engine never sees this close.
            if self.entry_fill is None:
                self.log.warning(
                    "exit_fill_no_open_trade",
                    leg_role=fill.leg_role,
                    position_qty=fill.position_qty,
                )
                return
            if not self._entry_fills:
                self.log.warning(
                    "exit_without_entry_fills",
                    leg_role=fill.leg_role,
                )
                self.entry_fill = None
                self._entry_fills = []
                self._exit_fills = []
                self._pending_entry_qty = 0
                return
            self._exit_fills.append(fill)
            # Only build TradeResult when position is fully closed
            if fill.position_qty == 0:
                self.trades.append(
                    self._build_trade_result(
                        list(self._entry_fills), list(self._exit_fills), "FLATTEN"
                    )
                )
                self.entry_fill = None
                self._entry_fills = []
                self._exit_fills = []
                self._pending_entry_qty = 0
            return

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
        try:
            # cancel resting bracket children first (best-effort)
            await self.broker.cancel_all()
        except Exception as exc:  # noqa: BLE001
            self.log.warning("flatten_cancel_failed", error=type(exc).__name__)
        # ALWAYS flatten — the critical EOD safety action (idempotent)
        await self.broker.flatten()

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
            self.end_equity = self.start_equity or Decimal("0")
        return summary.build_session_summary(
            trades=self.trades,
            start_equity=self.start_equity if self.start_equity is not None else Decimal("0"),
            end_equity=self.end_equity if self.end_equity is not None else Decimal("0"),
            mode=self.mode,
            symbol=self.symbol,
            session_date=self.clock.now().date(),
            no_trade_reason=self._no_trade_reason(),
        )

    async def _emit_session_report(self) -> None:
        if self._reported:
            return
        # Stash the engine's opening range onto the reporter's shared Discord
        # client so DiscordReporter._range_meta reads a real bars_present instead
        # of defaulting to 0/T LOW CONFIDENCE. Duck-typed: harmless for LogReporter
        # (no _client) and when the range never formed (opening_range is None).
        client = getattr(self.reporter, "_client", None)
        if client is not None and self.engine.opening_range is not None:
            client.opening_range = self.engine.opening_range
        summary_obj = await self._build_session_summary()
        self.log.info(
            "session_report",
            total_pnl=str(summary_obj.total_pnl),
            wins=summary_obj.wins,
            losses=summary_obj.losses,
            breakevens=summary_obj.breakevens,
            no_trade_reason=summary_obj.no_trade_reason,
        )
        await self.reporter.session_report(summary_obj)
        self._reported = True

    # ------------------------------------------------------------------
    # run() lifecycle (spec §15): wall-clock loop wiring feed → aggregate →
    # engine.on_candle → react, with a background trade-updates drain and a
    # graceful, signal-aware shutdown.
    # ------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._stop.set)
        except (NotImplementedError, RuntimeError):
            # signal handlers unavailable (e.g. non-main thread / Windows /
            # test event loop) — skip; the loop still exits via the feed
            # draining and engine.is_done().
            pass

    def _aggregate(self, c1m: Candle) -> list[Candle]:
        out: list[Candle] = []
        closed = self._aggr.add(c1m)
        if closed is not None:
            out.append(closed)
        return out

    def _bucket_boundary_after(self, ts: datetime.datetime) -> datetime.datetime:
        """Wall-clock instant ``bar_grace_seconds`` after the close of the
        range_timeframe_min bucket containing ``ts`` (the boundary timer fire time)."""
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
        # A bad single fill logs+continues (never kills the drain); a stream-level
        # error logs, sets _stop, and the task EXITS cleanly — the main loop then
        # breaks and the finally fail-safe flatten + teardown run (never trade blind
        # to fills). Cancellation on teardown propagates as normal.
        try:
            async for fill in self.broker.trade_updates():
                try:
                    await self._on_fill(fill)
                except Exception:  # noqa: BLE001 - one bad fill must not kill the drain
                    self.log.error(
                        "on_fill_failed", leg_role=getattr(fill, "leg_role", "?")
                    )
                if self._stop.is_set():
                    break
        except asyncio.CancelledError:  # graceful task cancel on teardown
            raise
        except Exception:  # noqa: BLE001 - stream-level failure: stop, don't trade blind
            self.log.error("trade_updates_stream_failed")
            self._stop.set()

    async def run(self) -> None:
        self._install_signal_handlers()
        ok = await self._preflight()
        if not ok:
            self.log.info("run_exit_preflight_done", mode=self.mode)
            await self._teardown(report=False)
            return

        await self.approver.start()
        await self.reporter.start()
        # Start the live producers BEFORE draining fills / iterating candles.
        # Without these starts the bot is inert in production: feed.candles()
        # blocks forever (no bars enqueued) and trade_updates() never yields
        # (no fills). feed.start() is SYNC: it subscribes bars and schedules the
        # ws _run_forever() task on THIS running loop (safe — we are inside the
        # coroutine, so asyncio.ensure_future has a loop). broker.start_stream()
        # is async: it subscribes trade updates and schedules its own ws task.
        self.feed.start()
        await self.broker.start_stream()
        self._transports_started = True
        updates_task = asyncio.create_task(self._drain_trade_updates())

        try:
            async for c1m in self.feed.candles():
                if self._stop.is_set():
                    break
                self._last_1m = c1m  # latest transport price for re-validation (HIGH #9)
                now = self.clock.now()
                # EOD flatten fires exactly once (once-guard): repeating it would
                # re-tag flatten_coid and re-cancel/flatten on every later candle.
                if (
                    self.flatten_at is not None
                    and now >= self.flatten_at
                    and not self._flattened
                ):
                    self._flattened = True
                    await self._flatten_eod()
                emitted = self._aggregate(c1m)
                # If the 1m feed skipped past a bucket edge with no advancing candle,
                # fire the boundary timer so a missing-minute bucket still emits
                # (spec §8 / HIGH #7).
                if not emitted:
                    # Use ts_OPEN: Aggregator.add() buckets a 1m bar by its ts_open,
                    # so an edge-aligned last-minute bar (e.g. 09:44→09:45) belongs to
                    # the 09:30 bucket. Using ts_close would compute the NEXT bucket's
                    # boundary (one full timeframe too late) — off-by-one (Fix E).
                    boundary_ts = self._bucket_boundary_after(c1m.ts_open)
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
            except Exception:  # noqa: BLE001 - drain task already failed; must not
                # propagate out of finally and skip teardown (Fix B). Type only.
                self.log.error("drain_task_failed")
            # Fail-safe flatten (Fix C): if a position is still open and not already
            # flattened, flatten best-effort BEFORE teardown. Guarantees an abnormal
            # exit (feed.candles() raising, SIGINT, engine bug) never leaves a live
            # position. Normal completed trades reset entry_fill=None on close, and the
            # normal EOD flatten sets _flattened=True, so no double/re-flatten.
            if self.entry_fill is not None and not self._flattened:
                self._flattened = True
                try:
                    await self._flatten_eod()
                except Exception:  # noqa: BLE001
                    self.log.error("failsafe_flatten_failed")
            await self._teardown(report=True)

    async def _teardown(self, *, report: bool) -> None:
        # Emit the session report only if preflight captured start_equity (§15).
        if report and self.start_equity is not None:
            await self._emit_session_report()
        if self._transports_started:
            await self.broker.stop_stream()
        await self.feed.close()
        await self.approver.close()
        await self.reporter.close()
        self.log.info("shutdown_complete", mode=self.mode)
