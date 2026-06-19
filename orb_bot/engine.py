"""Pure, I/O-free, clock-free single-timeframe ORB state machine (spec sec.9).

Imports NOTHING from feed/execution/approval/reporting/orchestrator/discordbot
and NEVER reads the host clock. All timing is driven off candle.ts_close
(event time).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from orb_bot import indicators
from orb_bot import models as m
from orb_bot.config import StrategyConfig
from orb_bot.indicators import ATR
from orb_bot.models import Candle, Direction, OpeningRange, State


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
    """sec.7: range day = range_day_sweep_both AND swept_high AND swept_low.

    When range_day_sweep_both is False, range-day detection is disabled entirely
    (always False) -- it only applies when configured for a both-sides sweep.
    """
    return cfg.range_day_sweep_both and ctx.swept_high and ctx.swept_low


def apply_day_type_filter(ctx: Context, cfg: StrategyConfig) -> None:
    """sec.10 #4: latch range_day. The model-enable helpers consult this latch;
    with reversal deferred (range_day_enables == []) no model is re-enabled."""
    ctx.range_day = True


def confirmed_breakout(
    candle: Candle, opening_range: OpeningRange, cfg: StrategyConfig
) -> Direction | None:
    """sec.8 step 4 / sec.9: close-based confirmation only. A candle confirms
    LONG iff it CLOSES above OR high AND is a strong bullish close; SHORT iff it
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
        has to reach into ``self.ctx``."""
        return self.ctx.opening_range

    def _is_first_session_candle(self, c: Candle) -> bool:
        return (
            c.timeframe_min == self.cfg.range_timeframe_min
            and c.ts_open.date() == self.session_date
            and c.ts_open.timetz().replace(tzinfo=None)
            == _session_open_time(self.cfg)
        )

    def _establish_range(self, c: Candle) -> list[m.EngineEvent]:
        bars_present = c.bars_present if c.bars_present is not None else 0
        # sec.10 #13: a zero-child force-closed bucket carries no usable OHLC --
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
        if self.ctx.state is State.WAIT_CONFIRMATION:
            return self._on_candle_wait_confirmation(c)
        # WAIT_ENTRY / IN_TRADE handled in Engine PART 2.
        self.ctx.last_ts = c.ts_close
        return [m.NoOp()]

    def _past_window(self, c: Candle) -> bool:
        # Event-time check off ts_close; never the host clock.
        so = _session_open_time(self.cfg)
        base = dt.datetime.combine(
            self.session_date, so, tzinfo=c.ts_close.tzinfo
        )
        window_close = base + dt.timedelta(minutes=self.cfg.trading_window_min)
        return c.ts_close >= window_close

    def _on_candle_wait_confirmation(self, c: Candle) -> list[m.EngineEvent]:
        events: list[m.EngineEvent] = []
        # Window guard FIRST: never act after trading_window_min (sec.9 guardrail).
        if self._past_window(c):
            self.ctx.state = State.DONE
            self.ctx.last_ts = c.ts_close
            return [m.WindowExpired()]
        # sec.10 #4 fixed order: track_sweeps -> is_range_day/apply_day_type_filter
        # -> confirmed_breakout.
        track_sweeps(self.ctx, c, self.cfg)
        if not self.ctx.range_day and is_range_day(self.ctx, self.cfg):
            apply_day_type_filter(self.ctx, self.cfg)
            events.append(m.RangeDayDetected())
        # A latched range day with nothing re-enabled suppresses confirmation
        # (incl. a same-bar breakout).
        if self._breakout_enabled() or self._retest_enabled():
            assert self.ctx.opening_range is not None
            direction = confirmed_breakout(c, self.ctx.opening_range, self.cfg)
            # sec.10 #14: on a re-arm (after a trade closed) with
            # rearm_opposite_only, reject a same-direction re-confirmation -- only
            # the opposite side may take the remaining slot.
            if (
                direction is not None
                and self.cfg.rearm_opposite_only
                and direction is self._last_traded_direction
            ):
                direction = None
            # sec.4/sec.7: gate confirmation by direction_allowed; a disallowed
            # side never confirms (stay WAIT_CONFIRMATION, direction stays None).
            if direction is not None and not self._direction_allowed(direction):
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

    def _direction_allowed(self, direction: Direction) -> bool:
        """sec.7 direction_allowed: LONG gated by cfg.allow_long, SHORT by
        cfg.allow_short (higher-TF bias handling lives upstream / deferred)."""
        if direction is Direction.LONG:
            return self.cfg.allow_long
        return self.cfg.allow_short

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
