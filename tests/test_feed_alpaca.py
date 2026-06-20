import asyncio
import os
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from orb_bot.feed import alpaca as feed_alpaca
from orb_bot.interfaces import DataFeed
from orb_bot.models import Candle

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


def _mk_bar(minute, price):
    return FakeBar(
        "SPY",
        datetime(2026, 6, 19, 13, 30 + minute, tzinfo=UTC),
        price, price, price, price, 100,
    )


async def test_candles_drains_queue_in_fifo_order():
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
    )
    # Simulate the SDK pushing three 1m bars via the registered callback.
    await f._on_bar(_mk_bar(0, 100.0))
    await f._on_bar(_mk_bar(1, 101.0))
    await f._on_bar(_mk_bar(2, 102.0))

    got = []
    agen = f.candles()
    for _ in range(3):
        got.append(await agen.__anext__())

    assert [c.close for c in got] == [Decimal("100.0"), Decimal("101.0"), Decimal("102.0")]
    assert [c.ts_open.hour for c in got] == [9, 9, 9]
    assert [c.ts_open.minute for c in got] == [30, 31, 32]
    assert all(c.timeframe_min == 1 for c in got)


async def test_candles_blocks_until_next_bar_enqueued():
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
    )
    agen = f.candles()
    task = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0)  # let the consumer block on an empty queue
    assert not task.done()

    await f._on_bar(_mk_bar(0, 55.0))
    candle = await asyncio.wait_for(task, timeout=1.0)
    assert candle.close == Decimal("55.0")


# ---------------------------------------------------------------------------
# Task 22: stream lifecycle tests (start / close / sentinel)
# ---------------------------------------------------------------------------

class FakeStream:
    """Stand-in for alpaca-py StockDataStream (records the §13 lifecycle calls)."""

    def __init__(self):
        self.subscribed = None
        self.run_forever_started = False
        self.stop_ws_called = False
        self._run_block = asyncio.Event()  # _run_forever hangs until cancelled

    def subscribe_bars(self, handler, *symbols):
        self.subscribed = (handler, symbols)

    async def _run_forever(self):
        self.run_forever_started = True
        await self._run_block.wait()  # mimic a long-lived ws loop

    async def stop_ws(self):
        self.stop_ws_called = True


async def test_start_subscribes_and_schedules_run_forever():
    fake = FakeStream()
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        stream_factory=lambda **_: fake,
    )
    f.start()
    await asyncio.sleep(0)  # let the scheduled task start

    handler, symbols = fake.subscribed
    assert handler == f._on_bar
    assert symbols == ("SPY",)
    assert fake.run_forever_started is True
    assert f._run_task is not None and not f._run_task.done()

    await f.close()


async def test_close_stops_ws_and_cancels_run_task_idempotently():
    fake = FakeStream()
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        stream_factory=lambda **_: fake,
    )
    f.start()
    await asyncio.sleep(0)

    await f.close()
    assert fake.stop_ws_called is True
    assert f._run_task is None or f._run_task.done()

    # idempotent: a second close is a no-op and does not raise
    await f.close()


async def test_close_before_start_is_safe():
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
    )
    await f.close()  # must not raise


async def test_candles_terminates_after_close():
    """close() puts a sentinel on the queue so async for exits cleanly."""
    fake = FakeStream()
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        stream_factory=lambda **_: fake,
    )
    f.start()
    await asyncio.sleep(0)

    # Push one real bar, then close. Queue will contain: [bar, SENTINEL].
    await f._on_bar(_mk_bar(0, 99.0))
    await f.close()

    # Collect everything from candles() – must not hang.
    collected = []
    async for candle in f.candles():
        collected.append(candle)

    # The sentinel terminates the iterator; the one bar is drained first.
    assert len(collected) == 1
    assert collected[0].close == Decimal("99.0")


# ---------------------------------------------------------------------------
# Task 23: historical REST tests (hist_tf / backfill_or)
# ---------------------------------------------------------------------------


class FakeBarSet:
    def __init__(self, symbol, bars):
        self.data = {symbol: bars}


class FakeHistClient:
    """Stand-in for alpaca-py StockHistoricalDataClient.get_stock_bars."""

    def __init__(self, symbol, bars):
        self._barset = FakeBarSet(symbol, bars)
        self.last_request = None

    def get_stock_bars(self, request):
        self.last_request = request
        return self._barset


async def test_hist_tf_returns_tf_candles_oldest_to_newest():
    # Two 15m bars (UTC timestamps); SDK returns them oldest-first.
    bars = [
        FakeBar("SPY", datetime(2026, 6, 19, 13, 0, tzinfo=UTC), 10, 11, 9, 10.5, 1000),
        FakeBar("SPY", datetime(2026, 6, 19, 13, 15, tzinfo=UTC), 10.5, 12, 10, 11.5, 2000),
    ]
    hist = FakeHistClient("SPY", bars)
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        hist_factory=lambda **_: hist,
    )

    async def fake_get_bars(*, tf_min, start, end, limit):
        return hist.get_stock_bars(object()).data["SPY"]

    f._get_bars = fake_get_bars
    end = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    candles = await f.hist_tf(limit=2, tf_min=15, end=end)

    assert [c.timeframe_min for c in candles] == [15, 15]
    assert [c.close for c in candles] == [Decimal("10.5"), Decimal("11.5")]
    # ts_close = ts_open + tf_min for T-bars
    assert candles[0].ts_close == candles[0].ts_open + timedelta(minutes=15)


