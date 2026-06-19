from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from orb_bot import models
from orb_bot.models import Direction, Model
from orb_bot.reporting import summary

ET = ZoneInfo("America/New_York")


def _ts(h: int, m: int) -> datetime:
    return datetime(2026, 6, 19, h, m, tzinfo=ET)


def _fill(role, side, price, qty, *, exit_reason=None, coid="2026-06-19-AAPL-1"):
    return models.Fill(
        order_id=f"{coid}-{role}",
        client_order_id=f"{coid}-{role}",
        leg_role=role,
        side=side,
        price=Decimal(str(price)),
        qty=qty,
        ts=_ts(10, 0),
        position_qty=qty,
        exit_reason=exit_reason,
    )


def test_pnl_for_long_is_positive_when_exit_above_entry():
    pnl = summary.pnl_for(Direction.LONG, Decimal("100.00"), Decimal("102.00"), 10)
    assert pnl == Decimal("20.00")


def test_pnl_for_short_is_positive_when_exit_below_entry():
    pnl = summary.pnl_for(Direction.SHORT, Decimal("100.00"), Decimal("98.00"), 5)
    assert pnl == Decimal("10.00")


def test_weighted_avg_price_over_partial_fills():
    fills = [
        _fill("TP", "sell", "102.00", 6),
        _fill("TP", "sell", "103.00", 4),
    ]
    # (102*6 + 103*4) / 10 = (612 + 412) / 10 = 102.40
    assert summary.weighted_avg_price(fills) == Decimal("102.40")


def test_weighted_avg_price_empty_raises():
    with pytest.raises(ValueError):
        summary.weighted_avg_price([])


def test_build_trade_result_long_target_share_weighted_and_pct_fraction():
    entry = [_fill("ENTRY", "buy", "100.00", 10)]
    exits = [
        _fill("TP", "sell", "102.00", 6, exit_reason="TARGET"),
        _fill("TP", "sell", "103.00", 4, exit_reason="TARGET"),
    ]
    tr = summary.build_trade_result(
        entry_fills=entry,
        exit_fills=exits,
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        start_equity=Decimal("10000.00"),
        exit_reason="TARGET",
    )
    assert tr.entry_price == Decimal("100.00")
    assert tr.exit_price == Decimal("102.40")        # share-weighted
    assert tr.qty == 10                               # min(entry, exit)
    assert tr.pnl == Decimal("24.00")                 # (102.40-100)*10
    assert tr.pnl_pct == Decimal("24.00") / Decimal("10000.00")   # stored as FRACTION
    assert tr.exit_reason == "TARGET"
    assert tr.direction is Direction.LONG
    assert tr.model is Model.BREAKOUT


def test_build_trade_result_flatten_attribution_and_min_qty():
    entry = [_fill("ENTRY", "buy", "50.00", 10)]
    exits = [_fill("FLATTEN", "sell", "49.00", 8, exit_reason="FLATTEN")]
    tr = summary.build_trade_result(
        entry_fills=entry,
        exit_fills=exits,
        direction=Direction.LONG,
        model=Model.RETEST,
        start_equity=Decimal("10000.00"),
        exit_reason="FLATTEN",
    )
    assert tr.qty == 8                                # min(entry=10, exit=8)
    assert tr.pnl == Decimal("-8.00")                 # (49-50)*8
    assert tr.exit_reason == "FLATTEN"


def _tr(pnl, start_equity=Decimal("10000.00"), reason="TARGET",
        direction=Direction.LONG, model=Model.BREAKOUT, qty=10):
    pnl = Decimal(str(pnl))
    return models.TradeResult(
        direction=direction,
        model=model,
        qty=qty,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("100.00") + pnl / Decimal(qty),
        pnl=pnl,
        pnl_pct=pnl / start_equity,
        exit_reason=reason,
    )


def test_build_session_summary_multitrade_totals_and_counts():
    trades = [
        _tr("20.00", reason="TARGET"),
        _tr("-10.00", reason="STOP"),
        _tr("0.00", reason="FLATTEN"),
    ]
    s = summary.build_session_summary(
        trades=trades,
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10010.00"),
        mode="PAPER",
        symbol="AAPL",
        session_date=date(2026, 6, 19),
        no_trade_reason=None,
    )
    assert s.total_pnl == Decimal("10.00")                          # 20 - 10 + 0
    assert s.total_pnl_pct == Decimal("10.00") / Decimal("10000.00")  # fraction
    assert s.wins == 1 and s.losses == 1 and s.breakevens == 1
    assert s.wins + s.losses + s.breakevens == len(s.trades)
    assert s.mode == "PAPER" and s.symbol == "AAPL"
    assert s.session_date == date(2026, 6, 19)
    assert s.start_equity == Decimal("10000.00")
    assert s.end_equity == Decimal("10010.00")
    assert s.no_trade_reason is None


