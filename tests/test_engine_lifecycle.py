from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot import engine as eng
from orb_bot.config import StrategyConfig
from orb_bot.models import (
    Candle,
    Direction,
    EntryConfirmed,
    Fill,
    OpeningRange,
    OrderResult,
    State,
    TradeRecorded,
)

from .context import orb_bot  # noqa: F401

ET = ZoneInfo("America/New_York")


def _cfg(**over):
    return StrategyConfig(**over)


def _mk_candle(o, h, lo, c, minute, tf=15, bars=15):
    ts_open = datetime(2026, 6, 19, 9, 30, tzinfo=ET) + timedelta(minutes=minute)
    return Candle(
        ts_open=ts_open, ts_close=ts_open + timedelta(minutes=tf),
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(lo)),
        close=Decimal(str(c)), volume=1000, timeframe_min=tf, bars_present=bars,
    )


def _engine_at_wait_entry(cfg, trades=1):
    e = eng.Engine(cfg, date(2026, 6, 19))
    ctx = e.ctx
    ctx.state = State.WAIT_ENTRY
    ctx.direction = Direction.LONG
    ctx.break_level = Decimal("101")
    ctx.trades_remaining = trades
    ctx.opening_range = OpeningRange(
        high=Decimal("101"), low=Decimal("99"),
        established_at=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        width=Decimal("2"), feed="IEX", bars_present=15, low_confidence=False,
    )
    seed = [_mk_candle(100, 100.5, 99.5, 100, m * 15) for m in range(-cfg.atr_period - 1, 0)]
    ctx.atr.seed(seed)
    return e


def _order_result():
    return OrderResult(order_id="o1", client_order_id="2026-06-19-SPY-1-ENTRY",
                       status="filled", filled_avg_price=Decimal("101.90"),
                       filled_qty=10, legs=[])


def test_on_approval_reject_returns_to_wait_entry():
    e = _engine_at_wait_entry(_cfg(), trades=1)
    events = e.on_approval("REJECT")
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.trades_remaining == 1  # no slot consumed
    assert all(not isinstance(ev, EntryConfirmed) for ev in events)


def test_on_approval_timeout_returns_to_wait_entry():
    e = _engine_at_wait_entry(_cfg(), trades=1)
    events = e.on_approval("TIMEOUT")
    assert e.ctx.state is State.WAIT_ENTRY
    assert e.ctx.trades_remaining == 1
    assert all(not isinstance(ev, EntryConfirmed) for ev in events)


def test_on_approval_approve_emits_entry_confirmed():
    e = _engine_at_wait_entry(_cfg(), trades=1)
    events = e.on_approval("APPROVE")
    assert any(isinstance(ev, EntryConfirmed) for ev in events)
    assert e.ctx.state is State.WAIT_ENTRY  # state unchanged until fill
    assert e.ctx.trades_remaining == 1  # still not consumed until fill


def test_on_entry_filled_enters_trade_and_decrements():
    e = _engine_at_wait_entry(_cfg(max_trades_per_day=1), trades=1)
    events = e.on_entry_filled(_order_result())
    assert e.ctx.state is State.IN_TRADE
    assert e.ctx.trades_remaining == 0
    assert all(not isinstance(ev, TradeRecorded) for ev in events)  # not closed yet


def _exit_fill(price="103.90", reason="TP", pos_after=0):
    return Fill(order_id="o2", client_order_id="2026-06-19-SPY-1-TP",
                leg_role="TP", side="sell", price=Decimal(price), qty=10,
                ts=datetime(2026, 6, 19, 11, 0, tzinfo=ET),
                position_qty=pos_after, exit_reason=reason)


def test_on_trade_closed_records_and_finishes_single_trade():
    e = _engine_at_wait_entry(_cfg(max_trades_per_day=1), trades=1)
    e.on_entry_filled(_order_result())  # IN_TRADE, trades_remaining 0
    events = e.on_trade_closed(_exit_fill())
    rec = [ev for ev in events if isinstance(ev, TradeRecorded)]
    assert len(rec) == 1
    assert rec[0].exit_reason == "TP"
    assert e.ctx.state is State.DONE
    assert e.is_done() is True


def test_on_trade_closed_pnl_long_win():
    e = _engine_at_wait_entry(_cfg(max_trades_per_day=1), trades=1)
    e.on_entry_filled(_order_result())  # entry 101.90 x 10 LONG
    events = e.on_trade_closed(_exit_fill(price="103.90"))
    rec = [ev for ev in events if isinstance(ev, TradeRecorded)][0]
    assert rec.pnl == Decimal("20.00")  # (103.90 - 101.90) * 10


def test_on_trade_closed_pnl_long_loss():
    e = _engine_at_wait_entry(_cfg(max_trades_per_day=1), trades=1)
    e.on_entry_filled(_order_result())  # entry 101.90 x 10 LONG
    events = e.on_trade_closed(_exit_fill(price="100.90", reason="SL"))
    rec = [ev for ev in events if isinstance(ev, TradeRecorded)][0]
    assert rec.pnl == Decimal("-10.00")  # (100.90 - 101.90) * 10


def test_on_trade_closed_rearms_when_multi_trade():
    cfg = _cfg(max_trades_per_day=2, rearm_opposite_only=True)
    e = _engine_at_wait_entry(cfg, trades=2)
    e.ctx.swept_high = True  # latch that must carry over
    e.ctx.retest_wait = 3
    e.ctx.retest_dead = True
    e.on_entry_filled(_order_result())  # trades_remaining -> 1
    events = e.on_trade_closed(_exit_fill())
    assert any(isinstance(ev, TradeRecorded) for ev in events)
    assert e.ctx.state is State.WAIT_CONFIRMATION  # re-armed, within window
    assert e.ctx.trades_remaining == 1  # NOT re-decremented
    assert e.ctx.direction is None and e.ctx.break_level is None  # reset
    assert e.ctx.retest_wait == 0 and e.ctx.retest_dead is False  # reset
    assert e.ctx.swept_high is True  # latch carried over
    assert e.is_done() is False
    # sec.10 #14: the just-traded direction is remembered for opposite-only gating
    assert e._last_traded_direction is Direction.LONG


