"""Tests for orb_bot.engine PART 1: Context, init, seed_atr, range-building and
breakout confirmation (IDLE -> BUILDING_RANGE -> RANGE_SET -> WAIT_CONFIRMATION
-> WAIT_ENTRY). Pure, event-time driven (no wall clock)."""
import datetime as dt
import pathlib
from dataclasses import fields, is_dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot import engine as eng
from orb_bot import models as m
from orb_bot.config import StrategyConfig
from orb_bot.indicators import ATR
from orb_bot.models import Candle, Direction, State

ET = ZoneInfo("America/New_York")
SESSION_DATE = dt.date(2026, 6, 19)


def _cfg(**over):
    base = dict(
        enable_breakout=True,
        enable_retest=True,
        enable_reversal=False,
        require_higher_tf_bias=False,
        max_trades_per_day=1,
    )
    base.update(over)
    return StrategyConfig(**base)


def make_candle(
    hhmm,
    o,
    h,
    lo,
    c,
    *,
    tf=15,
    volume=1000,
    bars_present=15,
    data_incomplete=False,
    date=SESSION_DATE,
):
    hh, mm = (int(x) for x in hhmm.split(":"))
    ts_open = dt.datetime(date.year, date.month, date.day, hh, mm, tzinfo=ET)
    ts_close = ts_open + dt.timedelta(minutes=tf)
    return Candle(
        ts_open=ts_open,
        ts_close=ts_close,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=volume,
        timeframe_min=tf,
        data_incomplete=data_incomplete,
        bars_present=bars_present,
    )


def feed_candles(engine, candles):
    """Feed candles, return list of (state, events) after each."""
    out = []
    for c in candles:
        evs = engine.on_candle(c)
        out.append((engine.ctx.state, evs))
    return out


# --- Step 1: Context dataclass + init ---------------------------------------
def test_context_has_exact_contract_fields():
    expected = {
        "state",
        "opening_range",
        "direction",
        "break_level",
        "swept_high",
        "swept_low",
        "range_day",
        "retest_wait",
        "retest_dead",
        "trades_remaining",
        "entry_window",
        "atr",
        "session_date",
        "last_ts",
    }
    assert is_dataclass(eng.Context)
    assert {f.name for f in fields(eng.Context)} == expected


def test_engine_init_idle():
    e = eng.Engine(_cfg(), SESSION_DATE)
    assert e.ctx.state is State.IDLE
    assert e.ctx.opening_range is None
    assert e.ctx.direction is None
    assert e.ctx.break_level is None
    assert e.ctx.swept_high is False
    assert e.ctx.swept_low is False
    assert e.ctx.range_day is False
    assert e.ctx.retest_wait == 0
    assert e.ctx.retest_dead is False
    assert e.ctx.trades_remaining == 1
    assert e.ctx.entry_window == []
    assert isinstance(e.ctx.atr, ATR)
    assert e.ctx.session_date == SESSION_DATE
    assert e.ctx.last_ts is None
    assert e.is_done() is False


# --- Step 3: seed_atr + session-open derivation -----------------------------
def test_seed_atr_makes_atr_ready():
    e = eng.Engine(_cfg(atr_period=3), SESSION_DATE)
    prev = dt.date(2026, 6, 18)
    bars = [
        make_candle("09:30", 100, 101, 99, 100, date=prev),
        make_candle("09:45", 100, 102, 99, 101, date=prev),
        make_candle("10:00", 101, 103, 100, 102, date=prev),
        make_candle("10:15", 102, 104, 101, 103, date=prev),
    ]
    assert e.ctx.atr.ready is False
    e.seed_atr(bars)
    assert e.ctx.atr.ready is True
    assert e.ctx.atr.value > Decimal("0")


def test_session_open_time_parsed():
    # _cfg(session_open=...) is coerced by StrategyConfig to a datetime.time;
    # _session_open_time returns it unchanged.
    cfg = _cfg(session_open=dt.time(9, 30))
    assert isinstance(cfg.session_open, dt.time)
    assert eng._session_open_time(cfg) == dt.time(9, 30)


# --- Step 4: range establishment (write-once) -------------------------------
def test_first_candle_establishes_range_and_emits():
    e = eng.Engine(_cfg(or_min_bars=15), SESSION_DATE)
    c = make_candle("09:30", 100, 105, 98, 102, bars_present=15)
    evs = e.on_candle(c)
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert len(evs) == 1
    assert isinstance(evs[0], m.RangeEstablished)
    or_ = evs[0].opening_range
    assert or_.high == Decimal("105")
    assert or_.low == Decimal("98")
    assert or_.width == Decimal("7")
    assert or_.established_at == c.ts_close
    assert or_.bars_present == 15
    assert or_.low_confidence is False
    assert or_.feed == ""
    assert e.ctx.opening_range is or_
    assert e.ctx.last_ts == c.ts_close