async def test_backfill_or_returns_1m_candles_in_window():
    bars = [
        FakeBar("SPY", datetime(2026, 6, 19, 13, 30, tzinfo=UTC), 1, 1, 1, 1, 1),
        FakeBar("SPY", datetime(2026, 6, 19, 13, 31, tzinfo=UTC), 2, 2, 2, 2, 2),
    ]
    hist = FakeHistClient("SPY", bars)
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        hist_factory=lambda **_: hist,
    )

    async def fake_get_bars(*, tf_min, start, end, limit):
        return hist.get_stock_bars(object()).data["SPY"]

    f._get_bars = fake_get_bars
    start = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    end = datetime(2026, 6, 19, 9, 45, tzinfo=ET)
    candles = await f.backfill_or(start=start, end=end)

    assert [c.timeframe_min for c in candles] == [1, 1]
    assert candles[0].ts_open == datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    assert candles[1].ts_open == datetime(2026, 6, 19, 9, 31, tzinfo=ET)
    assert candles[0].ts_close == candles[0].ts_open + timedelta(minutes=1)


async def test_hist_tf_empty_bars_returns_empty_list():
    hist = FakeHistClient("SPY", [])
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        hist_factory=lambda **_: hist,
    )

    async def fake_get_bars(*, tf_min, start, end, limit):
        return []

    f._get_bars = fake_get_bars
    candles = await f.hist_tf(limit=5, tf_min=15, end=datetime(2026, 6, 19, 9, 30, tzinfo=ET))
    assert candles == []


async def test_backfill_or_empty_bars_returns_empty_list():
    hist = FakeHistClient("SPY", [])
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
        hist_factory=lambda **_: hist,
    )

    async def fake_get_bars(*, tf_min, start, end, limit):
        return []

    f._get_bars = fake_get_bars
    candles = await f.backfill_or(
        start=datetime(2026, 6, 19, 9, 30, tzinfo=ET),
        end=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
    )
    assert candles == []


# ---------------------------------------------------------------------------
# Task 24: DataFeed Protocol conformance + network-gated SDK smoke tests
# ---------------------------------------------------------------------------


def test_alpaca_feed_satisfies_datafeed_protocol():
    """AlpacaFeed must be structurally compatible with the DataFeed Protocol.

    DataFeed is @runtime_checkable, so isinstance() performs a structural
    check: AlpacaFeed must expose candles() and close() with matching
    signatures. A plain object without those methods must return False.
    """
    f = feed_alpaca.AlpacaFeed(
        api_key="k", secret_key="s", symbol="SPY", feed="IEX",
    )
    assert isinstance(f, DataFeed)

    # Negative check: a bare object does NOT satisfy the protocol.
    assert not isinstance(object(), DataFeed)


def test_stockdatastream_exposes_pinned_sdk_surface():
    """Version-pin guard: assert the installed alpaca-py still exposes the
    three semi-internal methods the §13 lifecycle workaround depends on.

    Runs unconditionally (no creds required — inspects the class, not a live
    stream). If an SDK upgrade renames these, this test fails immediately.
    """
    from alpaca.data.live.stock import StockDataStream

    assert hasattr(StockDataStream, "subscribe_bars"), (
        "alpaca-py removed StockDataStream.subscribe_bars — update §13 adapter"
    )
    assert hasattr(StockDataStream, "_run_forever"), (
        "alpaca-py removed StockDataStream._run_forever — update §13 adapter"
    )
    assert hasattr(StockDataStream, "stop_ws"), (
        "alpaca-py removed StockDataStream.stop_ws — update §13 adapter"
    )


_NO_CREDS = not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET"))


@pytest.mark.skipif(_NO_CREDS, reason="paper smoke test needs ALPACA_KEY/ALPACA_SECRET")
def test_real_stockdatastream_exposes_pinned_sdk_surface():
    """Network-gated: build a live StockDataStream with real creds and verify
    the §13 lifecycle methods exist on the instantiated object (not just the
    class). Skipped when ALPACA_KEY/ALPACA_SECRET are absent.
    """
    from alpaca.data.enums import DataFeed as SDKDataFeed
    from alpaca.data.live.stock import StockDataStream

    stream = StockDataStream(
        os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"], feed=SDKDataFeed.IEX,
    )
    assert hasattr(stream, "subscribe_bars")
    assert hasattr(stream, "_run_forever")
    assert hasattr(stream, "stop_ws")


@pytest.mark.skipif(_NO_CREDS, reason="paper smoke test needs ALPACA_KEY/ALPACA_SECRET")
async def test_real_hist_tf_returns_candles():
    """Network-gated: call hist_tf against the real IEX endpoint to verify the
    REST path works end-to-end. Skipped when ALPACA_KEY/ALPACA_SECRET are absent.
    """
    f = feed_alpaca.AlpacaFeed(
        api_key=os.environ["ALPACA_KEY"],
        secret_key=os.environ["ALPACA_SECRET"],
        symbol="SPY",
        feed="IEX",
    )
    from datetime import datetime as _dt

    candles = await f.hist_tf(limit=5, tf_min=15, end=_dt.now(ET))
    assert len(candles) >= 1
    assert all(c.timeframe_min == 15 for c in candles)