def test_on_trade_closed_no_rearm_when_no_slot_left():
    # max_trades_per_day>1 but trades_remaining hits 0 after the fill -> DONE.
    cfg = _cfg(max_trades_per_day=2)
    e = _engine_at_wait_entry(cfg, trades=1)  # only one slot in practice
    e.on_entry_filled(_order_result())  # trades_remaining -> 0
    e.on_trade_closed(_exit_fill())
    assert e.ctx.state is State.DONE
    assert e.is_done() is True


def test_on_trade_closed_no_rearm_when_window_expired():
    cfg = _cfg(max_trades_per_day=2, trading_window_min=60)
    e = _engine_at_wait_entry(cfg, trades=2)
    e.on_entry_filled(_order_result())
    # exit fill at 11:00 ET — well past session_open 09:30 + 60min = 10:30
    e.on_trade_closed(_exit_fill())
    assert e.ctx.state is State.DONE


def test_rearm_opposite_only_suppresses_same_direction_reconfirm():
    # sec.10 #14: after a LONG trade closes and the engine re-arms, a fresh
    # strong-close breakout in the SAME (LONG) direction must NOT re-confirm;
    # only the opposite (SHORT) side may take the remaining slot.
    #
    # NOTE (ambiguity resolved): the brief's verbatim candle pair sweeps BOTH OR
    # extremes (same-dir candle sweeps the high, opp-dir candle sweeps the low),
    # which -- per spec sec.7 and PART 1's range-day rule (range_day_sweep_both
    # AND swept_high AND swept_low) -- latches a range day and suppresses the
    # opposite confirmation too. That contradicts the test's expectation. The
    # spec-faithful way to isolate the sec.10 #14 opposite-only rule is to disable
    # range-day detection (range_day_sweep_both=False) so the double sweep cannot
    # interfere; this exercises exactly the rearm/opposite-only branch without
    # violating the spec or PART 1's test_range_day_suppresses_same_bar_breakout.
    cfg = _cfg(max_trades_per_day=2, rearm_opposite_only=True, range_day_sweep_both=False)
    e = _engine_at_wait_entry(cfg, trades=2)
    e.on_entry_filled(_order_result())  # IN_TRADE, trades_remaining -> 1
    e.on_trade_closed(_exit_fill())     # re-arm -> WAIT_CONFIRMATION
    assert e.ctx.state is State.WAIT_CONFIRMATION
    assert e._last_traded_direction is Direction.LONG
    # break_level / opening_range carried over (high=101, low=99); feed a candle
    # that CLOSES strong above the OR high -> would normally confirm LONG.
    same_dir = _mk_candle(101.5, 102.5, 101.4, 102.4, 30)
    evs = e.on_candle(same_dir)
    assert e.ctx.state is State.WAIT_CONFIRMATION  # same-direction re-confirm rejected
    assert e.ctx.direction is None
    assert all(not isinstance(ev, eng.m.DirectionConfirmed) for ev in evs)
    # the OPPOSITE side still confirms: a strong close below the OR low -> SHORT
    opp_dir = _mk_candle(98.5, 98.6, 96.0, 96.2, 45)
    e.on_candle(opp_dir)
    assert e.ctx.direction is Direction.SHORT
    assert e.ctx.state is State.WAIT_ENTRY


def test_rearm_double_sweep_latches_range_day_and_suppresses(monkeypatch):
    # Spec sec.7 / PART 1: after re-arm, if subsequent candles sweep BOTH OR
    # extremes the range-day latch fires (range_day_sweep_both default True) and
    # confirmation is suppressed -- this is the behavior that makes the brief's
    # original opposite-only candle pair contradictory, documented here.
    cfg = _cfg(max_trades_per_day=2, rearm_opposite_only=True)  # range_day_sweep_both True
    e = _engine_at_wait_entry(cfg, trades=2)
    e.on_entry_filled(_order_result())
    e.on_trade_closed(_exit_fill())
    assert e.ctx.state is State.WAIT_CONFIRMATION
    e.on_candle(_mk_candle(101.5, 102.5, 101.4, 102.4, 30))  # sweeps high
    evs = e.on_candle(_mk_candle(98.5, 98.6, 96.0, 96.2, 45))  # sweeps low -> range day
    assert e.ctx.range_day is True
    assert e.ctx.direction is None  # range-day suppresses even the opposite side
    assert any(isinstance(ev, eng.m.RangeDayDetected) for ev in evs)


def test_full_lifecycle_approve_fill_close_single_trade():
    # IDLE -> ... -> WAIT_ENTRY (forced) -> APPROVE -> filled -> closed -> DONE
    e = _engine_at_wait_entry(_cfg(max_trades_per_day=1), trades=1)
    assert any(isinstance(ev, EntryConfirmed) for ev in e.on_approval("APPROVE"))
    assert e.ctx.trades_remaining == 1
    e.on_entry_filled(_order_result())
    assert e.ctx.state is State.IN_TRADE
    assert e.ctx.trades_remaining == 0
    evs = e.on_trade_closed(_exit_fill())
    assert any(isinstance(ev, TradeRecorded) for ev in evs)
    assert e.is_done() is True