def test_partial_range_marks_low_confidence():
    e = eng.Engine(_cfg(or_min_bars=15), SESSION_DATE)
    c = make_candle("09:30", 100, 105, 98, 102, bars_present=9)
    evs = e.on_candle(c)
    assert isinstance(evs[0], m.RangeEstablished)
    assert evs[0].opening_range.low_confidence is True
    assert evs[0].opening_range.bars_present == 9
    assert e.ctx.state is State.WAIT_CONFIRMATION


def test_zero_child_range_refused_done():
    # sec.10 #13: bars_present == 0 -> never fabricate a range -> IDLE->DONE
    e = eng.Engine(_cfg(), SESSION_DATE)
    c = make_candle(
        "09:30", 0, 0, 0, 0, bars_present=0, data_incomplete=True
    )
    evs = e.on_candle(c)
    assert e.ctx.state is State.DONE
    assert e.is_done() is True
    assert any(isinstance(ev, m.WindowExpired) for ev in evs) or any(
        isinstance(ev, m.NoOp) for ev in evs
    )


def test_range_is_write_once_late_first_candle_does_not_refabricate():
    # A non-09:30 candle arriving while IDLE must not set a range.
    e = eng.Engine(_cfg(), SESSION_DATE)
    late = make_candle("09:45", 100, 110, 90, 105)
    evs = e.on_candle(late)
    assert e.ctx.opening_range is None
    assert e.ctx.state is State.DONE  # cannot establish OR off a non-open candle
    assert any(isinstance(ev, (m.NoOp, m.WindowExpired)) for ev in evs)


# --- Step 6: pure helpers (sweeps / range-day / day-type filter) -------------
def _ctx_after_range(e, or_high=105, or_low=98, bars=15):
    e.on_candle(make_candle("09:30", 100, or_high, or_low, 102, bars_present=bars))
    return e.ctx


def test_track_sweeps_sets_high_then_low():
    e = eng.Engine(_cfg(sweep_buffer=0.0), SESSION_DATE)
    ctx = _ctx_after_range(e)
    # wick above OR high but not below low
    up = make_candle("09:45", 103, 107, 101, 104)
    eng.track_sweeps(ctx, up, e.cfg)
    assert ctx.swept_high is True
    assert ctx.swept_low is False
    # later wick below OR low
    down = make_candle("10:00", 100, 102, 96, 99)
    eng.track_sweeps(ctx, down, e.cfg)
    assert ctx.swept_low is True


def test_is_range_day_requires_both_sweeps():
    e = eng.Engine(_cfg(range_day_sweep_both=True), SESSION_DATE)
    ctx = _ctx_after_range(e)
    ctx.swept_high = True
    ctx.swept_low = False
    assert eng.is_range_day(ctx, e.cfg) is False
    ctx.swept_low = True
    assert eng.is_range_day(ctx, e.cfg) is True


def test_is_range_day_false_when_sweep_both_disabled():
    # sec.7: is_range_day = range_day_sweep_both AND swept_high AND swept_low.
    # With range_day_sweep_both=False, range-day detection is OFF: always False
    # even when both sides are swept (no `or` self-suppression branch).
    e = eng.Engine(_cfg(range_day_sweep_both=False), SESSION_DATE)
    ctx = _ctx_after_range(e)
    ctx.swept_high = True
    ctx.swept_low = False
    assert eng.is_range_day(ctx, e.cfg) is False
    ctx.swept_low = True
    assert eng.is_range_day(ctx, e.cfg) is False


def test_apply_day_type_filter_disables_breakout_and_retest():
    e = eng.Engine(
        _cfg(
            enable_breakout=True,
            enable_retest=True,
            range_day_disables=["breakout", "retest"],
            range_day_enables=[],
        ),
        SESSION_DATE,
    )
    ctx = _ctx_after_range(e)
    eng.apply_day_type_filter(ctx, e.cfg)
    assert ctx.range_day is True
    # filter latches the range_day flag; model gating reads cfg + range_day.
    assert e._breakout_enabled() is False
    assert e._retest_enabled() is False


# --- Step 8: confirmed_breakout (close-based strong close) -------------------
def test_confirmed_breakout_long_strong_close():
    e = eng.Engine(
        _cfg(strong_close_body_ratio=0.60, strong_close_location=0.70),
        SESSION_DATE,
    )
    ctx = _ctx_after_range(e, or_high=105, or_low=98)
    # close (108) well above OR high (105), strong bullish body, closes near high
    c = make_candle("09:45", 105.5, 108.2, 105.0, 108.0)
    d = eng.confirmed_breakout(c, ctx.opening_range, e.cfg)
    assert d is Direction.LONG