def test_build_session_summary_no_trade_zero_and_reason():
    s = summary.build_session_summary(
        trades=[],
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10000.00"),
        mode="LIVE",
        symbol="MSFT",
        session_date=date(2026, 6, 19),
        no_trade_reason="window expired",
    )
    assert s.trades == []
    assert s.total_pnl == Decimal("0.00")
    assert s.total_pnl_pct == Decimal("0")
    assert s.wins == 0 and s.losses == 0 and s.breakevens == 0
    assert s.no_trade_reason == "window expired"


def test_build_trade_result_short_profitable():
    # SHORT trade: entry fill (SELL) at 100, exit fill (BUY) at 98 (below entry → profitable)
    entry = [_fill("ENTRY", "sell", "100.00", 10)]
    exits = [_fill("TP", "buy", "98.00", 10, exit_reason="TARGET")]
    tr = summary.build_trade_result(
        entry_fills=entry,
        exit_fills=exits,
        direction=Direction.SHORT,
        model=Model.BREAKOUT,
        start_equity=Decimal("10000.00"),
        exit_reason="TARGET",
    )
    assert tr.direction is Direction.SHORT
    assert tr.entry_price == Decimal("100.00")
    assert tr.exit_price == Decimal("98.00")
    assert tr.qty == 10
    # For SHORT: pnl = (entry - exit) * qty = (100 - 98) * 10 = 20 (profitable)
    assert tr.pnl == Decimal("20.00")
    assert tr.pnl_pct == Decimal("20.00") / Decimal("10000.00")


def test_build_trade_result_short_losing():
    # SHORT trade: entry at 100, exit at 102 (above entry → losing)
    entry = [_fill("ENTRY", "sell", "100.00", 5)]
    exits = [_fill("STOP", "buy", "102.00", 5, exit_reason="STOP")]
    tr = summary.build_trade_result(
        entry_fills=entry,
        exit_fills=exits,
        direction=Direction.SHORT,
        model=Model.BREAKOUT,
        start_equity=Decimal("10000.00"),
        exit_reason="STOP",
    )
    assert tr.pnl == Decimal("-10.00")  # (100 - 102) * 5 = -10


def test_build_trade_result_start_equity_zero_raises():
    entry = [_fill("ENTRY", "buy", "100.00", 10)]
    exits = [_fill("TP", "sell", "102.00", 10, exit_reason="TARGET")]
    with pytest.raises(ValueError, match="start_equity must be positive"):
        summary.build_trade_result(
            entry_fills=entry,
            exit_fills=exits,
            direction=Direction.LONG,
            model=Model.BREAKOUT,
            start_equity=Decimal("0"),
            exit_reason="TARGET",
        )


def test_build_trade_result_start_equity_negative_raises():
    entry = [_fill("ENTRY", "buy", "100.00", 10)]
    exits = [_fill("TP", "sell", "102.00", 10, exit_reason="TARGET")]
    with pytest.raises(ValueError, match="start_equity must be positive"):
        summary.build_trade_result(
            entry_fills=entry,
            exit_fills=exits,
            direction=Direction.LONG,
            model=Model.BREAKOUT,
            start_equity=Decimal("-100.00"),
            exit_reason="TARGET",
        )


def test_build_session_summary_start_equity_zero_raises():
    trades = [_tr("20.00")]
    with pytest.raises(ValueError, match="start_equity must be positive"):
        summary.build_session_summary(
            trades=trades,
            start_equity=Decimal("0"),
            end_equity=Decimal("10020.00"),
            mode="PAPER",
            symbol="AAPL",
            session_date=date(2026, 6, 19),
            no_trade_reason=None,
        )


def test_build_trade_result_empty_entry_fills_raises():
    exits = [_fill("TP", "sell", "102.00", 10, exit_reason="TARGET")]
    with pytest.raises(ValueError, match="requires at least one entry fill"):
        summary.build_trade_result(
            entry_fills=[],
            exit_fills=exits,
            direction=Direction.LONG,
            model=Model.BREAKOUT,
            start_equity=Decimal("10000.00"),
            exit_reason="TARGET",
        )


def test_build_trade_result_empty_exit_fills_raises():
    entry = [_fill("ENTRY", "buy", "100.00", 10)]
    with pytest.raises(ValueError, match="requires at least one exit fill"):
        summary.build_trade_result(
            entry_fills=entry,
            exit_fills=[],
            direction=Direction.LONG,
            model=Model.BREAKOUT,
            start_equity=Decimal("10000.00"),
            exit_reason="TARGET",
        )


def test_weighted_avg_price_single_fill():
    fills = [_fill("ENTRY", "buy", "50.00", 10)]
    assert summary.weighted_avg_price(fills) == Decimal("50.00")
