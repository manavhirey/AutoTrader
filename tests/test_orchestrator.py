# tests/test_orchestrator.py
import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from orb_bot import orchestrator as orch_mod
from orb_bot.config import RunConfig, Settings, StrategyConfig
from orb_bot.models import (
    AccountSnapshot,
    Candle,
    ClockInfo,
    OrderResult,
)

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _alpaca_env(monkeypatch):
    # Function-scoped, auto-reverted: inject the Alpaca secrets via monkeypatch so
    # Settings() can load them, WITHOUT mutating global os.environ at import time.
    monkeypatch.setenv("ALPACA_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET", "test-secret")


def _candle_1m(h, m, price=Decimal("100"), tf=1):
    ts_open = datetime.datetime(2026, 6, 19, h, m, tzinfo=ET)
    return Candle(
        ts_open=ts_open,
        ts_close=ts_open + datetime.timedelta(minutes=tf),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=10,
        timeframe_min=tf,
    )


class FakeFeed:
    def __init__(self, candles, hist=None):
        self._candles = candles
        self._hist = hist or []
        self.hist_calls = []
        self.closed = False

    async def candles(self):
        for c in self._candles:
            yield c

    async def hist_tf(self, *, limit, tf_min, end):
        self.hist_calls.append((limit, tf_min, end))
        return list(self._hist)

    async def close(self):
        self.closed = True


class FakeBroker:
    def __init__(
        self,
        *,
        is_open=True,
        next_close=None,
        equity=Decimal("100000"),
        open_orders=None,
        positions_qty=0,
    ):
        self._clock = ClockInfo(
            is_open=is_open,
            next_close=next_close
            or datetime.datetime(2026, 6, 19, 16, 0, tzinfo=ET),
        )
        self._equity = equity
        self._open_orders = open_orders or []
        self._positions_qty = positions_qty
        self._seq = 1
        self.submitted = []
        self.cancel_all_calls = 0
        self.flatten_calls = 0
        self._fills = []

    async def get_account(self):
        return AccountSnapshot(
            equity=self._equity,
            buying_power=self._equity,
            shorting_enabled=True,
        )

    async def get_clock(self):
        return self._clock

    @property
    def current_seq(self):
        return self._seq

    async def submit_bracket(self, setup, qty):
        res = OrderResult(
            order_id="o1",
            client_order_id="2026-06-19-AAPL-1-ENTRY",
            status="accepted",
            filled_avg_price=None,
            filled_qty=0,
            legs=[],
        )
        self.submitted.append((setup, qty))
        return res

    async def trade_updates(self):
        for f in self._fills:
            yield f

    async def get_order(self, order_id):
        return OrderResult(
            order_id=order_id,
            client_order_id="2026-06-19-AAPL-1-ENTRY",
            status="filled",
            filled_avg_price=Decimal("100"),
            filled_qty=10,
            legs=[],
        )

    async def cancel_all(self):
        self.cancel_all_calls += 1

    async def flatten(self):
        self.flatten_calls += 1


class FakeApprover:
    def __init__(self, decision="APPROVE"):
        self.decision = decision
        self.requests = []
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def request(self, req):
        self.requests.append(req)
        return self.decision

    async def close(self):
        self.closed = True


class FakeReporter:
    def __init__(self):
        self.started = False
        self.closed = False
        self.trade_taken_calls = []
        self.session_reports = []

    async def start(self):
        self.started = True

    async def trade_taken(self, setup, qty, mode):
        self.trade_taken_calls.append((setup, qty, mode))

    async def session_report(self, summary):
        self.session_reports.append(summary)

    async def close(self):
        self.closed = True


class FixedClock:
    def __init__(self, now):
        self._now = now

    def now(self):
        return self._now


class FakeEngine:
    """Records calls; emits scripted events per candle."""

    def __init__(self, events_by_index=None, cfg=None, session_date=None):
        self.cfg = cfg
        self.session_date = session_date
        self.seeded = None
        self.candles = []
        self._events = events_by_index or {}
        self._done = False
        self.approvals = []
        self.entry_fills = []
        self.closed_fills = []

    def seed_atr(self, hist_tf):
        self.seeded = hist_tf

    def adopt_open_position(self, qty, entry_fill):
        self.adopted = (qty, entry_fill)

    def on_candle(self, c):
        idx = len(self.candles)
        self.candles.append(c)
        return list(self._events.get(idx, []))

    def on_approval(self, decision):
        self.approvals.append(decision)
        return []

    def on_entry_filled(self, res):
        self.entry_fills.append(res)
        return []

    def on_trade_closed(self, fill):
        self.closed_fills.append(fill)
        return []

    def is_done(self):
        return self._done


def _settings(**run_over):
    # ALPACA_KEY/ALPACA_SECRET are provided by the autouse _alpaca_env fixture
    # (function-scoped monkeypatch, auto-reverted) -- no global os.environ mutation.
    run = RunConfig(symbol="AAPL", **run_over)
    return Settings(strategy=StrategyConfig(), run=run)


