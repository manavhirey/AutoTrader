import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from alpaca.trading.enums import OrderSide, TradeEvent

from orb_bot.config import RunConfig
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.models import Fill

from .fakes.alpaca_sdk import FakeTradingClient, FakeTradingStream

ET = ZoneInfo("America/New_York")


def _broker(stream):
    b = AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=True),
        key="k", secret="s",
        client=FakeTradingClient(), stream=stream,
        session_date=date(2026, 6, 19),
    )
    b._parent_coid = "2026-06-19-SPY-1-ENTRY"   # as if submit_bracket ran
    return b


def _event(event, coid, side, price, filled_qty, position_qty):
    # mirrors alpaca TradeUpdate: .event + nested .order
    order = SimpleNamespace(
        id="ord-1", client_order_id=coid, side=side,
        filled_avg_price=str(price), filled_qty=str(filled_qty),
    )
    return SimpleNamespace(
        event=event, order=order, price=str(price), qty=str(filled_qty),
        position_qty=str(position_qty),
        timestamp=datetime(2026, 6, 19, 10, 0, tzinfo=ET),
    )


async def test_stream_start_registers_handler_and_schedules_run_forever():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    assert stream.handler is not None
    await asyncio.sleep(0)   # let the scheduled task run
    assert stream.run_forever_started is True
    await broker.stop_stream()
    assert stream.stopped is True   # used stop_ws(), not sync stop()


async def test_entry_fill_maps_to_fill_with_entry_role():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("fill", "2026-06-19-SPY-1-ENTRY", "buy", "100.10", 10, 10))
    gen = broker.trade_updates()
    fill = await asyncio.wait_for(gen.__anext__(), timeout=1)
    assert isinstance(fill, Fill)
    assert fill.order_id == "ord-1"
    assert fill.client_order_id == "2026-06-19-SPY-1-ENTRY"
    assert fill.leg_role == "ENTRY"
    assert fill.side == "buy"
    assert fill.price == Decimal("100.10")
    assert fill.qty == 10
    assert fill.position_qty == 10
    assert fill.exit_reason is None
    await broker.stop_stream()


async def test_tp_fill_sets_target_exit_reason():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("fill", "2026-06-19-SPY-1-TP", "sell", "102.00", 10, 0))
    fill = await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=1)
    assert fill.leg_role == "TP"
    assert fill.exit_reason == "TARGET"
    await broker.stop_stream()


async def test_sl_fill_sets_stop_exit_reason():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("fill", "2026-06-19-SPY-1-SL", "sell", "99.00", 10, 0))
    fill = await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=1)
    assert fill.leg_role == "SL"
    assert fill.exit_reason == "STOP"
    await broker.stop_stream()


async def test_flatten_fill_sets_flatten_exit_reason():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("fill", "2026-06-19-SPY-1-FLATTEN", "sell", "100.50", 10, 0))
    fill = await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=1)
    assert fill.leg_role == "FLATTEN"
    assert fill.exit_reason == "FLATTEN"
    await broker.stop_stream()


async def test_partial_fill_is_emitted_with_position_qty():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("partial_fill", "2026-06-19-SPY-1-ENTRY", "buy", "100.10", 4, 4))
    fill = await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=1)
    assert fill.leg_role == "ENTRY"
    assert fill.qty == 4
    assert fill.position_qty == 4
    await broker.stop_stream()


async def test_canceled_and_rejected_do_not_emit_fills():
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    await stream.handler(_event("canceled", "2026-06-19-SPY-1-SL", "sell", "0", 0, 0))
    await stream.handler(_event("rejected", "2026-06-19-SPY-1-ENTRY", "buy", "0", 0, 0))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=0.2)
    await broker.stop_stream()


async def test_real_sdk_enums_normalize_correctly():
    """Regression: alpaca-py enums are str-subclass Enums where str() returns the
    repr ('OrderSide.BUY'), not the value ('buy').  _on_trade_update must take .value."""
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()

    # Build event with real SDK enum types and float-shaped string numeric fields
    order = SimpleNamespace(
        id="ord-1",
        client_order_id="2026-06-19-SPY-1-ENTRY",
        side=OrderSide.BUY,                 # real enum — str() == 'OrderSide.BUY'
        filled_avg_price="100.10",
        filled_qty="10.0",                  # float-shaped string — int() would raise
    )
    data = SimpleNamespace(
        event=TradeEvent.FILL,              # real enum — str() == 'TradeEvent.FILL'
        order=order,
        price="100.10",
        qty="10.0",
        position_qty="10.0",               # float-shaped string
        timestamp=datetime(2026, 6, 19, 10, 0, tzinfo=ET),
    )

    await stream.handler(data)
    fill = await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=1)

    assert fill.side == "buy", f"expected 'buy', got {fill.side!r}"
    assert fill.qty == 10, f"expected 10, got {fill.qty!r}"
    assert fill.position_qty == 10, f"expected 10, got {fill.position_qty!r}"
    assert fill.leg_role == "ENTRY"
    await broker.stop_stream()


async def test_malformed_event_does_not_raise_and_emits_no_fill():
    """Defense-in-depth: a malformed event (order=None) must not propagate out of
    the handler and kill the stream.  The try/except swallows it and returns."""
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()

    # order=None will cause AttributeError inside _on_trade_update
    bad_data = SimpleNamespace(
        event="fill",
        order=None,
        price="100.00",
        qty="10",
        position_qty="10",
        timestamp=datetime(2026, 6, 19, 10, 0, tzinfo=ET),
    )

    # Must not raise
    await stream.handler(bad_data)

    # Must not enqueue any fill
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(broker.trade_updates().__anext__(), timeout=0.2)
    await broker.stop_stream()


async def test_double_start_does_not_orphan_task():
    """start_stream called twice must not spawn a second task."""
    stream = FakeTradingStream()
    broker = _broker(stream)
    await broker.start_stream()
    task_first = broker._stream_task
    await broker.start_stream()   # second call — must be a no-op
    assert broker._stream_task is task_first, "second start_stream call must not replace the task"
    await broker.stop_stream()
