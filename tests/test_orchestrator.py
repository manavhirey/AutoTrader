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
    ApprovalRequest,
    Candle,
    ClockInfo,
    Direction,
    Fill,
    Model,
    OrderResult,
    Setup,
    SetupProposed,
)

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _alpaca_env(monkeypatch):
    # Function-scoped, auto-reverted: inject the Alpaca + Discord secrets via
    # monkeypatch so Settings() can load them (including live mode),
    # WITHOUT mutating global os.environ at import time.
    monkeypatch.setenv("ALPACA_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET", "test-secret")
    monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123456789")
    monkeypatch.setenv("DISCORD_APPROVER_USER_ID", "987654321")


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
        cancel_raises=False,
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
        self.cancel_raises = cancel_raises

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
        if self.cancel_raises:
            raise RuntimeError("cancel_all failed")

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


# ---------------------------------------------------------------------------
# Task 39: sizing, equity_for_sizing, revalidate_setup
# ---------------------------------------------------------------------------


def _setup_long():
    return Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.00"),
        target=Decimal("102.00"),
        rr=2.0,
        reason=["breakout"],
    )


async def test_sizing_floors_whole_shares():
    broker = FakeBroker(equity=Decimal("100000"))
    o = _make_orch(broker)
    # risk_per_trade_pct default 0.5 => 100000*0.5/100 = 500 risk; /1.00 stop dist = 500
    # buying_power large enough that risk is the binding constraint
    qty = o._sizing(Decimal("100000"), _setup_long(), Decimal("100000000"))
    assert qty == 500


async def test_sizing_floor_rounds_down():
    broker = FakeBroker()
    o = _make_orch(broker)
    s = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.30"),  # stop dist 0.70
        target=Decimal("101.40"),
        rr=2.0,
        reason=["x"],
    )
    # 100000*0.5/100=500 ; 500/0.70 = 714.28 -> 714
    # buying_power large enough that risk is the binding constraint
    assert o._sizing(Decimal("100000"), s, Decimal("100000000")) == 714


async def test_sizing_rejects_below_one_share():
    broker = FakeBroker()
    o = _make_orch(broker)
    s = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("0.01"),  # huge stop dist 99.99
        target=Decimal("300.00"),
        rr=2.0,
        reason=["x"],
    )
    # 100*0.5/100=0.5 risk over 99.99 -> 0.005 -> floor 0 -> reject
    assert o._sizing(Decimal("100"), s, Decimal("100000000")) == 0


async def test_sizing_capped_by_buying_power():
    broker = FakeBroker()
    o = _make_orch(broker)
    # risk-sizes to 500 (equity=100000, stop_dist=1.00) but buying_power only
    # affords 100 shares at entry=100 (10000/100=100)
    qty = o._sizing(Decimal("100000"), _setup_long(), Decimal("10000"))
    assert qty == 100


async def test_sizing_short_direction():
    broker = FakeBroker()
    o = _make_orch(broker)
    s = Setup(
        direction=Direction.SHORT,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("101.00"),  # stop dist abs(100-101)=1.00
        target=Decimal("98.00"),
        rr=2.0,
        reason=["breakout"],
    )
    # 100000*0.5/100=500 risk; /1.00 stop dist = 500
    qty = o._sizing(Decimal("100000"), s, Decimal("100000000"))
    assert qty == 500


async def test_sizing_zero_equity_returns_zero(caplog):
    import logging

    broker = FakeBroker()
    o = _make_orch(broker)
    with caplog.at_level(logging.WARNING):
        qty = o._sizing(Decimal("0"), _setup_long(), Decimal("100000000"))
    assert qty == 0


async def test_equity_for_sizing_uses_fixed_when_configured():
    broker = FakeBroker(equity=Decimal("100000"))
    settings = Settings(
        strategy=StrategyConfig(equity_source="fixed", fixed_equity=Decimal("25000")),
        run=RunConfig(symbol="AAPL"),
    )
    o = _make_orch(broker, settings=settings)
    o.start_equity = Decimal("100000")
    assert o._equity_for_sizing() == Decimal("25000")


async def test_equity_for_sizing_uses_live_start_equity():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("80000")
    assert o._equity_for_sizing() == Decimal("80000")


def test_revalidate_setup_passes_when_no_live_tick():
    o = _make_orch(FakeBroker())
    o._last_1m = None
    assert o._revalidate_setup(_setup_long()) is True


