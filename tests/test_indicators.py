# tests/test_indicators.py
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from orb_bot.indicators import ATR
from orb_bot.models import Candle

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
