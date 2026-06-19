from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from orb_bot import models
from orb_bot.models import Direction, Model
from orb_bot.reporting import summary
from orb_bot.reporting.log import LogReporter

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


class _RecordingLogger:
    """Minimal structlog-style stub: records (event, kwargs) per level."""

    def __init__(self):
        self.events = []

    def info(self, event, **kw):
        self.events.append(("info", event, kw))

    def warning(self, event, **kw):
        self.events.append(("warning", event, kw))

    def bind(self, **kw):  # structlog API surface used defensively
        return self


def _setup(direction=Direction.LONG):
    return models.Setup(
        direction=direction,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.50"),
        target=Decimal("101.00"),
        rr=2.0,
        reason=["strong close above OR high"],
    )


@pytest.mark.asyncio
async def test_logreporter_trade_taken_logs_fields():
    log = _RecordingLogger()
    r = LogReporter(logger=log)
    await r.start()
    await r.trade_taken(_setup(), qty=10, mode="PAPER")
    await r.close()
    kinds = [e[1] for e in log.events]
    assert "trade_taken" in kinds
    ev = next(e for e in log.events if e[1] == "trade_taken")
    kw = ev[2]
    assert kw["direction"] == "LONG"
    assert kw["model"] == "BREAKOUT"
    assert kw["qty"] == 10
    assert kw["mode"] == "PAPER"
    assert kw["entry"] == "100.00"
    assert kw["stop"] == "99.50"
    assert kw["target"] == "101.00"


@pytest.mark.asyncio
async def test_logreporter_session_report_logs_totals_and_trades():
    log = _RecordingLogger()
    r = LogReporter(logger=log)
    s = summary.build_session_summary(
        trades=[_tr("20.00", reason="TARGET"), _tr("-10.00", reason="STOP")],
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10010.00"),
        mode="PAPER",
        symbol="AAPL",
        session_date=date(2026, 6, 19),
        no_trade_reason=None,
    )
    await r.session_report(s)
    ev = next(e for e in log.events if e[1] == "session_report")
    kw = ev[2]
    assert kw["total_pnl"] == "10.00"
    assert kw["total_pnl_pct"] == "0.10"          # fraction 0.001 * 100
    assert kw["wins"] == 1 and kw["losses"] == 1 and kw["breakevens"] == 0
    assert kw["n_trades"] == 2
    assert len(kw["trades"]) == 2
    assert kw["trades"][0]["pnl"] == "20.00"


@pytest.mark.asyncio
async def test_logreporter_session_report_no_trade_zero_and_reason():
    log = _RecordingLogger()
    r = LogReporter(logger=log)
    s = summary.build_session_summary(
        trades=[],
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10000.00"),
        mode="LIVE",
        symbol="MSFT",
        session_date=date(2026, 6, 19),
        no_trade_reason="no confirmed breakout/entry",
    )
    await r.session_report(s)
    kw = next(e for e in log.events if e[1] == "session_report")[2]
    assert kw["total_pnl"] == "0.00"
    assert kw["no_trade_reason"] == "no confirmed breakout/entry"
    assert kw["trades"] == []


# ---------------------------------------------------------------------------
# Task 36: DiscordReporter tests
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

discord = pytest.importorskip("discord")
from orb_bot.reporting.discord import (  # noqa: E402
    DiscordReporter,
    format_session_embed,
    format_trade_line,
)


def test_format_trade_line_long_target_matches_spec():
    t = models.TradeResult(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        qty=10,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("102.00"),
        pnl=Decimal("20.00"),
        pnl_pct=Decimal("20.00") / Decimal("5000.00"),  # 0.004 -> +0.40%
        exit_reason="TARGET",
    )
    line = format_trade_line(t)
    assert line == "LONG 10sh @ 100.00 → 102.00  +$20.00 (+0.40%)  [TARGET]"


def test_format_trade_line_short_stop_signs():
    t = models.TradeResult(
        direction=Direction.SHORT,
        model=Model.RETEST,
        qty=5,
        entry_price=Decimal("50.00"),
        exit_price=Decimal("51.00"),
        pnl=Decimal("-5.00"),
        pnl_pct=Decimal("-5.00") / Decimal("10000.00"),  # -0.0005 -> -0.05%
        exit_reason="STOP",
    )
    line = format_trade_line(t)
    assert line == "SHORT 5sh @ 50.00 → 51.00  -$5.00 (-0.05%)  [STOP]"


class _FakeDiscordClient:
    def __init__(self, opening_range=None):
        self.sent = []
        self.opening_range = opening_range

    async def send_embed(self, embed):
        self.sent.append(embed)


@pytest.mark.asyncio
async def test_discordreporter_session_report_embed_has_denominator_and_caveat():
    rng = models.OpeningRange(
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        established_at=_ts(9, 45),
        width=Decimal("2.00"),
        feed="IEX",
        bars_present=9,            # < T=15 -> low confidence
        low_confidence=True,
    )
    client = _FakeDiscordClient(opening_range=rng)
    r = DiscordReporter(client, timeframe_min=15)
    s = summary.build_session_summary(
        trades=[_tr("20.00", reason="TARGET")],
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10020.00"),
        mode="LIVE",
        symbol="AAPL",
        session_date=date(2026, 6, 19),
        no_trade_reason=None,
    )
    await r.session_report(s)
    assert len(client.sent) == 1
    embed = client.sent[0]
    fields = {f.name: f.value for f in embed.fields}
    assert "bars_present 9/15" in fields["Opening range data"]
    assert "LOW CONFIDENCE" in fields["Opening range data"]
    assert fields["Record"] == "W 1 / L 0 / BE 0"