def test_revalidate_setup_rejects_long_when_price_below_stop():
    o = _make_orch(FakeBroker())
    # latest 1m close has collapsed below the long's stop -> no longer viable
    o._last_1m = _candle_1m(9, 50, price=Decimal("98.50"))
    assert o._revalidate_setup(_setup_long()) is False


def test_revalidate_setup_passes_long_when_price_in_range():
    o = _make_orch(FakeBroker())
    o._last_1m = _candle_1m(9, 50, price=Decimal("100.20"))
    assert o._revalidate_setup(_setup_long()) is True


def test_equity_for_sizing_returns_zero_when_start_equity_is_none():
    broker = FakeBroker()
    o = _make_orch(broker)
    # start_equity is None (preflight not yet run)
    assert o.start_equity is None
    assert o._equity_for_sizing() == Decimal("0")


def _setup_short():
    return Setup(
        direction=Direction.SHORT,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("101.00"),
        target=Decimal("98.00"),
        rr=2.0,
        reason=["breakout"],
    )


def test_revalidate_setup_rejects_short_when_price_at_or_above_stop():
    o = _make_orch(FakeBroker())
    # price >= stop (101.00) -> short is blown past its invalidation level
    o._last_1m = _candle_1m(9, 50, price=Decimal("101.20"))
    assert o._revalidate_setup(_setup_short()) is False


def test_revalidate_setup_passes_short_when_price_in_range():
    o = _make_orch(FakeBroker())
    # price between target (98) and stop (101) -> valid short setup
    o._last_1m = _candle_1m(9, 50, price=Decimal("99.50"))
    assert o._revalidate_setup(_setup_short()) is True


def test_revalidate_setup_rejects_short_when_price_overshot_target():
    o = _make_orch(FakeBroker())
    # price well below target (98) -> has already overshot downward; tol=0 for FakeEngine
    o._last_1m = _candle_1m(9, 50, price=Decimal("95.00"))
    assert o._revalidate_setup(_setup_short()) is False


def test_revalidate_setup_rejects_long_when_price_overshot_target():
    o = _make_orch(FakeBroker())
    # price well above target (102) -> has already overshot upward; tol=0 for FakeEngine
    o._last_1m = _candle_1m(9, 50, price=Decimal("103.00"))
    assert o._revalidate_setup(_setup_long()) is False


# ---------------------------------------------------------------------------
# Task 40: _react (SetupProposed → approval → bracket) + _build_approval_request
# ---------------------------------------------------------------------------


def _or_set(o):
    # engine context the orchestrator reads for OR levels in the approval request
    class _OR:
        high = Decimal("101.00")
        low = Decimal("99.00")
        bars_present = 15
        low_confidence = False
        feed = "IEX"
    o.engine.opening_range = _OR()


async def test_react_setup_proposed_paper_auto_approve_submits_bracket():
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="APPROVE")
    reporter = FakeReporter()
    o = _make_orch(broker, approver=approver, reporter=reporter)
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_long()))
    assert len(broker.submitted) == 1
    sub_setup, sub_qty = broker.submitted[0]
    assert sub_qty == 500
    assert approver.requests, "approver.request must be called (mode-uniform)"
    assert o.engine.approvals == ["APPROVE"]
    assert reporter.trade_taken_calls == [(_setup_long(), 500, "PAPER")]


async def test_react_live_reject_does_not_submit_no_slot():
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="REJECT")
    live_settings = Settings(
        strategy=StrategyConfig(), run=RunConfig(symbol="AAPL", live=True, feed="SIP")
    )
    o = _make_orch(broker, approver=approver, settings=live_settings)
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_long()))
    assert broker.submitted == []
    assert o.engine.approvals == ["REJECT"]
    assert o.reporter.trade_taken_calls == []


async def test_react_live_timeout_does_not_submit():
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="TIMEOUT")
    live_settings = Settings(
        strategy=StrategyConfig(), run=RunConfig(symbol="AAPL", live=True, feed="SIP")
    )
    o = _make_orch(broker, approver=approver, settings=live_settings)
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_long()))
    assert broker.submitted == []
    assert o.engine.approvals == ["TIMEOUT"]


async def test_react_setup_proposed_qty_below_one_rejects_before_approval():
    broker = FakeBroker(equity=Decimal("100"))
    approver = FakeApprover(decision="APPROVE")
    o = _make_orch(broker, approver=approver)
    o.start_equity = Decimal("100")
    _or_set(o)
    s = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("0.01"),
        target=Decimal("300.00"),
        rr=2.0,
        reason=["x"],
    )
    await o._react(SetupProposed(setup=s))
    assert broker.submitted == []
    assert approver.requests == [], "qty<1 must reject before any approval request"