def test_confirmed_breakout_short_strong_close():
    e = eng.Engine(_cfg(), SESSION_DATE)
    ctx = _ctx_after_range(e, or_high=105, or_low=98)
    c = make_candle("09:45", 97.5, 98.0, 94.0, 94.2)
    d = eng.confirmed_breakout(c, ctx.opening_range, e.cfg)
    assert d is Direction.SHORT


def test_confirmed_breakout_wick_only_close_inside_returns_none():
    e = eng.Engine(_cfg(), SESSION_DATE)
    ctx = _ctx_after_range(e, or_high=105, or_low=98)
    # high pokes above 105 but close (104) is back inside the range -> no confirm
    c = make_candle("09:45", 103, 107, 102, 104)
    assert eng.confirmed_breakout(c, ctx.opening_range, e.cfg) is None


def test_confirmed_breakout_weak_close_returns_none():
    e = eng.Engine(
        _cfg(strong_close_body_ratio=0.60, strong_close_location=0.70),
        SESSION_DATE,
    )
    ctx = _ctx_after_range(e, or_high=105, or_low=98)
    # closes above 105 but tiny body / closes mid-bar -> weak close -> no confirm
    c = make_candle("09:45", 105.4, 109.0, 105.1, 105.6)
    assert eng.confirmed_breakout(c, ctx.opening_range, e.cfg) is None


