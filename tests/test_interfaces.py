from collections.abc import AsyncIterator
from typing import get_type_hints

import pytest

from orb_bot import interfaces
from orb_bot.interfaces import Approver, Broker, Clock, DataFeed, Reporter

ALL_PROTOCOLS = [DataFeed, Broker, Approver, Reporter, Clock]


def _is_protocol(cls) -> bool:
    # typing marks Protocol classes with the private _is_protocol flag.
    return getattr(cls, "_is_protocol", False) is True


@pytest.mark.parametrize("proto", ALL_PROTOCOLS)
def test_is_protocol(proto):
    assert _is_protocol(proto), f"{proto.__name__} must be a typing.Protocol"


@pytest.mark.parametrize("proto", ALL_PROTOCOLS)
def test_is_runtime_checkable(proto):
    # runtime_checkable sets _is_runtime_protocol; isinstance against the bare
    # protocol must not raise TypeError (which it would for a non-runtime Protocol).
    assert getattr(proto, "_is_runtime_protocol", False) is True
    try:
        isinstance(object(), proto)
    except TypeError:
        pytest.fail(f"{proto.__name__} is not runtime_checkable")


def test_datafeed_stub_satisfies_isinstance():
    class StubFeed:
        def start(self) -> None:
            ...

        def candles(self) -> AsyncIterator[object]:
            ...

        async def close(self) -> None:
            ...

    assert isinstance(StubFeed(), DataFeed)


def test_datafeed_missing_method_fails_isinstance():
    class Partial:
        def candles(self):
            ...

    # Missing close() -> structural check fails.
    assert not isinstance(Partial(), DataFeed)


def test_broker_stub_satisfies_isinstance():
    class StubBroker:
        async def start_stream(self) -> None:
            ...

        async def stop_stream(self) -> None:
            ...

        async def get_account(self):
            ...

        async def get_clock(self):
            ...

        async def submit_bracket(self, setup, qty):
            ...

        def trade_updates(self):
            ...

        async def get_order(self, order_id):
            ...

        async def cancel_all(self) -> None:
            ...

        async def flatten(self) -> None:
            ...

        async def list_position_symbols(self) -> list[str]:
            ...

    assert isinstance(StubBroker(), Broker)


def test_approver_stub_satisfies_isinstance():
    class StubApprover:
        async def start(self) -> None:
            ...

        async def request(self, req) -> str:
            return "TIMEOUT"

        async def close(self) -> None:
            ...

    assert isinstance(StubApprover(), Approver)


def test_reporter_stub_satisfies_isinstance():
    class StubReporter:
        async def start(self) -> None:
            ...

        async def trade_taken(self, setup, qty, mode) -> None:
            ...

        async def session_report(self, summary) -> None:
            ...

        async def close(self) -> None:
            ...

    assert isinstance(StubReporter(), Reporter)


def test_clock_stub_satisfies_isinstance():
    class StubClock:
        def now(self):
            ...

    assert isinstance(StubClock(), Clock)


def test_all_protocols_exported():
    for name in ("DataFeed", "Broker", "Approver", "Reporter", "Clock"):
        assert hasattr(interfaces, name)


def test_protocol_annotations_resolve():
    # get_type_hints forces evaluation of the model-typed annotations,
    # proving interfaces.py imports the canonical models correctly.
    hints = get_type_hints(Broker.get_account)
    assert "return" in hints