def _setup_retest():
    return Setup(
        direction=Direction.LONG,
        model=Model.RETEST,
        entry=Decimal("100.00"),
        stop=Decimal("99.00"),
        target=Decimal("102.00"),
        rr=2.0,
        reason=["retest"],
    )


async def test_react_setup_proposed_records_last_model_for_attribution():
    # MED #11: a RETEST setup must set self._last_model = RETEST (not BREAKOUT).
    broker = FakeBroker(equity=Decimal("100000"))
    o = _make_orch(broker, approver=FakeApprover(decision="APPROVE"))
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_retest()))
    assert len(broker.submitted) == 1
    assert o._last_model == Model.RETEST


async def test_react_setup_proposed_stale_price_rejects_before_approval():
    # HIGH #9: latest 1m price below the long's stop -> reject before approver.
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="APPROVE")
    o = _make_orch(broker, approver=approver)
    o.start_equity = Decimal("100000")
    _or_set(o)
    o._last_1m = _candle_1m(9, 50, price=Decimal("98.00"))
    await o._react(SetupProposed(setup=_setup_long()))
    assert broker.submitted == []
    assert approver.requests == [], "stale-price reject must precede any approval request"


async def test_build_approval_request_carries_runtime_data():
    broker = FakeBroker(equity=Decimal("100000"))
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    _or_set(o)
    req = o._build_approval_request(_setup_long(), 500)
    assert isinstance(req, ApprovalRequest)
    assert req.symbol == "AAPL"
    assert req.qty == 500
    assert req.mode == "PAPER"
    assert req.feed == "IEX"
    assert req.or_high == Decimal("101.00")
    assert req.or_low == Decimal("99.00")
    assert req.bars_present == 15
    assert req.approval_ttl_s == StrategyConfig().approval_timeout_s
    assert req.risk_dollars == pytest.approx(500.0)


async def test_react_short_rejected_when_shorting_disabled():
    # Task 40 adaptation: SHORT setup with shorting_enabled=False must be rejected
    # BEFORE the approver is called and BEFORE submit_bracket.
    class NoShortBroker(FakeBroker):
        async def get_account(self):
            from orb_bot.models import AccountSnapshot
            return AccountSnapshot(
                equity=self._equity,
                buying_power=self._equity,
                shorting_enabled=False,
            )

    broker = NoShortBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="APPROVE")
    o = _make_orch(broker, approver=approver)
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_short()))
    assert broker.submitted == [], "SHORT must not be submitted when shorting disabled"
    assert approver.requests == [], "approver must not be called when shorting disabled"


# ---------------------------------------------------------------------------
# Task 40 review fixes: error-handling paths
# ---------------------------------------------------------------------------


async def test_react_submit_bracket_failure_is_failsafe():
    # Fix A: if submit_bracket raises, _react must not raise, reporter.trade_taken
    # must NOT be called, and _last_model must remain None (no false attribution).
    class RaisingSubmitBroker(FakeBroker):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.raise_on_submit = True

        async def submit_bracket(self, setup, qty):
            if self.raise_on_submit:
                raise RuntimeError("exchange down")
            return await super().submit_bracket(setup, qty)

    broker = RaisingSubmitBroker(equity=Decimal("100000"))
    reporter = FakeReporter()
    approver = FakeApprover(decision="APPROVE")
    o = _make_orch(broker, approver=approver, reporter=reporter)
    o.start_equity = Decimal("100000")
    _or_set(o)
    # Must not raise
    await o._react(SetupProposed(setup=_setup_long()))
    assert reporter.trade_taken_calls == [], "trade_taken must NOT be called on submit failure"
    assert o._last_model is None, "_last_model must remain None on submit failure"


async def test_react_get_account_failure_is_graceful():
    # Fix B: if get_account raises, _on_setup_proposed must return gracefully —
    # no submit, no approver call, no crash.
    class RaisingAccountBroker(FakeBroker):
        async def get_account(self):
            raise OSError("network timeout")

    broker = RaisingAccountBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="APPROVE")
    o = _make_orch(broker, approver=approver)
    o.start_equity = Decimal("100000")
    _or_set(o)
    # Must not raise
    await o._react(SetupProposed(setup=_setup_long()))
    assert broker.submitted == [], "submit_bracket must not be called when get_account fails"
    assert approver.requests == [], "approver must not be called when get_account fails"


