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
from orb_bot.indicators import (
    ATR,
    detect_displacement,
    find_swing,
    is_strong_close,
    within,
)
from orb_bot.models import (
    Candle,
    Direction,
    Fill,
    Model,
    OpeningRange,
    OrderResult,
    Setup,
    State,
)

# Floating tolerance for the risk/reward floor comparison in validate_setup.
EPS = Decimal("1e-9")


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


def project_target(
    entry: Decimal, stop: Decimal, direction: Direction, rr: float
) -> Decimal:
    """Project the R-multiple target from entry/stop (sec.10 #1).

    risk = |entry - stop|; reward = risk * rr. LONG target above entry, SHORT
    below. The target is the price that yields ``rr`` units of reward per unit
    of risk.
    """
    risk = abs(entry - stop)
    reward = risk * Decimal(str(rr))
    if direction is Direction.LONG:
        return entry + reward
    return entry - reward


def validate_setup(setup: Setup, cfg: StrategyConfig) -> list[str]:
    """Guardrail validation (sec.9/sec.10): empty list == valid.

    Rejects an inverted stop/target (LONG must satisfy stop < entry < target;
    SHORT must satisfy target < entry < stop), a risk/reward below the
    configured floor, and a stop distance below ``min_stop_distance``.
    """
    reasons: list[str] = []
    if setup.direction is Direction.LONG:
        if setup.stop >= setup.entry:
            reasons.append("inverted: long stop not below entry")
        if setup.target <= setup.entry:
            reasons.append("inverted: long target not above entry")
    else:  # SHORT
        if setup.stop <= setup.entry:
            reasons.append("inverted: short stop not above entry")
        if setup.target >= setup.entry:
            reasons.append("inverted: short target not below entry")
    stop_dist = abs(setup.entry - setup.stop)
    if stop_dist < Decimal(str(cfg.min_stop_distance)):
        reasons.append("stop distance below min_stop_distance")
    if Decimal(str(setup.rr)) < Decimal(str(cfg.risk_reward_ratio)) - EPS:
        reasons.append("rr below risk_reward_ratio floor")
    return reasons


def _buffer(cfg: StrategyConfig, atr_value: Decimal) -> Decimal:
    """Stop buffer per sec.10 #1: max(stop_buffer_ticks*tick, stop_buffer_atr*ATR)."""
    ticks = Decimal(str(cfg.stop_buffer_ticks)) * Decimal(str(cfg.tick_size))
    atr_buf = Decimal(str(cfg.stop_buffer_atr)) * atr_value
    return max(ticks, atr_buf)


def _setup_from(
    direction: Direction,
    model: Model,
    entry: Decimal,
    stop: Decimal,
    cfg: StrategyConfig,
    reason: list[str],
) -> Setup:
    """Build a Setup with a target projected at the RR floor and the realized
    rr computed from the chosen stop (so a swing/fallback stop reports its
    actual reward-to-risk)."""
    target = project_target(entry, stop, direction, cfg.risk_reward_ratio)
    risk = abs(entry - stop)
    rr = float(abs(target - entry) / risk) if risk > 0 else 0.0
    return Setup(
        direction=direction,
        model=model,
        entry=entry,
        stop=stop,
        target=target,
        rr=rr,
        reason=reason,
    )


def entry_breakout(ctx: Context, candle: Candle, cfg: StrategyConfig) -> Setup | None:
    """Breakout entry (sec.9): eligible on every WAIT_ENTRY candle INCLUDING the
    confirmation candle, but only fires when ``detect_displacement`` finds a
    displacement in the entry window. Enters at the candle close; stop is
    ``break_level ± buffer``; target via ``project_target``."""
    disp = detect_displacement(
        ctx.entry_window, cfg.displacement_model, ctx.atr.value, cfg
    )
    if disp is None:
        return None
    direction = ctx.direction
    assert direction is not None
    assert ctx.break_level is not None
    entry = candle.close
    buf = _buffer(cfg, ctx.atr.value)
    if direction is Direction.LONG:
        stop = ctx.break_level - buf
    else:
        stop = ctx.break_level + buf
    return _setup_from(
        direction,
        Model.BREAKOUT,
        entry,
        stop,
        cfg,
        [f"breakout {direction.value}", f"displacement={disp.type}"],
    )