def _make_orch(  # noqa: PLR0913
    broker, feed=None, approver=None, reporter=None, clock=None, engine=None, settings=None
):
    settings = settings or _settings()
    feed = feed or FakeFeed([])
    approver = approver or FakeApprover()
    reporter = reporter or FakeReporter()
    clock = clock or FixedClock(datetime.datetime(2026, 6, 19, 9, 25, tzinfo=ET))
    engine = engine or FakeEngine()
    return orch_mod.Orchestrator(
        settings, feed, broker, approver, reporter, clock, engine
    )


@freeze_time("2026-06-19 13:25:00")  # 09:25 ET
async def test_preflight_market_closed_returns_false():
    broker = FakeBroker(is_open=False)
    o = _make_orch(broker, clock=FixedClock(datetime.datetime(2026, 6, 19, 9, 25, tzinfo=ET)))
    ok = await o._preflight()
    assert ok is False


async def test_preflight_captures_start_equity_and_flatten_at():
    broker = FakeBroker(
        is_open=True,
        equity=Decimal("50000"),
        next_close=datetime.datetime(2026, 6, 19, 16, 0, tzinfo=ET),
    )
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    o = _make_orch(broker, clock=clk)
    ok = await o._preflight()
    assert ok is True
    assert o.start_equity == Decimal("50000")
    assert o.next_close == datetime.datetime(2026, 6, 19, 16, 0, tzinfo=ET)
    # flatten_at = min(config 15:55, next_close - flatten_buffer_min=5 => 15:55) == 15:55
    assert o.flatten_at == datetime.datetime(2026, 6, 19, 15, 55, tzinfo=ET)
    assert o.mode == "PAPER"


async def test_preflight_flatten_at_clamped_to_next_close_minus_buffer():
    broker = FakeBroker(
        is_open=True,
        next_close=datetime.datetime(2026, 6, 19, 13, 0, tzinfo=ET),  # half day
    )
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    o = _make_orch(broker, clock=clk)
    ok = await o._preflight()
    assert ok is True
    # min(15:55, 13:00-5) == 12:55
    assert o.flatten_at == datetime.datetime(2026, 6, 19, 12, 55, tzinfo=ET)


async def test_preflight_restart_into_open_position_reconciles_in_trade():
    broker = FakeBroker(is_open=True, positions_qty=10)
    clk = FixedClock(datetime.datetime(2026, 6, 19, 11, 30, tzinfo=ET))
    engine = FakeEngine()
    o = _make_orch(broker, clock=clk, engine=engine)
    ok = await o._preflight()
    assert ok is True
    assert o.reconciled_open_position is True
    assert getattr(engine, "adopted", None) is not None
    assert engine.adopted[0] == 10
    assert o.entry_fill is not None and o.entry_fill.qty == 10
    assert engine.seeded is None


async def test_preflight_reconcile_adopts_even_when_get_order_raises():
    # A non-zero position with the ENTRY-order lookup RAISING must still adopt the
    # position (engine.adopt_open_position CALLED) and mark reconciled, with NO
    # fabricated entry_fill ($0 entry would corrupt P/L). Adoption never depends
    # on the order lookup succeeding.
    class RaisingBroker(FakeBroker):
        async def get_order(self, order_id):
            raise RuntimeError("order lookup unavailable")

    broker = RaisingBroker(is_open=True, positions_qty=10)
    clk = FixedClock(datetime.datetime(2026, 6, 19, 11, 30, tzinfo=ET))
    engine = FakeEngine()
    o = _make_orch(broker, clock=clk, engine=engine)
    ok = await o._preflight()
    assert ok is True
    assert o.reconciled_open_position is True
    assert getattr(engine, "adopted", None) is not None
    assert engine.adopted[0] == 10
    assert engine.adopted[1] is None  # adopted with entry_fill=None
    assert o.entry_fill is None  # no $0 fabrication
    assert engine.seeded is None


async def test_preflight_flatten_at_is_tz_aware_in_configured_tz():
    broker = FakeBroker(
        is_open=True,
        next_close=datetime.datetime(2026, 6, 19, 16, 0, tzinfo=ET),
    )
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    o = _make_orch(broker, clock=clk)
    ok = await o._preflight()
    assert ok is True
    assert o.flatten_at is not None
    # tz-aware and normalized to the bot's configured tz (StrategyConfig.timezone).
    assert o.flatten_at.tzinfo is not None
    assert o.flatten_at.utcoffset() == datetime.datetime.now(o.tz).utcoffset()


async def test_preflight_seeds_atr_with_nonempty_hist():
    broker = FakeBroker(is_open=True)
    clk = FixedClock(datetime.datetime(2026, 6, 19, 9, 20, tzinfo=ET))
    hist = [_candle_1m(9, 30, tf=15), _candle_1m(9, 45, tf=15)]
    engine = FakeEngine()
    o = _make_orch(broker, feed=FakeFeed([], hist=hist), clock=clk, engine=engine)
    ok = await o._preflight()
    assert ok is True
    assert engine.seeded == hist
    assert o.feed.hist_calls and o.feed.hist_calls[0][1] == 15  # tf_min
