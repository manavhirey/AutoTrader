import dataclasses
import typing
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from .context import orb_bot
from orb_bot import models

ET = ZoneInfo("America/New_York")


def _setup() -> models.Setup:
    return models.Setup(
        direction=models.Direction.LONG,
        model=models.Model.BREAKOUT,
        entry=Decimal("101.50"),
        stop=Decimal("100.00"),
        target=Decimal("104.50"),
        rr=2.0,
        reason=["x"],
    )


def test_account_snapshot_frozen():
    a = models.AccountSnapshot(
        equity=Decimal("100000.00"),
        buying_power=Decimal("400000.00"),
        shorting_enabled=True,
    )
    assert isinstance(a.equity, Decimal)
    assert a.shorting_enabled is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.equity = Decimal("0")  # type: ignore[misc]


def test_clock_info_frozen():
    nc = datetime(2026, 6, 19, 16, 0, tzinfo=ET)
    ck = models.ClockInfo(is_open=True, next_close=nc)
    assert ck.is_open is True
    assert ck.next_close == nc
    with pytest.raises(dataclasses.FrozenInstanceError):
        ck.is_open = False  # type: ignore[misc]


def test_order_result_is_mutable():
    o = models.OrderResult(
        order_id="o-1",
        client_order_id="2026-06-19-AAPL-0-ENTRY",
        status="accepted",
        filled_avg_price=None,
        filled_qty=0,
        legs=[],
    )
    o.status = "filled"
    o.filled_avg_price = Decimal("101.50")
    o.filled_qty = 100
    o.legs.append("tp-leg")
    assert o.status == "filled"
    assert o.filled_avg_price == Decimal("101.50")
    assert o.filled_qty == 100
    assert o.legs == ["tp-leg"]


def test_fill_is_mutable_and_fields():
    f = models.Fill(
        order_id="o-1",
        client_order_id="2026-06-19-AAPL-0-ENTRY",
        leg_role="ENTRY",
        side="buy",
        price=Decimal("101.50"),
        qty=100,
        ts=datetime(2026, 6, 19, 9, 46, tzinfo=ET),
        position_qty=100,
        exit_reason=None,
    )
    assert f.leg_role == "ENTRY"
    assert isinstance(f.price, Decimal)
    assert f.exit_reason is None
    f.exit_reason = "TARGET"  # mutable
    assert f.exit_reason == "TARGET"


def test_approval_request_is_mutable_and_fields():
    req = models.ApprovalRequest(
        setup=_setup(),
        symbol="AAPL",
        qty=100,
        risk_dollars=500.0,
        mode="LIVE",
        feed="SIP",
        or_high=Decimal("101.50"),
        or_low=Decimal("99.75"),
        bars_present=15,
        data_warning=None,
        approval_ttl_s=90,
    )
    assert req.setup.direction is models.Direction.LONG
    assert req.mode == "LIVE"
    assert req.approval_ttl_s == 90
    req.data_warning = "low confidence range"  # mutable
    assert req.data_warning == "low confidence range"


def test_trade_result_frozen_and_fields():
    tr = models.TradeResult(
        direction=models.Direction.LONG,
        model=models.Model.BREAKOUT,
        qty=100,
        entry_price=Decimal("101.50"),
        exit_price=Decimal("104.50"),
        pnl=Decimal("300.00"),
        pnl_pct=Decimal("0.003"),
        exit_reason="TARGET",
    )
    assert tr.qty == 100
    assert isinstance(tr.pnl, Decimal)
    assert tr.exit_reason == "TARGET"
    with pytest.raises(dataclasses.FrozenInstanceError):
        tr.pnl = Decimal("0")  # type: ignore[misc]


def test_session_summary_frozen_and_fields():
    tr = models.TradeResult(
        direction=models.Direction.LONG,
        model=models.Model.BREAKOUT,
        qty=100,
        entry_price=Decimal("101.50"),
        exit_price=Decimal("104.50"),
        pnl=Decimal("300.00"),
        pnl_pct=Decimal("0.003"),
        exit_reason="TARGET",
    )
    ss = models.SessionSummary(
        session_date=date(2026, 6, 19),
        symbol="AAPL",
        mode="PAPER",
        trades=[tr],
        total_pnl=Decimal("300.00"),
        total_pnl_pct=Decimal("0.003"),
        wins=1,
        losses=0,
        breakevens=0,
        start_equity=Decimal("100000.00"),
        end_equity=Decimal("100300.00"),
        no_trade_reason=None,
    )
    assert ss.session_date == date(2026, 6, 19)
    assert ss.trades == [tr]
    assert ss.wins + ss.losses + ss.breakevens == len(ss.trades)
    assert ss.no_trade_reason is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        ss.total_pnl = Decimal("0")  # type: ignore[misc]


def test_engine_events_construct_and_frozen():
    orng = models.OpeningRange(
        high=Decimal("101.50"),
        low=Decimal("99.75"),
        established_at=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        width=Decimal("1.75"),
        feed="IEX",
        bars_present=15,
        low_confidence=False,
    )
    re = models.RangeEstablished(opening_range=orng)
    assert re.opening_range is orng

    dc = models.DirectionConfirmed(
        direction=models.Direction.LONG, break_level=Decimal("101.50")
    )
    assert dc.direction is models.Direction.LONG
    assert dc.break_level == Decimal("101.50")

    sp = models.SetupProposed(setup=_setup())
    assert sp.setup.model is models.Model.BREAKOUT

    tr = models.TradeRecorded(pnl=Decimal("300.00"), exit_reason="TARGET")
    assert tr.pnl == Decimal("300.00")
    assert tr.exit_reason == "TARGET"

    # zero-payload events construct cleanly
    assert models.RangeDayDetected() is not None
    assert models.EntryConfirmed() is not None
    assert models.WindowExpired() is not None
    assert models.NoOp() is not None

    # frozen check on a representative payload event
    with pytest.raises(dataclasses.FrozenInstanceError):
        re.opening_range = orng  # type: ignore[misc]


def test_engine_event_union_covers_all_members():
    members = set(typing.get_args(models.EngineEvent))
    assert members == {
        models.RangeEstablished,
        models.DirectionConfirmed,
        models.RangeDayDetected,
        models.SetupProposed,
        models.EntryConfirmed,
        models.TradeRecorded,
        models.WindowExpired,
        models.NoOp,
    }