def entry_retest(ctx: Context, candle: Candle, cfg: StrategyConfig) -> Setup | None:
    """Retest entry (sec.9, preferred): a LATER WAIT_ENTRY candle that returns to
    ``break_level`` (within retest_tolerance_atr*ATR) AND holds with a strong
    close in the trade direction. Stop from ``find_swing`` (kind by direction);
    if find_swing returns None, FALL BACK to ``break_level ± buffer``. Enters at
    the candle close; target via ``project_target``."""
    direction = ctx.direction
    assert direction is not None
    assert ctx.break_level is not None
    tol = Decimal(str(cfg.retest_tolerance_atr)) * ctx.atr.value
    if not within(candle, ctx.break_level, tol):
        return None
    if not is_strong_close(
        candle,
        direction,
        cfg.retest_confirm_body_ratio,
        cfg.strong_close_location,
    ):
        return None
    kind = "low" if direction is Direction.LONG else "high"
    swing = find_swing(ctx.entry_window, cfg.swing_fractal_k, cfg.swing_lookback, kind)
    buf = _buffer(cfg, ctx.atr.value)
    if swing is not None:
        stop = swing
        stop_src = "swing"
    elif direction is Direction.LONG:
        stop = ctx.break_level - buf
        stop_src = "fallback"
    else:
        stop = ctx.break_level + buf
        stop_src = "fallback"
    entry = candle.close
    return _setup_from(
        direction,
        Model.RETEST,
        entry,
        stop,
        cfg,
        [f"retest {direction.value}", f"stop={stop_src}"],
    )


