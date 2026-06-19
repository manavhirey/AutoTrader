"""AlpacaFeed: 1-minute StockDataStream (IEX/SIP) -> Candle queue (transport).

alpaca-py imports are isolated in this module (the SDK is a heavy, optional dep).
The pure SDK-Bar -> Candle conversion lives at module level so it can be unit
tested with a fake Bar and no network / no alpaca-py installed.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
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


class AlpacaFeed:
    """DataFeed: subscribes to 1m StockDataStream bars and exposes them as an
    async iterator of transport Candles (timeframe_min=1).

    The alpaca-py StockDataStream delivers bars via a registered async callback
    (subscribe_bars(handler, *symbols)); _on_bar is that handler. It enqueues a
    converted Candle onto an asyncio.Queue that candles() drains (the §6
    callback->async-iterator adapter). The SDK client is built lazily so the
    queue path is testable without alpaca-py installed.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        symbol: str,
        feed: str = "IEX",
        queue_maxsize: int = 10000,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._symbol = symbol
        self._feed = feed
        self._queue: asyncio.Queue[Candle] = asyncio.Queue(maxsize=queue_maxsize)
        self._stream = None  # lazily built StockDataStream
        self._run_task: asyncio.Task | None = None
        self._closed = False

    async def _on_bar(self, bar) -> None:
        """SDK callback: convert a 1m Bar and enqueue it for candles()."""
        await self._queue.put(_bar_to_candle(bar))

    def candles(self) -> AsyncIterator[Candle]:
        """Drain the queue, yielding 1m transport Candles in FIFO order."""
        async def _drain():
            while True:
                candle = await self._queue.get()
                yield candle
        return _drain()
