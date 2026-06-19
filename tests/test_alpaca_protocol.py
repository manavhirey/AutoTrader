from datetime import date

from orb_bot.config import RunConfig
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.interfaces import Broker

from .fakes.alpaca_sdk import FakeTradingClient, FakeTradingStream


def test_alpaca_broker_satisfies_broker_protocol():
    broker = AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=True),
        key="k", secret="s",
        client=FakeTradingClient(), stream=FakeTradingStream(),
        session_date=date(2026, 6, 19),
    )
    assert isinstance(broker, Broker)
    for name in ("get_account", "get_clock", "submit_bracket",
                 "trade_updates", "get_order", "cancel_all", "flatten"):
        assert callable(getattr(broker, name)), name
