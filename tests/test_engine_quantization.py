"""Tick-size quantization of engine setup prices + the Engine.current_atr accessor.

Covers the live-blocking bug where an ATR-derived stop/target buffer produced
sub-penny prices that Alpaca rejects on a bracket's TP/SL legs. Quantization
happens in setup-construction (pure core), AWAY from entry, before the rr is
computed so ``validate_setup`` stays consistent."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot import engine as eng
from orb_bot.config import StrategyConfig
from orb_bot.models import Candle, Direction, Model

ET = ZoneInfo("America/New_York")


def _cfg(**over):
    return StrategyConfig(**over)


def _candle(o, h, lo, c, ts_open, tf=15, bars=15):
    return Candle(
        ts_open=ts_open,
        ts_close=ts_open + timedelta(minutes=tf),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(lo)), close=Decimal(str(c)),
        volume=1000, timeframe_min=tf, bars_present=bars,
    )


def _is_tick_aligned(price: Decimal, tick: Decimal) -> bool:
    units = price / tick
    return units == units.to_integral_value()


def test_quantize_price_floors_and_ceils():
    tick = Decimal("0.01")
    assert eng._quantize_price(Decimal("99.873"), tick, "down") == Decimal("99.87")
    assert eng._quantize_price(Decimal("99.873"), tick, "up") == Decimal("99.88")
    # already aligned -> unchanged in either direction
    assert eng._quantize_price(Decimal("100.00"), tick, "down") == Decimal("100.00")
    assert eng._quantize_price(Decimal("100.00"), tick, "up") == Decimal("100.00")
    # directional toward +/-inf (ROUND_FLOOR/CEILING), not toward zero: a negative
    # price floors more-negative and ceils less-negative regardless of sign.
    assert eng._quantize_price(Decimal("-2.697"), Decimal("0.10"), "down") == Decimal("-2.70")
    assert eng._quantize_price(Decimal("-2.697"), Decimal("0.10"), "up") == Decimal("-2.60")


def test_setup_from_quantizes_long_stop_down_target_up():
    cfg = _cfg(tick_size=Decimal("0.01"), risk_reward_ratio=1.5)
    s = eng._setup_from(
        Direction.LONG, Model.BREAKOUT,
        entry=Decimal("100.00"), stop=Decimal("99.873"), cfg=cfg, reason=[],
    )
    assert s.stop == Decimal("99.87")    # floored away from entry (wider risk)
    assert s.target == Decimal("100.20")  # ceiled away from entry (preserves RR)
    assert _is_tick_aligned(s.stop, cfg.tick_size)
    assert _is_tick_aligned(s.target, cfg.tick_size)
    assert s.rr >= cfg.risk_reward_ratio  # RR floor preserved after quantization


def test_setup_from_quantizes_short_stop_up_target_down():
    cfg = _cfg(tick_size=Decimal("0.01"), risk_reward_ratio=1.5)
    s = eng._setup_from(
        Direction.SHORT, Model.BREAKOUT,
        entry=Decimal("100.00"), stop=Decimal("100.127"), cfg=cfg, reason=[],
    )
    assert s.stop == Decimal("100.13")   # ceiled away from entry (wider risk)
    assert s.target == Decimal("99.80")  # floored away from entry (preserves RR)
    assert _is_tick_aligned(s.stop, cfg.tick_size)
    assert _is_tick_aligned(s.target, cfg.tick_size)
    assert s.rr >= cfg.risk_reward_ratio


def test_setup_from_respects_nonpenny_tick_size():
    # a 5-cent tick: prices must land on 0.05 multiples
    cfg = _cfg(tick_size=Decimal("0.05"), risk_reward_ratio=2.0)
    s = eng._setup_from(
        Direction.LONG, Model.BREAKOUT,
        entry=Decimal("100.00"), stop=Decimal("99.91"), cfg=cfg, reason=[],
    )
    assert s.stop == Decimal("99.90")    # 99.91 floored to 0.05 grid
    assert _is_tick_aligned(s.stop, cfg.tick_size)
    assert _is_tick_aligned(s.target, cfg.tick_size)


def test_engine_current_atr_accessor():
    e = eng.Engine(_cfg(atr_period=3), date(2026, 6, 19))
    assert e.current_atr() is None  # not seeded yet -> None, not a raised ValueError
    base = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    bars = [
        _candle(100, 101, 99, 100, base + timedelta(minutes=15 * i)) for i in range(5)
    ]
    e.seed_atr(bars)
    assert e.current_atr() == e.ctx.atr.value
    assert e.current_atr() > Decimal("0")
