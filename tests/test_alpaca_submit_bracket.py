import threading
from datetime import date
from decimal import Decimal

import pytest
from alpaca.trading.enums import OrderClass, OrderSide, OrderStatus, TimeInForce

from orb_bot.config import RunConfig
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.models import Direction, Model, OrderResult, Setup

from .fakes.alpaca_sdk import FakeOrder, FakeTradingClient, FakeTradingStream


def _broker(fc):
    return AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=True),
        key="k",
        secret="s",
        client=fc,
        stream=FakeTradingStream(),
        session_date=date(2026, 6, 19),
    )


def _long_setup():
    return Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.00"),
        target=Decimal("102.00"),
        rr=2.0,
        reason=["x"],
    )


def _short_setup():
    return Setup(
        direction=Direction.SHORT,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("101.00"),
        target=Decimal("98.00"),
        rr=2.0,
        reason=["x"],
    )


async def test_long_bracket_request_fields():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.submit_bracket(_long_setup(), 10)
    req = fc.submitted[-1]
    assert req.symbol == "SPY"
    assert req.qty == 10
    assert req.side == OrderSide.BUY
    assert req.time_in_force == TimeInForce.DAY
    assert req.order_class == OrderClass.BRACKET
    assert Decimal(str(req.take_profit.limit_price)) == Decimal("102.00")
    assert Decimal(str(req.stop_loss.stop_price)) == Decimal("99.00")


async def test_short_bracket_uses_sell_and_inverts_legs():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.submit_bracket(_short_setup(), 5)
    req = fc.submitted[-1]
    assert req.side == OrderSide.SELL
    assert Decimal(str(req.take_profit.limit_price)) == Decimal("98.00")  # TP below
    assert Decimal(str(req.stop_loss.stop_price)) == Decimal("101.00")  # SL above


async def test_qty_below_one_is_rejected_before_submit():
    fc = FakeTradingClient()
    broker = _broker(fc)
    with pytest.raises(ValueError):
        await broker.submit_bracket(_long_setup(), 0)
    assert fc.submitted == []  # never reached the SDK


async def test_deterministic_client_order_id_and_seq():
    fc = FakeTradingClient()
    broker = _broker(fc)
    await broker.submit_bracket(_long_setup(), 1)
    await broker.submit_bracket(_long_setup(), 1)
    assert fc.submitted[0].client_order_id == "2026-06-19-SPY-1-ENTRY"
    assert fc.submitted[1].client_order_id == "2026-06-19-SPY-2-ENTRY"


async def test_current_seq_tracks_latest_submitted_bracket():
    fc = FakeTradingClient()
    broker = _broker(fc)
    assert broker.current_seq == 0
    await broker.submit_bracket(_long_setup(), 1)
    assert broker.current_seq == 1
    await broker.submit_bracket(_long_setup(), 1)
    assert broker.current_seq == 2


async def test_submit_runs_off_event_loop_thread():
    fc = FakeTradingClient()
    broker = _broker(fc)
    main_tid = threading.get_ident()
    await broker.submit_bracket(_long_setup(), 1)
    assert fc.thread_ids and all(tid != main_tid for tid in fc.thread_ids)


async def test_order_result_mapping():
    fc = FakeTradingClient()
    fc.next_order = FakeOrder(
        id="ord-9",
        client_order_id="2026-06-19-SPY-1-ENTRY",
        status="accepted",
        filled_avg_price=None,
        filled_qty="0",
        legs=[],
    )
    broker = _broker(fc)
    res = await broker.submit_bracket(_long_setup(), 1)
    assert isinstance(res, OrderResult)
    assert res.order_id == "ord-9"
    assert res.client_order_id == "2026-06-19-SPY-1-ENTRY"
    assert res.status == "accepted"
    assert res.filled_avg_price is None
    assert res.filled_qty == 0
    assert res.legs == []


async def test_order_result_maps_real_sdk_status_enum_and_float_shaped_qty():
    """A real alpaca-py Order returns an OrderStatus enum (str(enum) -> 'OrderStatus.ACCEPTED')
    and may report filled_qty as a float-shaped string (typed Union[str, float, None]).
    The plain-string fakes mask both; pin the real-SDK boundary explicitly."""
    fc = FakeTradingClient()
    fc.next_order = FakeOrder(
        id="ord-1",
        client_order_id="2026-06-19-SPY-1-ENTRY",
        status=OrderStatus.ACCEPTED,  # enum, not a plain string
        filled_avg_price="100.05",
        filled_qty="10.0",  # API may return a float-shaped string
        legs=[],
    )
    broker = _broker(fc)
    res = await broker.submit_bracket(_long_setup(), 10)
    assert res.status == "accepted"  # not "OrderStatus.ACCEPTED"
    assert res.filled_qty == 10  # coerced, not crashed
    assert res.filled_avg_price == Decimal("100.05")
