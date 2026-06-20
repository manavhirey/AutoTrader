"""Network-gated paper integration smoke test (spec §18, §13, §19).

Gated on real Alpaca paper credentials (ALPACA_KEY / ALPACA_SECRET) and the
optional Discord token. Without them every test is skipped, so the default
`pytest tests` run never touches the network. Run explicitly with creds in the
environment (or a loaded .env) to exercise the live SDK seams that the pins in
§19 depend on (`subscribe_bars` / `_run_forever` / `stop_ws`).
"""
import asyncio
import importlib
import os
import uuid
from decimal import Decimal

import pytest

ALPACA_KEY = os.environ.get("ALPACA_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

_HAVE_ALPACA_CREDS = bool(ALPACA_KEY and ALPACA_SECRET)

alpaca_creds = pytest.mark.skipif(
    not _HAVE_ALPACA_CREDS,
    reason="ALPACA_KEY/ALPACA_SECRET not set; skipping network smoke test",
)
discord_creds = pytest.mark.skipif(
    not DISCORD_TOKEN,
    reason="DISCORD_TOKEN not set; skipping Discord connect smoke test",
)


def _require(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ImportError:
        pytest.skip(f"{module_name} not installed")


def test_alpaca_py_version_pinned():
    """Spec §19: alpaca-py is pinned >=0.43,<0.44 — guard against silent bumps."""
    alpaca = _require("alpaca")
    ver = getattr(alpaca, "__version__", None)
    if ver is None:
        pytest.skip("alpaca.__version__ unavailable")
    parts = ver.split(".")
    major, minor = int(parts[0]), int(parts[1])
    assert (major, minor) == (0, 43), (
        f"alpaca-py {ver} outside the pinned >=0.43,<0.44 window (spec §19); "
        "re-verify _run_forever / stop_ws before unpinning."
    )


def test_stock_data_stream_exposes_semiinternal_seams():
    """Spec §13: the orchestrator schedules `_run_forever` and shuts down via `stop_ws`.

    These are semi-internal; assert they still exist on the pinned SDK so an
    upgrade that removes them fails loudly here rather than at runtime.
    """
    live = _require("alpaca.data.live")
    StockDataStream = live.StockDataStream
    assert hasattr(StockDataStream, "subscribe_bars")
    assert hasattr(StockDataStream, "_run_forever")
    assert hasattr(StockDataStream, "stop_ws")


def test_trading_stream_exposes_trade_update_seams():
    """Spec §13: fills arrive via TradingStream.subscribe_trade_updates; same loop seams."""
    trading_live = _require("alpaca.trading.stream")
    TradingStream = trading_live.TradingStream
    assert hasattr(TradingStream, "subscribe_trade_updates")
    assert hasattr(TradingStream, "_run_forever")
    assert hasattr(TradingStream, "stop_ws")


@alpaca_creds
def test_paper_account_reachable():
    """Smoke: TradingClient(paper=True) returns an account with equity/buying_power."""
    trading = _require("alpaca.trading.client")
    client = trading.TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
    account = client.get_account()
    assert account is not None
    assert Decimal(str(account.buying_power)) >= 0
    # Clock seam used by preflight gating (spec §8 step 1 / §13).
    clock = client.get_clock()
    assert hasattr(clock, "is_open")
    assert hasattr(clock, "next_close")


@alpaca_creds
def test_paper_bracket_submit_and_cancel():
    """Submit a tiny far-from-market paper bracket, then cancel it (no fill intended)."""
    trading = _require("alpaca.trading.client")
    requests = _require("alpaca.trading.requests")
    enums = _require("alpaca.trading.enums")

    client = trading.TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)

    # A deep limit so it rests unfilled; bracket children bracket that limit.
    entry = Decimal("1.00")
    target = Decimal("2.00")
    stop = Decimal("0.50")
    client_order_id = f"smoke-{uuid.uuid4().hex[:12]}"

    order_req = requests.LimitOrderRequest(
        symbol="AAPL",
        qty=1,
        side=enums.OrderSide.BUY,
        time_in_force=enums.TimeInForce.DAY,
        order_class=enums.OrderClass.BRACKET,
        limit_price=float(entry),
        client_order_id=client_order_id,
        take_profit=requests.TakeProfitRequest(limit_price=float(target)),
        stop_loss=requests.StopLossRequest(stop_price=float(stop)),
    )
    submitted = client.submit_order(order_req)
    try:
        assert submitted.id is not None
        assert submitted.client_order_id == client_order_id
        # A bracket parent reports its OCO/OTO children as `legs`.
        assert submitted.legs is not None and len(submitted.legs) >= 1
    finally:
        # Defense: cancel just this order; fall back to cancel_all on any issue.
        try:
            client.cancel_order_by_id(submitted.id)
        except Exception:
            client.cancel_orders()


@discord_creds
def test_discord_client_connects_and_closes():
    """Spec §14: discord.py client starts as a task and can be cleanly closed."""
    discord = _require("discord")

    async def _connect_then_close():
        intents = discord.Intents.none()
        client = discord.Client(intents=intents)
        ready = asyncio.Event()

        @client.event
        async def on_ready():  # noqa: ANN202
            ready.set()

        task = asyncio.create_task(client.start(DISCORD_TOKEN))
        try:
            await asyncio.wait_for(ready.wait(), timeout=30)
            assert client.user is not None
        finally:
            await client.close()
            try:
                await asyncio.wait_for(task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()

    asyncio.run(_connect_then_close())
