from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot.feed import alpaca as feed_alpaca
from orb_bot.models import Candle

from .context import orb_bot  # noqa: F401

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


class FakeBar:
    """Mimics the alpaca-py StockDataStream Bar payload (UTC tz-aware timestamp)."""

    def __init__(self, symbol, timestamp, open_, high, low, close, volume):
        self.symbol = symbol
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def test_bar_to_candle_converts_prices_to_decimal_and_sets_1m_timeframe():
    # 09:30 ET == 13:30 UTC (EDT, summer); pass UTC like the SDK does.
    ts_utc = datetime(2026, 6, 19, 13, 30, tzinfo=UTC)
    bar = FakeBar("SPY", ts_utc, 100.12, 100.45, 99.98, 100.30, 5000)

    candle = feed_alpaca._bar_to_candle(bar)

    assert isinstance(candle, Candle)
    assert candle.timeframe_min == 1
    assert candle.open == Decimal("100.12")
    assert candle.high == Decimal("100.45")
    assert candle.low == Decimal("99.98")
    assert candle.close == Decimal("100.30")
    assert candle.volume == 5000


def test_bar_to_candle_uses_et_and_close_is_open_plus_one_minute():
    ts_utc = datetime(2026, 6, 19, 13, 30, tzinfo=UTC)
    bar = FakeBar("SPY", ts_utc, 1.0, 1.0, 1.0, 1.0, 1)

    candle = feed_alpaca._bar_to_candle(bar)

    expected_open = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    assert candle.ts_open == expected_open
    assert candle.ts_open.tzinfo is not None
    assert candle.ts_close == candle.ts_open + timedelta(minutes=1)