def try_build_entry(ctx: Context, candle: Candle, cfg: StrategyConfig) -> Setup | None:
    """Per-candle entry attempt (sec.9 eligibility + sec.10 #3): try retest first
    (preferred), then breakout; the first to pass ``validate_setup`` wins. If no
    valid setup, advance the retest wait counter on EVERY WAIT_ENTRY candle and
    latch ``retest_dead`` at the cap (counter-based abandonment regardless of
    proximity). Reversal is DEFERRED (not attempted)."""
    candidates = []
    if cfg.enable_retest and not ctx.retest_dead:
        candidates.append(entry_retest)
    if cfg.enable_breakout:
        candidates.append(entry_breakout)
    for build in candidates:
        setup = build(ctx, candle, cfg)
        if setup is not None and not validate_setup(setup, cfg):
            return setup
    # No valid setup this candle: advance the retest wait counter and abandon at cap.
    if cfg.enable_retest and not ctx.retest_dead:
        ctx.retest_wait += 1
        if ctx.retest_wait >= cfg.retest_max_wait_candles:
            ctx.retest_dead = True
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
        # Entry-fill context captured on on_entry_filled and consumed on
        # on_trade_closed to compute realized P/L sign-correctly.
        self._entry_direction: Direction | None = None
        self._entry_price: Decimal | None = None
        self._entry_qty: int = 0

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
        if self.ctx.state is State.WAIT_ENTRY:
            return self._on_candle_wait_entry(c)
        # IN_TRADE: the open trade is managed by the orchestrator/execution
        # bracket; the engine only advances on the lifecycle callbacks
        # (on_trade_closed). No per-candle action here.
        self.ctx.last_ts = c.ts_close
        return [m.NoOp()]

    def _on_candle_wait_entry(self, c: Candle) -> list[m.EngineEvent]:
        # Window guard FIRST: never act after trading_window_min (sec.9 guardrail).
        # A past-window candle must not buffer, advance the retest wait, or build
        # an entry -- the early return prevents all of it (mirrors WAIT_CONFIRMATION).
        if self._past_window(c):
            self.ctx.state = State.DONE
            self.ctx.last_ts = c.ts_close
            return [m.WindowExpired()]
        # Buffer this candle into the entry window (capped to swing_lookback so
        # find_swing/detect_displacement only see the relevant recent bars), then
        # attempt an entry. This is the single source of truth for entry-window
        # buffering and last_ts in WAIT_ENTRY.
        self.ctx.entry_window.append(c)
        if len(self.ctx.entry_window) > self.cfg.swing_lookback:
            self.ctx.entry_window = self.ctx.entry_window[-self.cfg.swing_lookback :]
        self.ctx.last_ts = c.ts_close
        setup = try_build_entry(self.ctx, c, self.cfg)
        if setup is not None:
            return [m.SetupProposed(setup=setup)]
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

    # --- lifecycle callbacks (orchestrator-driven) --------------------------
    def on_approval(self, decision: str) -> list[m.EngineEvent]:
        """Approval-gate result. APPROVE -> EntryConfirmed (the orchestrator
        submits the bracket and awaits the fill); state stays WAIT_ENTRY and NO
        slot is consumed here (the slot is consumed only on the fill).
        REJECT / TIMEOUT -> remain in WAIT_ENTRY, consume no slot."""
        if decision == "APPROVE":
            return [m.EntryConfirmed()]
        # REJECT / TIMEOUT: no slot consumed, remain in WAIT_ENTRY.
        return [m.NoOp()]

    def on_entry_filled(self, res: OrderResult) -> list[m.EngineEvent]:
        """Entry fill confirmed by execution. Transition to IN_TRADE and
        DECREMENT ``trades_remaining`` here -- this is the SINGLE point a trade
        slot is consumed (sec.9/sec.8 step 8). Captures the entry context
        (direction/price/qty) so realized P/L can be computed on close."""
        self.ctx.state = State.IN_TRADE
        self.ctx.trades_remaining -= 1
        self._entry_direction = self.ctx.direction
        self._entry_price = res.filled_avg_price
        self._entry_qty = res.filled_qty
        return [m.NoOp()]

    def adopt_open_position(self, qty: int, entry_fill: Fill | None) -> None:
        """sec.16: restart-into-open-position reconciliation. Place the engine in
        the exact IN_TRADE shape a live in-trade would have so that after a
        mid-session restart it ONLY monitors/flattens (never re-establishes a
        range or re-confirms a direction this session) and ``on_trade_closed``
        advances correctly. Mirrors ``on_entry_filled`` (enters IN_TRADE, captures
        the entry context used to compute realized P/L on close), with three
        restart-specific guards:

        - ``qty`` MUST be non-zero (a zero position is not an open trade).
        - ``entry_fill`` MAY be None when the original entry price is unknown
          (the entry order could not be looked up): ``_entry_price`` is left None
          and ``on_trade_closed`` falls back to the closing fill price -> ~0 P/L
          for the adopted trade rather than a fictitious one.
        - ``trades_remaining`` is forced to 0 (NOT decremented). After a
          mid-session restart there is NO established opening_range/ATR, so
          re-arming for a NEW trade would crash; a restart-adopted trade must be
          the last trade of the day (flatten at EOD, never re-enter). At the
          default max_trades_per_day=1 this is identical to -=1.

        Direction is derived from the position sign (qty>0 -> LONG, qty<0 ->
        SHORT)."""
        if qty == 0:
            raise ValueError("adopt_open_position requires a non-zero position qty")
        direction = Direction.LONG if qty > 0 else Direction.SHORT
        self.ctx.direction = direction
        self.ctx.state = State.IN_TRADE
        self.ctx.trades_remaining = 0
        self._entry_direction = direction
        self._entry_price = entry_fill.price if entry_fill is not None else None
        self._entry_qty = abs(qty)

    def on_trade_closed(self, fill: Fill) -> list[m.EngineEvent]:
        """Closing fill for the open trade. Compute realized P/L sign-correctly,
        emit TradeRecorded, then re-arm to WAIT_CONFIRMATION (carrying over the
        range/ATR/sweep/range-day latches; resetting direction/break_level and
        the retest counters) when ``max_trades_per_day > 1``, a slot remains, and
        the trading window has not elapsed; otherwise finish (DONE). No re-decrement."""
        entry_px = self._entry_price if self._entry_price is not None else fill.price
        qty = self._entry_qty
        if self._entry_direction is Direction.SHORT:
            pnl = (entry_px - fill.price) * qty
        else:
            pnl = (fill.price - entry_px) * qty
        events: list[m.EngineEvent] = [
            m.TradeRecorded(pnl=pnl, exit_reason=fill.exit_reason or "UNKNOWN")
        ]
        # sec.10 #14: remember the just-traded direction BEFORE resetting it so a
        # same-direction re-confirmation can be rejected when rearm_opposite_only.
        self._last_traded_direction = self._entry_direction
        # sec.10 NIT: if this close already lands past the trading window, do not
        # re-arm -- finish the session.
        window_expired = self._fill_past_window(fill)
        if (
            self.cfg.max_trades_per_day > 1
            and self.ctx.trades_remaining > 0
            and not window_expired
        ):
            self.ctx.state = State.WAIT_CONFIRMATION
            self.ctx.direction = None
            self.ctx.break_level = None
            self.ctx.retest_wait = 0
            self.ctx.retest_dead = False
            self.ctx.entry_window = []
            # range, atr, swept_high/low, range_day latches carry over (untouched).
        else:
            self.ctx.state = State.DONE
        self._entry_direction = None
        self._entry_price = None
        self._entry_qty = 0
        return events

    def _fill_past_window(self, fill: Fill) -> bool:
        """Event-time window check for a closing fill (off fill.ts, never the
        host clock). Returns False when the fill carries no timestamp."""
        if fill.ts is None:
            return False
        so = _session_open_time(self.cfg)
        base = dt.datetime.combine(self.session_date, so, tzinfo=fill.ts.tzinfo)
        window_close = base + dt.timedelta(minutes=self.cfg.trading_window_min)
        return fill.ts >= window_close