async def test_react_unexpected_decision_does_not_submit():
    # Fix D gate: an approver returning an unrecognized string (e.g. "MAYBE") must
    # NOT result in a bracket submission — the strict `decision != "APPROVE"` gate holds.
    broker = FakeBroker(equity=Decimal("100000"))
    approver = FakeApprover(decision="MAYBE")
    o = _make_orch(broker, approver=approver)
    o.start_equity = Decimal("100000")
    _or_set(o)
    await o._react(SetupProposed(setup=_setup_long()))
    assert broker.submitted == [], "unexpected decision must not result in bracket submission"


# ---------------------------------------------------------------------------
# Task 41: fill tracking (_on_fill)
# ---------------------------------------------------------------------------


def _fill(leg_role, side, price, qty, position_qty, exit_reason=None, coid="2026-06-19-AAPL-1"):
    return Fill(
        order_id="o-" + leg_role,
        client_order_id=f"{coid}-{leg_role}",
        leg_role=leg_role,
        side=side,
        price=Decimal(str(price)),
        qty=qty,
        ts=datetime.datetime(2026, 6, 19, 10, 0, tzinfo=ET),
        position_qty=position_qty,
        exit_reason=exit_reason,
    )


async def test_entry_fill_routes_to_engine_on_entry_filled():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o._pending_entry_qty = 10
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    assert len(o.engine.entry_fills) == 1
    assert o.entry_fill is not None
    assert o.entry_fill.qty == 10


async def test_partial_entry_fill_not_yet_in_trade():
    # MED #13: a partial entry fill (position_qty < cumulative qty) must NOT flip
    # to IN_TRADE / notify the engine.
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o._pending_entry_qty = 10
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 4, position_qty=4))  # fill 4 of 10
    assert o.engine.entry_fills == []   # not yet full -> engine not told
    assert o.entry_fill is None
    # second partial completes the position
    await o._on_fill(_fill("ENTRY", "buy", 100.50, 6, position_qty=10))
    assert len(o.engine.entry_fills) == 1
    assert o.entry_fill is not None


async def test_partial_exit_fills_share_weighted_exit_price():
    # HIGH #5: two partial TP fills average share-weighted via the pure builder.
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o._pending_entry_qty = 10
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    await o._on_fill(_fill("TP", "sell", 102.00, 6, position_qty=4, exit_reason="TARGET"))
    await o._on_fill(_fill("TP", "sell", 103.00, 4, position_qty=0, exit_reason="TARGET"))
    assert len(o.trades) == 1
    tr = o.trades[0]
    # (102*6 + 103*4)/10 = 102.40 share-weighted exit
    assert tr.exit_price == Decimal("102.40")
    assert tr.qty == 10
    assert tr.pnl == Decimal("24.00")  # (102.40-100)*10


async def test_target_exit_builds_trade_result_and_records():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o._pending_entry_qty = 10
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    await o._on_fill(
        _fill("TP", "sell", 102.00, 10, position_qty=0, exit_reason="TARGET")
    )
    assert len(o.engine.closed_fills) == 1
    assert len(o.trades) == 1
    tr = o.trades[0]
    assert tr.exit_reason == "TARGET"
    assert tr.entry_price == Decimal("100.00")
    assert tr.exit_price == Decimal("102.00")
    assert tr.qty == 10
    assert tr.pnl == Decimal("20.00")  # (102-100)*10 LONG
    assert tr.pnl_pct == Decimal("20.00") / Decimal("100000")


async def test_stop_exit_long_negative_pnl():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o._pending_entry_qty = 10
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    await o._on_fill(
        _fill("SL", "sell", 99.00, 10, position_qty=0, exit_reason="STOP")
    )
    assert o.trades[0].pnl == Decimal("-10.00")
    assert o.trades[0].exit_reason == "STOP"


async def test_flatten_fill_synthesizes_trade_result():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o._pending_entry_qty = 10
    o.flatten_coid = "2026-06-19-AAPL-1-FLATTEN"
    await o._on_fill(_fill("ENTRY", "buy", 100.00, 10, position_qty=10))
    await o._on_fill(
        _fill("FLATTEN", "sell", 100.50, 10, position_qty=0, exit_reason="FLATTEN")
    )
    assert len(o.trades) == 1
    tr = o.trades[0]
    assert tr.exit_reason == "FLATTEN"
    assert tr.pnl == Decimal("5.00")  # (100.50-100)*10
    # FLATTEN is synthesized by orchestrator, NOT via engine.on_trade_closed
    assert o.engine.closed_fills == []


