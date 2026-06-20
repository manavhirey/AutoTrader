import os
from datetime import date

import pytest

from orb_bot.config import RunConfig
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.models import AccountSnapshot, ClockInfo

_NO_CREDS = not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET"))
pytestmark = pytest.mark.skipif(_NO_CREDS, reason="ALPACA_KEY/ALPACA_SECRET not set")


def test_real_trading_stream_has_semi_internal_methods():
    # spec §13: pin alpaca-py >=0.43,<0.44; assert _run_forever/stop_ws exist on upgrade
    from alpaca.trading.stream import TradingStream

    stream = TradingStream(os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"], paper=True)
    assert hasattr(stream, "_run_forever"), "alpaca-py changed: TradingStream._run_forever gone"
    assert hasattr(stream, "stop_ws"), "alpaca-py changed: TradingStream.stop_ws gone"


async def test_paper_get_account_and_clock_round_trip():
    broker = AlpacaBroker(
        RunConfig(symbol="SPY", live=False, feed="IEX", allow_live_iex=False),
        key=os.environ["ALPACA_KEY"], secret=os.environ["ALPACA_SECRET"],
        session_date=date.today(),
    )
    snap = await broker.get_account()
    clk = await broker.get_clock()
    assert isinstance(snap, AccountSnapshot)
    assert isinstance(clk, ClockInfo)
    assert snap.equity >= 0
