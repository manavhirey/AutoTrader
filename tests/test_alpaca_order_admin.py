import threading
from datetime import date
from decimal import Decimal

from orb_bot.config import RunConfig
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.models import OrderResult

from .fakes.alpaca_sdk import FakeOrder, FakeTradingClient, FakeTradingStream


def _broker(fc):
    return AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=True),
        key="k", secret="s",
        client=fc, stream=FakeTradingStream(),
        session_date=date(2026, 6, 19),
    )


async def test_get_order_maps_and_offloads():
    fc = FakeTradingClient()
    fc.next_order = FakeOrder(id="ord-3", client_order_id="c-3", status="filled",
                              filled_avg_price="100.25", filled_qty="10", legs=[])
    broker = _broker(fc)
    main_tid = threading.get_ident()
    res = await broker.get_order("ord-3")
    assert isinstance(res, OrderResult)
    assert res.order_id == "ord-3"
    assert res.filled_avg_price == Decimal("100.25")
    assert res.filled_qty == 10
    assert fc.thread_ids and all(tid != main_tid for tid in fc.thread_ids)
    assert fc.requested_order_id == "ord-3"


async def test_cancel_all_calls_cancel_orders():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.cancel_all()
    assert fc.canceled_all == 1


async def test_flatten_closes_all_with_cancel_orders_true():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.flatten()
    assert fc.closed_all == [True]


async def test_admin_calls_offload_to_worker_threads():
    fc = FakeTradingClient()
    broker = _broker(fc)
    main_tid = threading.get_ident()
    await broker.cancel_all()
    await broker.flatten()
    assert fc.thread_ids and all(tid != main_tid for tid in fc.thread_ids)