@pytest.mark.asyncio
async def test_discordreporter_trade_taken_sends_embed():
    client = _FakeDiscordClient()
    r = DiscordReporter(client, timeframe_min=15)
    await r.trade_taken(_setup(), qty=10, mode="PAPER")
    assert len(client.sent) == 1
    fields = {f.name: f.value for f in client.sent[0].fields}
    assert fields["Direction"] == "LONG"
    assert fields["Qty"] == "10"
    assert fields["Entry"] == "100.00"


# ---------------------------------------------------------------------------
# Fix A: absent opening_range -> low-confidence (0 bars) not full-confidence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discordreporter_absent_opening_range_is_low_confidence():
    """When client.opening_range is absent the embed must show 0/T (LOW CONFIDENCE)."""
    client = _FakeDiscordClient(opening_range=None)
    r = DiscordReporter(client, timeframe_min=15)
    s = summary.build_session_summary(
        trades=[],
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10000.00"),
        mode="PAPER",
        symbol="AAPL",
        session_date=date(2026, 6, 19),
        no_trade_reason="window expired",
    )
    await r.session_report(s)
    assert len(client.sent) == 1
    fields = {f.name: f.value for f in client.sent[0].fields}
    assert "bars_present 0/15" in fields["Opening range data"]
    assert "LOW CONFIDENCE" in fields["Opening range data"]


# ---------------------------------------------------------------------------
# Fix B: Trades field overflow guard
# ---------------------------------------------------------------------------

def test_format_session_embed_many_trades_field_within_1024():
    """40-trade summary: 'Trades' field must be ≤1024 chars and include '…(N more)'."""
    trades = [_tr("1.00", reason="TARGET") for _ in range(40)]
    s = summary.build_session_summary(
        trades=trades,
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10040.00"),
        mode="PAPER",
        symbol="AAPL",
        session_date=date(2026, 6, 19),
        no_trade_reason=None,
    )
    embed = format_session_embed(s, bars_present=15, timeframe_min=15, low_confidence=False)
    fields = {f.name: f.value for f in embed.fields}
    trades_value = fields["Trades"]
    assert len(trades_value) <= 1024
    assert "…(" in trades_value and "more)" in trades_value


# ---------------------------------------------------------------------------
# Fix C: send failure must not propagate out of session_report
# ---------------------------------------------------------------------------

class _FailingDiscordClient:
    """Fake client whose send_embed always raises."""

    async def send_embed(self, embed):
        raise RuntimeError("network error")


@pytest.mark.asyncio
async def test_discordreporter_session_report_survives_send_failure():
    """A failing send_embed must not propagate — session_report returns normally."""
    client = _FailingDiscordClient()
    r = DiscordReporter(client, timeframe_min=15)
    s = summary.build_session_summary(
        trades=[_tr("20.00", reason="TARGET")],
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10020.00"),
        mode="PAPER",
        symbol="AAPL",
        session_date=date(2026, 6, 19),
        no_trade_reason=None,
    )
    # Must not raise
    await r.session_report(s)


# ---------------------------------------------------------------------------
# Fix E: additional coverage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_format_session_embed_zero_pnl_color_and_field():
    """Zero-P/L session: color is light_grey and Total realized P/L shows +$0.00."""
    s = summary.build_session_summary(
        trades=[_tr("0.00", reason="FLATTEN")],
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10000.00"),
        mode="PAPER",
        symbol="AAPL",
        session_date=date(2026, 6, 19),
        no_trade_reason=None,
    )
    embed = format_session_embed(s, bars_present=15, timeframe_min=15)
    assert embed.color == discord.Color.light_grey()
    fields = {f.name: f.value for f in embed.fields}
    assert "+$0.00" in fields["Total realized P/L"]


@pytest.mark.asyncio
async def test_discordreporter_session_report_no_trade_reason_in_field():
    """session_report with trades=[] and a no_trade_reason shows both in 'Trades' field."""
    client = _FakeDiscordClient()
    r = DiscordReporter(client, timeframe_min=15)
    s = summary.build_session_summary(
        trades=[],
        start_equity=Decimal("10000.00"),
        end_equity=Decimal("10000.00"),
        mode="PAPER",
        symbol="AAPL",
        session_date=date(2026, 6, 19),
        no_trade_reason="no confirmed breakout",
    )
    await r.session_report(s)
    assert len(client.sent) == 1
    fields = {f.name: f.value for f in client.sent[0].fields}
    assert "No trades" in fields["Trades"]
    assert "no confirmed breakout" in fields["Trades"]


def test_format_trade_line_long_negative_pnl_signs():
    """Stopped-out LONG: pnl negative, pnl_pct negative → -$... (-...%) signs."""
    t = models.TradeResult(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        qty=10,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("98.00"),
        pnl=Decimal("-20.00"),
        pnl_pct=Decimal("-20.00") / Decimal("10000.00"),
        exit_reason="STOP",
    )
    line = format_trade_line(t)
    assert "LONG" in line
    assert "→" in line
    assert "-$20.00" in line
    assert "(-0.20%)" in line
    assert "[STOP]" in line
