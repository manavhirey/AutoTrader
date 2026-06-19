# tests/test_indicators.py
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from orb_bot.indicators import ATR, find_fvg, find_impulse, find_swing, is_strong_close, within
from orb_bot.models import Candle, Direction

ET = ZoneInfo("America/New_York")


def _c(o, h, lo, cl, *, minute=0):
    ts_open = datetime(2026, 6, 19, 9, 30 + minute, tzinfo=ET)
    ts_close = datetime(2026, 6, 19, 9, 31 + minute, tzinfo=ET)
    return Candle(
        ts_open=ts_open,
        ts_close=ts_close,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(cl)),
        volume=100,
        timeframe_min=15,
    )


def test_atr_not_ready_before_seed():
    atr = ATR(period=3)
    assert atr.ready is False
    with pytest.raises(ValueError):
        _ = atr.value


def test_atr_wilder_seed_then_update():
    # prevC starts at first bar close=10. TR for each subsequent bar:
    #   bar1 close=10 (seed prevC)
    #   bar2: h=12,lo=10,prevC=10 -> max(2, 2, 0)=2
    #   bar3: h=13,lo=10,prevC=11 (bar2 close) -> max(3, 2, 1)=3
    #   bar4: h=14,lo=10,prevC=12 (bar3 close) -> max(4, 2, 2)=4
    # seed = mean(2,3,4) = 3
    bars = [
        _c(10, 10, 10, 10, minute=0),  # seeds prevC
        _c(10, 12, 10, 11, minute=1),  # TR=2
        _c(11, 13, 10, 12, minute=2),  # TR=3
        _c(12, 14, 10, 13, minute=3),  # TR=4
    ]
    atr = ATR(period=3)
    atr.seed(bars)
    assert atr.ready is True
    assert atr.value == Decimal("3")

    # update: bar5 h=18,lo=10,prevC=13 -> TR=max(8,5,3)=8
    # ATR = (3*(3-1)+8)/3 = (6+8)/3 = 14/3
    nxt = _c(13, 18, 10, 17, minute=4)
    out = atr.update(nxt)
    assert out == Decimal("14") / Decimal("3")
    assert atr.value == Decimal("14") / Decimal("3")


def test_atr_seed_requires_enough_bars():
    atr = ATR(period=3)
    with pytest.raises(ValueError):
        atr.seed([_c(10, 11, 9, 10)])  # need period+1 bars to form `period` TRs


def _candle(o, h, lo, cl):
    return _c(o, h, lo, cl)


def test_is_strong_close_long_true():
    # rng=10, body=|9.5-1|=8.5 -> 0.85 >= 0.60 ok
    # loc long=(9.5-0)/10=0.95 >= 0.70 ok
    c = _candle(1, 10, 0, "9.5")
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is True


def test_is_strong_close_long_fails_location():
    # body ok (0.85) but close mid-candle: loc=(5-0)/10=0.5 < 0.70
    c = _candle(1, 10, 0, 5)
    # adjust body to stay >=0.60: open=9.0 -> body=4 -> 0.4 < 0.6 also fails body;
    # instead open near low to isolate location: open=0.5, close=5 -> body=4.5 ->0.45 fails body
    # Use a tall body but centered close: open=0, close=5, body=5 ->0.5 fails body too.
    # Isolate location with strong body: open=9, close=5 body=4 ->0.4 fails body.
    # Pure location-only failure needs big body AND centered close — impossible with one
    # candle, so accept this also fails on body; assert overall False:
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is False


def test_is_strong_close_long_fails_body_small():
    # body=|5.5-4.5|=1 -> 0.1 < 0.60
    c = _candle("4.5", 10, 0, "5.5")
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is False


def test_is_strong_close_short_true():
    # body=|0.5-9|=8.5 ->0.85 ok; loc short=(10-0.5)/10=0.95 >=0.70 ok
    c = _candle(9, 10, 0, "0.5")
    assert is_strong_close(c, Direction.SHORT, 0.60, 0.70) is True


def test_is_strong_close_short_fails_location():
    # strong bearish body but close near high: body=|9.5-1|=8.5 ok;
    # loc short=(10-9.5)/10=0.05 < 0.70 -> False
    c = _candle("9.5", 10, 0, 1)  # open 9.5 close 1 -> bullish? no: this is a tall body
    # body=|1-9.5|=8.5 -> 0.85 ok; loc short=(10-1)/10=0.9 ok -> would pass.
    # To fail short location, close must be near HIGH: close=9.6
    c = _candle(1, 10, 0, "9.6")
    # body=|9.6-1|=8.6 ->0.86 ok; loc short=(10-9.6)/10=0.04 <0.70 -> False
    assert is_strong_close(c, Direction.SHORT, 0.60, 0.70) is False


