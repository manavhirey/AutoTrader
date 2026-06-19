"""Orchestrator: owns the asyncio loop, the wall clock, all wall-clock timing
(session gating, bucket-boundary timers, flatten scheduling) and the I/O
reactions to engine events. The engine stays pure (spec §15)."""
from __future__ import annotations

import asyncio
import datetime
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
        self._pending_entry_qty: int = 0
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
            feed=self.run.feed,
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
            feed=self.run.feed,
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
        try:
            res = await self.broker.submit_bracket(setup, qty)
        except Exception as exc:  # noqa: BLE001
            # Log only the exception type — never str/repr (SDK exceptions may echo
            # request or auth material). On failure, do NOT set _last_model and do NOT
            # call reporter.trade_taken; the engine's WAIT_ENTRY window guard will
            # expire the un-filled setup naturally.
            # BACKLOG: a fully-robust handler would reconcile whether the bracket
            # actually landed via get_order_by_client_id (deterministic client_order_id
            # lookup) — that broker primitive is a CRITICAL backlog item (Task 41+).
            self.log.error("order_submit_failed", error=type(exc).__name__)
            return
        # Record the submitted setup's model so the eventual TradeResult is
        # attributed correctly (MED #11). Set on each successful submit; remains
        # until the next successful submit (P/L-attribution consumer lands in Task 41).
        self._last_model = setup.model
        self._pending_entry_qty = qty
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
