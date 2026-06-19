import threading
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot.config import RunConfig
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.models import AccountSnapshot, ClockInfo

from .fakes.alpaca_sdk import FakeClock, FakeTradingClient, FakeTradingStream

ET = ZoneInfo("America/New_York")


def _broker(client=None, stream=None, live=False):
    return AlpacaBroker(
        RunConfig(symbol="SPY", live=live, feed="IEX", allow_live_iex=True),
        key="k",
        secret="s",
        client=client or FakeTradingClient(),
        stream=stream or FakeTradingStream(),
        session_date=date(2026, 6, 19),
    )


async def test_get_account_maps_to_snapshot_with_decimals():
    fc = FakeTradingClient()
    fc.account.equity = "100000.00"
    fc.account.buying_power = "200000.00"
    fc.account.shorting_enabled = True
    broker = _broker(client=fc)
    snap = await broker.get_account()
    assert isinstance(snap, AccountSnapshot)
    assert snap.equity == Decimal("100000.00")
    assert snap.buying_power == Decimal("200000.00")
    assert snap.shorting_enabled is True


async def test_get_clock_maps_to_clockinfo():
    fc = FakeTradingClient()
    fc.clock = FakeClock(is_open=True, next_close=datetime(2026, 6, 19, 16, 0, tzinfo=ET))
    broker = _broker(client=fc)
    ci = await broker.get_clock()
    assert isinstance(ci, ClockInfo)
    assert ci.is_open is True
    assert ci.next_close == datetime(2026, 6, 19, 16, 0, tzinfo=ET)


async def test_sync_calls_run_off_the_event_loop_thread():
    fc = FakeTradingClient()
    broker = _broker(client=fc)
    main_tid = threading.get_ident()
    await broker.get_account()
    await broker.get_clock()
    # asyncio.to_thread offloads each sync SDK call to a worker thread
    assert fc.thread_ids, "no sync call recorded"
    assert all(tid != main_tid for tid in fc.thread_ids)


def test_paper_flag_set_when_not_live():
    """Constructor uses paper=True when run.live is False."""
    paper_calls: list[dict] = []

    class CaptureTradingClient:
        def __init__(self, key, secret, *, paper):
            paper_calls.append({"paper": paper})

    _ = AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=True),
        key="k",
        secret="s",
        session_date=date(2026, 6, 19),
        stream=FakeTradingStream(),
        _client_factory=CaptureTradingClient,
    )
    assert paper_calls == [{"paper": True}]


def test_paper_flag_false_when_live():
    """Constructor uses paper=False when run.live is True."""
    paper_calls: list[dict] = []

    class CaptureTradingClient:
        def __init__(self, key, secret, *, paper):
            paper_calls.append({"paper": paper})

    _ = AlpacaBroker(
        RunConfig(symbol="SPY", live=True, feed="SIP", allow_live_iex=False),
        key="k",
        secret="s",
        session_date=date(2026, 6, 19),
        stream=FakeTradingStream(),
        _client_factory=CaptureTradingClient,
    )
    assert paper_calls == [{"paper": False}]