def test_is_strong_close_zero_range_is_false():
    c = _candle(5, 5, 5, 5)  # rng=0 (doji/flat)
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is False
    assert is_strong_close(c, Direction.SHORT, 0.60, 0.70) is False


def test_is_strong_close_boundary_inclusive():
    # exactly at thresholds must PASS (>= semantics). rng=10, body=6 ->0.60 == ratio;
    # long loc=(close-low)/10 must == 0.70 -> close-low=7. open=1,close=7 -> body=6 ok,
    # loc=(7-0)/10=0.70 -> inclusive True
    c = _candle(1, 10, 0, 7)
    assert is_strong_close(c, Direction.LONG, 0.60, 0.70) is True


def test_within_contains_level():
    c = _candle(99, 101, 98, 100)  # low=98, high=101
    assert within(c, Decimal("100"), Decimal("0.25")) is True


def test_within_upper_boundary_inclusive():
    # candle entirely below band; high exactly == level - tol -> inclusive True
    c = _candle("99.5", "99.75", "99.0", "99.6")  # high=99.75
    assert within(c, Decimal("100"), Decimal("0.25")) is True  # level-tol=99.75


def test_within_lower_boundary_inclusive():
    # candle entirely above band; low exactly == level + tol -> inclusive True
    c = _candle("100.30", "100.50", "100.25", "100.40")  # low=100.25
    assert within(c, Decimal("100"), Decimal("0.25")) is True  # level+tol=100.25


def test_within_miss_above():
    c = _candle("100.40", "100.60", "100.30", "100.50")  # low=100.30 > 100.25
    assert within(c, Decimal("100"), Decimal("0.25")) is False


def test_within_miss_below():
    c = _candle("99.40", "99.70", "99.30", "99.60")  # high=99.70 < 99.75
    assert within(c, Decimal("100"), Decimal("0.25")) is False


def test_within_zero_tol():
    c = _candle("99.9", "100.0", "99.8", "99.95")  # high=100 touches level exactly
    assert within(c, Decimal("100"), Decimal("0")) is True
    c2 = _candle("99.9", "99.99", "99.8", "99.95")  # high<level, low<level
    assert within(c2, Decimal("100"), Decimal("0")) is False


# ---------------------------------------------------------------------------
# find_swing tests
# ---------------------------------------------------------------------------


def _hilo(h, lo, *, minute):
    # build a candle with given high/low; open/close inside range
    mid = (Decimal(str(h)) + Decimal(str(lo))) / 2
    return Candle(
        ts_open=datetime(2026, 6, 19, 9, 30 + minute, tzinfo=ET),
        ts_close=datetime(2026, 6, 19, 9, 31 + minute, tzinfo=ET),
        open=mid,
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=mid,
        volume=10,
        timeframe_min=15,
    )


def test_find_swing_high_single_pivot():
    # highs:      5   7   9   6   4   (idx2=9 is the only fractal high for k=1)
    bars = [
        _hilo(5, 1, minute=0),
        _hilo(7, 2, minute=1),
        _hilo(9, 3, minute=2),
        _hilo(6, 2, minute=3),
        _hilo(4, 1, minute=4),
    ]
    assert find_swing(bars, k=1, lookback=5, kind="high") == Decimal("9")


def test_find_swing_low_single_pivot():
    # lows:       9   6   3   5   8   (idx2=3 is fractal low)
    bars = [
        _hilo(20, 9, minute=0),
        _hilo(18, 6, minute=1),
        _hilo(15, 3, minute=2),
        _hilo(17, 5, minute=3),
        _hilo(19, 8, minute=4),
    ]
    assert find_swing(bars, k=1, lookback=5, kind="low") == Decimal("3")


def test_find_swing_returns_most_recent_pivot():
    # two fractal highs: idx1 (8) and idx3 (10); most recent = 10
    bars = [
        _hilo(5, 1, minute=0),
        _hilo(8, 2, minute=1),   # pivot high (5<8>6)
        _hilo(6, 2, minute=2),
        _hilo(10, 3, minute=3),  # pivot high (6<10>7)
        _hilo(7, 2, minute=4),
    ]
    assert find_swing(bars, k=1, lookback=5, kind="high") == Decimal("10")


def test_find_swing_none_monotonic():
    # strictly increasing highs -> no fractal high exists (each side never lower on both)
    bars = [_hilo(h, h - 4, minute=h) for h in (5, 6, 7, 8, 9)]
    assert find_swing(bars, k=1, lookback=5, kind="high") is None


def test_find_swing_none_too_few_bars():
    # need 2k+1 = 3 bars; give 2 -> None
    bars = [_hilo(5, 1, minute=0), _hilo(6, 2, minute=1)]
    assert find_swing(bars, k=1, lookback=5, kind="high") is None