# --- Step 10: WAIT_CONFIRMATION fixed-order wiring + window expiry -----------
def test_on_candle_confirm_breakout_long_transitions_wait_entry():
    e = eng.Engine(_cfg(), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    evs = e.on_candle(make_candle("09:45", 105.5, 108.2, 105.0, 108.0))
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.direction is Direction.LONG
    assert e.ctx.break_level == Decimal("105")
    assert any(
        isinstance(ev, m.DirectionConfirmed)
        and ev.direction is Direction.LONG
        and ev.break_level == Decimal("105")
        for ev in evs
    )


def test_on_candle_confirm_breakout_short_transitions_wait_entry():
    e = eng.Engine(_cfg(), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    evs = e.on_candle(make_candle("09:45", 97.5, 98.0, 94.0, 94.2))
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.direction is Direction.SHORT
    assert e.ctx.break_level == Decimal("98")
    assert any(
        isinstance(ev, m.DirectionConfirmed)
        and ev.direction is Direction.SHORT
        and ev.break_level == Decimal("98")
        for ev in evs
    )


# --- Fix: direction_allowed gate (sec.4 line 159 / sec.7 lines 305-307) ------
def test_allow_long_false_blocks_long_breakout_no_confirm():
    # allow_long=False: a strong LONG breakout must NOT confirm -- the engine
    # stays in WAIT_CONFIRMATION with no direction and no DirectionConfirmed.
    e = eng.Engine(_cfg(allow_long=False), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    evs = e.on_candle(make_candle("09:45", 105.5, 108.2, 105.0, 108.0))
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert e.ctx.direction is None
    assert e.ctx.break_level is None
    assert all(not isinstance(ev, m.DirectionConfirmed) for ev in evs)


def test_allow_long_false_still_allows_short_breakout():
    # The allowed (SHORT) side still confirms when only LONG is blocked.
    e = eng.Engine(_cfg(allow_long=False), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    evs = e.on_candle(make_candle("09:45", 97.5, 98.0, 94.0, 94.2))
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.direction is Direction.SHORT
    assert e.ctx.break_level == Decimal("98")
    assert any(
        isinstance(ev, m.DirectionConfirmed) and ev.direction is Direction.SHORT
        for ev in evs
    )


def test_allow_short_false_blocks_short_breakout_no_confirm():
    # allow_short=False: a strong SHORT breakout must NOT confirm.
    e = eng.Engine(_cfg(allow_short=False), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    evs = e.on_candle(make_candle("09:45", 97.5, 98.0, 94.0, 94.2))
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert e.ctx.direction is None
    assert e.ctx.break_level is None
    assert all(not isinstance(ev, m.DirectionConfirmed) for ev in evs)


def test_allow_short_false_still_allows_long_breakout():
    # The allowed (LONG) side still confirms when only SHORT is blocked.
    e = eng.Engine(_cfg(allow_short=False), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    evs = e.on_candle(make_candle("09:45", 105.5, 108.2, 105.0, 108.0))
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.direction is Direction.LONG
    assert e.ctx.break_level == Decimal("105")
    assert any(
        isinstance(ev, m.DirectionConfirmed) and ev.direction is Direction.LONG
        for ev in evs
    )


def test_on_candle_wick_only_no_transition():
    e = eng.Engine(_cfg(), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))
    evs = e.on_candle(make_candle("09:45", 103, 107, 102, 104))  # close inside
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert e.ctx.direction is None
    assert all(not isinstance(ev, m.DirectionConfirmed) for ev in evs)


def test_on_candle_weak_close_no_transition():
    e = eng.Engine(
        _cfg(strong_close_body_ratio=0.60, strong_close_location=0.70),
        SESSION_DATE,
    )
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))
    evs = e.on_candle(make_candle("09:45", 105.4, 109.0, 105.1, 105.6))
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert all(not isinstance(ev, m.DirectionConfirmed) for ev in evs)


def test_range_day_suppresses_same_bar_breakout():
    # Both sides swept on the confirm bar: range-day latches BEFORE confirmation
    # (sec.10 #4), disabling breakout/retest -> no DirectionConfirmed even if the
    # close is a strong breakout.
    e = eng.Engine(
        _cfg(
            range_day_sweep_both=True,
            range_day_disables=["breakout", "retest"],
            range_day_enables=[],
        ),
        SESSION_DATE,
    )
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    # pre-sweep the low on an earlier candle
    e.on_candle(make_candle("09:45", 101, 104, 96, 100))  # sweeps low, close inside
    assert e.ctx.swept_low is True
    assert e.ctx.state is State.WAIT_CONFIRMATION
    # now a candle that sweeps high AND closes strong above -> both sides swept
    evs = e.on_candle(make_candle("10:00", 105.5, 109.0, 105.0, 108.5))
    assert e.ctx.range_day is True
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert e.ctx.direction is None
    assert any(isinstance(ev, m.RangeDayDetected) for ev in evs)
    assert all(not isinstance(ev, m.DirectionConfirmed) for ev in evs)


def test_sweep_both_false_does_not_self_suppress_breakout():
    # sec.7: with range_day_sweep_both=False, is_range_day is always False, so a
    # normal breakout is NOT self-suppressed -- range_day never latches and the
    # breakout confirms to WAIT_ENTRY (even after both sides have been swept).
    e = eng.Engine(
        _cfg(
            range_day_sweep_both=False,
            range_day_disables=["breakout", "retest"],
            range_day_enables=[],
        ),
        SESSION_DATE,
    )
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))  # range
    # pre-sweep the low (close back inside) -- one side swept
    e.on_candle(make_candle("09:45", 101, 104, 96, 100))
    assert e.ctx.swept_low is True
    assert e.ctx.range_day is False
    # candle sweeps high AND closes strong above: both sides now swept, but
    # range-day detection is disabled -> no suppression -> confirm.
    evs = e.on_candle(make_candle("10:00", 105.5, 109.0, 105.0, 108.5))
    assert e.ctx.range_day is False
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.direction is Direction.LONG
    assert all(not isinstance(ev, m.RangeDayDetected) for ev in evs)
    assert any(isinstance(ev, m.DirectionConfirmed) for ev in evs)


def test_window_expiry_emits_window_expired_and_done():
    # trading_window_min=120 -> window closes at 11:30; a candle whose ts_close
    # is at/after that while still unconfirmed -> WindowExpired + DONE.
    e = eng.Engine(_cfg(trading_window_min=120), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))
    # feed an in-window non-confirming candle (close inside)
    e.on_candle(make_candle("09:45", 102, 104, 100, 103))
    assert e.ctx.state is State.WAIT_CONFIRMATION
    # 11:15 candle closes at 11:30 == window end -> expired
    evs = e.on_candle(make_candle("11:15", 102, 104, 100, 103))
    assert e.ctx.state is State.DONE
    assert any(isinstance(ev, m.WindowExpired) for ev in evs)


# --- Step 12: purity invariant + entry-window seeding -----------------------
def test_engine_module_has_no_wall_clock_calls():
    src = pathlib.Path(eng.__file__).read_text()
    assert "datetime.now(" not in src
    assert ".now(" not in src.replace("ts_close", "")  # no .now() usage
    assert "time.time(" not in src


def test_confirm_candle_buffered_into_entry_window():
    e = eng.Engine(_cfg(), SESSION_DATE)
    e.on_candle(make_candle("09:30", 100, 105, 98, 102))
    confirm = make_candle("09:45", 105.5, 108.2, 105.0, 108.0)
    e.on_candle(confirm)
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.entry_window[-1] is confirm


def test_done_state_is_terminal_noop():
    # Once DONE, any further candle is a no-op and never re-enters the machine.
    e = eng.Engine(_cfg(), SESSION_DATE)
    e.on_candle(make_candle("09:30", 0, 0, 0, 0, bars_present=0, data_incomplete=True))
    assert e.is_done() is True
    evs = e.on_candle(make_candle("09:45", 100, 110, 90, 105))
    assert e.ctx.state is State.DONE
    assert all(isinstance(ev, m.NoOp) for ev in evs)
