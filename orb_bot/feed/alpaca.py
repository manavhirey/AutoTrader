"""AlpacaFeed: 1-minute StockDataStream (IEX/SIP) -> Candle queue (transport).

alpaca-py imports are isolated in this module (the SDK is a heavy, optional dep).
The pure SDK-Bar -> Candle conversion lives at module level so it can be unit
tested with a fake Bar and no network / no alpaca-py installed.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot.models import Candle

ET = ZoneInfo("America/New_York")


def _bar_to_candle(bar) -> Candle:
    """Convert an alpaca-py 1m Bar (UTC tz-aware timestamp, float OHLC) to a Candle.

    Prices -> Decimal(str(...)) (exact, no float artefacts); timestamp -> ET;
    timeframe_min=1 (transport); ts_close = ts_open + 1 minute.
    """
    ts_open = bar.timestamp.astimezone(ET)
    return Candle(
        ts_open=ts_open,
        ts_close=ts_open + timedelta(minutes=1),
        open=Decimal(str(bar.open)),
        high=Decimal(str(bar.high)),
        low=Decimal(str(bar.low)),
        close=Decimal(str(bar.close)),
        volume=int(bar.volume),
        timeframe_min=1,
    )