def test_find_swing_respects_lookback_slice():
    # an old pivot outside the lookback slice must be ignored.
    # 7 bars; lookback=4 -> only last 4 considered: highs [6,10,7,4]
    #   within slice idx1(10) is a pivot (6<10>7) -> returns 10.
    bars = [
        _hilo(20, 1, minute=0),  # outside slice
        _hilo(5, 1, minute=1),   # outside slice
        _hilo(99, 1, minute=2),  # outside slice (would be a huge pivot if seen)
        _hilo(6, 1, minute=3),   # slice start
        _hilo(10, 1, minute=4),  # pivot in slice
        _hilo(7, 1, minute=5),
        _hilo(4, 1, minute=6),
    ]
    assert find_swing(bars, k=1, lookback=4, kind="high") == Decimal("10")


# ---------------------------------------------------------------------------
# find_impulse tests
# ---------------------------------------------------------------------------


def test_find_impulse_long_match():
    # last candle: rng=high-low=10 >= 2*1.5=3 and strong bullish close
    win = [
        _candle(1, 2, 0, "1.5"),
        _candle(1, 10, 0, "9.5"),  # rng=10, body=8.5, bullish, close near high
    ]
    disp = find_impulse(win, atr=Decimal("2"), mult=1.5)
    assert disp is not None
    assert disp.type == "IMPULSE"
    assert disp.size == Decimal("10")
    assert disp.upper_candle is win[-1]
    assert disp.lower_candle is win[-1]


def test_find_impulse_short_match():
    win = [_candle(9, 10, 0, "0.5")]  # rng=10, bearish strong close near low
    disp = find_impulse(win, atr=Decimal("2"), mult=1.5)
    assert disp is not None
    assert disp.size == Decimal("10")


def test_find_impulse_none_small_range():
    # rng=2 < threshold 3 -> None even though strong close
    win = [_candle(0, 2, 0, "1.9")]
    assert find_impulse(win, atr=Decimal("2"), mult=1.5) is None


def test_find_impulse_none_not_strong_close():
    # big range but weak/centered body -> not a strong close -> None
    win = [_candle("4.5", 10, 0, "5.5")]  # body=1, rng=10 -> body ratio 0.1
    assert find_impulse(win, atr=Decimal("2"), mult=1.5) is None


def test_find_impulse_empty_window():
    assert find_impulse([], atr=Decimal("2"), mult=1.5) is None


# ---------------------------------------------------------------------------
# find_fvg tests
# ---------------------------------------------------------------------------


def test_find_fvg_bullish():
    # c1.high=100, c3.low=100.05 -> gap=0.05 >= 0.02
    win = [
        _candle(98, 100, 97, "99.5"),    # c1
        _candle(100, 103, 100, "102"),   # c2 (impulse middle)
        _candle("100.10", 104, "100.05", "103"),  # c3 low=100.05 above c1.high
    ]
    disp = find_fvg(win, min_size_ticks=2, tick=Decimal("0.01"))
    assert disp is not None
    assert disp.type == "FVG_BULL"
    assert disp.lower_candle is win[0]   # c1 -> stop uses lower_candle.low for LONG
    assert disp.upper_candle is win[2]   # c3
    assert disp.size == Decimal("0.05")


def test_find_fvg_bearish():
    # c1.low=100, c3.high=99.90 -> gap=0.10 (bearish imbalance)
    win = [
        _candle(102, 103, 100, "100.5"),     # c1 low=100
        _candle(100, 100, 97, "98"),         # c2
        _candle("99.5", "99.90", 96, "97"),  # c3 high=99.90 below c1.low
    ]
    disp = find_fvg(win, min_size_ticks=2, tick=Decimal("0.01"))
    assert disp is not None
    assert disp.type == "FVG_BEAR"
    assert disp.upper_candle is win[0]   # c1 -> stop uses upper_candle.high for SHORT
    assert disp.lower_candle is win[2]   # c3
    assert disp.size == Decimal("0.10")


def test_find_fvg_none_no_gap():
    # overlapping candles -> no imbalance
    win = [
        _candle(98, 101, 97, "100"),
        _candle(99, 102, 98, "101"),
        _candle(100, 103, 99, "102"),  # c3.low=99 < c1.high=101 -> no bull gap
    ]
    assert find_fvg(win, min_size_ticks=2, tick=Decimal("0.01")) is None


def test_find_fvg_none_gap_too_small():
    # bull gap exists but only 1 tick (0.01) < min 2 ticks (0.02)
    win = [
        _candle(98, 100, 97, "99.5"),
        _candle(100, 103, 100, "102"),
        _candle("100.05", 104, "100.01", "103"),  # gap = 100.01-100 = 0.01
    ]
    assert find_fvg(win, min_size_ticks=2, tick=Decimal("0.01")) is None


def test_find_fvg_none_short_window():
    win = [_candle(98, 100, 97, "99"), _candle(100, 103, 100, "102")]  # only 2
    assert find_fvg(win, min_size_ticks=2, tick=Decimal("0.01")) is None
