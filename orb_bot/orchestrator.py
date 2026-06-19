"""Orchestrator: owns the asyncio loop, the wall clock, all wall-clock timing
(session gating, bucket-boundary timers, flatten scheduling) and the I/O
reactions to engine events. The engine stays pure (spec §15)."""
from __future__ import annotations

import asyncio
import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot import logconf
from orb_bot.aggregation import Aggregator
from orb_bot.execution import alpaca as execution_alpaca
from orb_bot.models import (
    Candle,
    Fill,
    Model,
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
