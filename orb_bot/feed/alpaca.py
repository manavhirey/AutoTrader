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
from typing import Any
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


_SENTINEL = object()  # put on the queue by close() to terminate candles()


def _default_stream_factory(*, api_key: str, secret_key: str, feed: str):
    """Build a real alpaca-py StockDataStream. alpaca-py imported lazily here so
    the SDK stays isolated to this call site (§4/§13)."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.live.stock import StockDataStream

    return StockDataStream(api_key, secret_key, feed=DataFeed[feed])


def _default_hist_factory(*, api_key: str, secret_key: str):
    """Build a real alpaca-py StockHistoricalDataClient (lazy import)."""
    from alpaca.data.historical.stock import StockHistoricalDataClient

    return StockHistoricalDataClient(api_key, secret_key)


def _bars_to_candles(bars: list[Any], tf_min: int) -> list[Candle]:
    """Convert SDK bars to Candles with the given timeframe (oldest→newest).

    For 1m bars delegates to ``_bar_to_candle``; for T-min bars builds a
    Candle directly so ``timeframe_min`` reflects the requested granularity
    and ``ts_close = ts_open + tf_min``.
    """
    out: list[Candle] = []
    for bar in bars:
        if tf_min == 1:
            out.append(_bar_to_candle(bar))
        else:
            ts_open = bar.timestamp.astimezone(ET)
            out.append(
                Candle(
                    ts_open=ts_open,
                    ts_close=ts_open + timedelta(minutes=tf_min),
                    open=Decimal(str(bar.open)),
                    high=Decimal(str(bar.high)),
                    low=Decimal(str(bar.low)),
                    close=Decimal(str(bar.close)),
                    volume=int(bar.volume),
                    timeframe_min=tf_min,
                )
            )
    return out


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
        stream_factory=None,
        hist_factory=None,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._symbol = symbol
        self._feed = feed
        self._queue: asyncio.Queue[Candle | object] = asyncio.Queue(maxsize=queue_maxsize)
        self._stream_factory = stream_factory or _default_stream_factory
        self._hist_factory = hist_factory or _default_hist_factory
        self._stream: Any = None  # built in start()
        self._hist: Any = None  # built lazily by _ensure_hist()
        self._run_task: asyncio.Task[Any] | None = None
        self._closed = False

    async def _on_bar(self, bar) -> None:
        """SDK callback: convert a 1m Bar and enqueue it for candles()."""
        await self._queue.put(_bar_to_candle(bar))

    def candles(self) -> AsyncIterator[Candle]:
        """Drain the queue, yielding 1m transport Candles in FIFO order.

        Terminates cleanly when close() puts the sentinel on the queue.
        """
        async def _drain():
            while True:
                item = await self._queue.get()
                if item is _SENTINEL:
                    return
                yield item
        return _drain()

    def start(self) -> None:
        """Build the stream, subscribe the 1m-bar handler, and schedule the SDK's
        _run_forever() as a task on the running loop (never stream.run(), which
        calls asyncio.run() and blocks -- §13 loop trap)."""
        self._stream = self._stream_factory(
            api_key=self._api_key, secret_key=self._secret_key, feed=self._feed,
        )
        self._stream.subscribe_bars(self._on_bar, self._symbol)
        self._run_task = asyncio.ensure_future(self._stream._run_forever())

    async def close(self) -> None:
        """Idempotent shutdown: stop the ws, then cancel + await the run task.
        Uses stop_ws() (async, same-loop) -- never the sync stream.stop() (§13).
        Puts a sentinel on the queue so any active candles() iterator exits."""
        if self._closed:
            return
        self._closed = True
        await self._queue.put(_SENTINEL)
        if self._stream is not None:
            await self._stream.stop_ws()
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
            self._run_task = None

    # ------------------------------------------------------------------
    # Historical REST (§8 step 1): ATR seed + OR backfill
    # ------------------------------------------------------------------

    def _ensure_hist(self) -> Any:
        if self._hist is None:
            self._hist = self._hist_factory(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
        return self._hist

    async def _get_bars(
        self, *, tf_min: int, start: Any, end: Any, limit: int | None
    ) -> list[Any]:
        """Run the sync historical REST call off the loop (§13 to_thread)."""
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        hist = self._ensure_hist()
        timeframe = (
            TimeFrame.Minute
            if tf_min == 1
            else TimeFrame(tf_min, TimeFrameUnit.Minute)
        )
        request = StockBarsRequest(
            symbol_or_symbols=self._symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            feed=DataFeed[self._feed],
        )
        barset = await asyncio.to_thread(hist.get_stock_bars, request)
        return barset.data.get(self._symbol, [])

    async def hist_tf(self, *, limit: int, tf_min: int, end: Any) -> list[Candle]:
        """Last ``limit`` T-minute Candles up to ``end`` (oldest→newest) for ATR seed.

        Note: IEX free tier withholds the most recent ~15 min of history.
        """
        bars = await self._get_bars(tf_min=tf_min, start=None, end=end, limit=limit)
        return _bars_to_candles(bars, tf_min)

    async def backfill_or(self, *, start: Any, end: Any) -> list[Candle]:
        """1m Candles in [start, end) for opening-range reconstruction (§8 step 1)."""
        bars = await self._get_bars(tf_min=1, start=start, end=end, limit=None)
        return _bars_to_candles(bars, 1)