# ---------------------------------------------------------------------------
# Task 41 review fixes: SHORT detection, overshoot, idempotency, adopted close
# ---------------------------------------------------------------------------


async def test_short_entry_and_exit_produces_correct_trade_result():
    """Fix A+: SHORT entry fill has negative position_qty; exit is a buy.
    Winning short: entry 100, exit 98, qty 10 → pnl = (100-98)*10 = +20."""
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o._pending_entry_qty = 10
    # ENTRY fill for a short: side='sell', position_qty=-10 (Alpaca signed)
    await o._on_fill(_fill("ENTRY", "sell", 100, 10, position_qty=-10))
    assert len(o.engine.entry_fills) == 1, "on_entry_filled must fire for short"
    assert o.entry_fill is not None
    # SL fill closes the short (buy to cover): position_qty=0
    await o._on_fill(_fill("SL", "buy", 98, 10, position_qty=0, exit_reason="STOP"))
    assert len(o.trades) == 1
    tr = o.trades[0]
    assert tr.direction is Direction.SHORT
    assert tr.pnl == Decimal("20.00")  # winning short: (100-98)*10


async def test_overshoot_entry_fill_treated_as_full():
    """Fix A: abs(position_qty)=12 >= pending=10 → full fill fires on_entry_filled."""
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o._pending_entry_qty = 10
    await o._on_fill(_fill("ENTRY", "buy", 100, 12, position_qty=12))
    assert len(o.engine.entry_fills) == 1
    assert o.entry_fill is not None


async def test_duplicate_entry_full_fill_fires_engine_only_once():
    """Fix B: idempotency — two identical ENTRY fills with position_qty=10 must
    call engine.on_entry_filled exactly once (slot-decrement is inside the engine)."""
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o._pending_entry_qty = 10
    await o._on_fill(_fill("ENTRY", "buy", 100, 10, position_qty=10))
    await o._on_fill(_fill("ENTRY", "buy", 100, 10, position_qty=10))
    assert len(o.engine.entry_fills) == 1, "duplicate ENTRY fill must not double-fire engine"


async def test_adopted_close_builds_trade_result_without_crash():
    """Fix C: simulate reconciliation — entry_fill and _entry_fills set directly
    (as _preflight does), then an SL exit fires → TradeResult appended, no crash."""
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    # Simulate what _preflight sets after a successful reconciliation
    entry = _fill("ENTRY", "buy", 100, 10, position_qty=10)
    o.entry_fill = entry
    o._entry_fills = [entry]
    # _pending_entry_qty stays 0 (entry already happened pre-restart)
    # Drive an SL exit
    await o._on_fill(_fill("SL", "sell", 99, 10, position_qty=0, exit_reason="STOP"))
    assert len(o.trades) == 1
    tr = o.trades[0]
    assert tr.pnl == Decimal("-10.00")  # long loss: (99-100)*10


# ---------------------------------------------------------------------------
# Task 42: Orchestrator._flatten_eod (cancel_all + flatten + tagged FLATTEN coid)
# ---------------------------------------------------------------------------


async def test_flatten_eod_cancels_then_flattens():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    await o._flatten_eod()
    assert broker.cancel_all_calls == 1
    assert broker.flatten_calls == 1


async def test_flatten_eod_tags_flatten_client_order_id_before_flatten():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    o.trade_seq = 1
    await o._flatten_eod()
    assert o.flatten_coid is not None
    assert o.flatten_coid.endswith("-FLATTEN")
    assert "AAPL" in o.flatten_coid


async def test_flatten_eod_idempotent_second_call_noops_on_already_flat():
    broker = FakeBroker()
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    await o._flatten_eod()
    await o._flatten_eod()  # second call must be safe
    # cancel/flatten are idempotent; we only require no exception + flatten ran at least once
    assert broker.flatten_calls >= 1


async def test_flatten_eod_flatten_runs_even_if_cancel_all_raises():
    # Task 42 (HIGH): flatten must run even if cancel_all fails (network/API error).
    # If cancel_all raises, _flatten_eod must NOT raise and must still call flatten().
    broker = FakeBroker(cancel_raises=True)
    o = _make_orch(broker)
    o.start_equity = Decimal("100000")
    # Must not raise despite cancel_all raising
    await o._flatten_eod()
    assert broker.cancel_all_calls == 1, "cancel_all must be attempted"
    assert broker.flatten_calls == 1, "flatten must run despite cancel_all raising"
