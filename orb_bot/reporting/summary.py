"""Pure builders for per-trade and per-session P/L (spec §17a). No I/O, no clock."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from orb_bot import models
from orb_bot.models import Direction, Model


def pnl_for(
    direction: Direction, entry_price: Decimal, exit_price: Decimal, qty: int
) -> Decimal:
    """Realized P/L: (exit - entry) * qty * (+1 LONG | -1 SHORT). No commission."""
    sign = Decimal("1") if direction is Direction.LONG else Decimal("-1")
    return (exit_price - entry_price) * Decimal(qty) * sign


def weighted_avg_price(fills: list[models.Fill]) -> Decimal:
    """Share-weighted average price across a leg's partial fills (spec §16/§17a)."""
    if not fills:
        raise ValueError("weighted_avg_price requires at least one fill")
    total_qty = sum(f.qty for f in fills)
    if total_qty <= 0:
        raise ValueError("weighted_avg_price requires positive total quantity")
    notional = sum((f.price * Decimal(f.qty) for f in fills), Decimal("0"))
    return notional / Decimal(total_qty)


def build_trade_result(
    entry_fills: list[models.Fill],
    exit_fills: list[models.Fill],
    direction: Direction,
    model: Model,
    start_equity: Decimal,
    exit_reason: str,
) -> models.TradeResult:
    """One TradeResult from a leg's fills. Entry/exit prices are share-weighted;
    qty = min(entry_qty, exit_qty) (§16); pnl_pct stored as a FRACTION (§17a)."""
    if start_equity <= 0:
        raise ValueError("start_equity must be positive")
    if not entry_fills:
        raise ValueError("build_trade_result requires at least one entry fill")
    if not exit_fills:
        raise ValueError("build_trade_result requires at least one exit fill")
    entry_price = weighted_avg_price(entry_fills)
    exit_price = weighted_avg_price(exit_fills)
    entry_qty = sum(f.qty for f in entry_fills)
    exit_qty = sum(f.qty for f in exit_fills)
    qty = min(entry_qty, exit_qty)
    pnl = pnl_for(direction, entry_price, exit_price, qty)
    pnl_pct = pnl / start_equity
    return models.TradeResult(
        direction=direction,
        model=model,
        qty=qty,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl=pnl,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
    )


def build_session_summary(
    trades: list[models.TradeResult],
    start_equity: Decimal,
    end_equity: Decimal,
    mode: str,
    symbol: str,
    session_date: date,
    no_trade_reason: str | None,
) -> models.SessionSummary:
    """Aggregate per-trade results into the end-of-session summary (spec §17a).

    total_pnl_pct stored as a FRACTION (total_pnl / start_equity). Classification:
    pnl>0 win, pnl<0 loss, pnl==0 breakeven, so wins+losses+breakevens == len(trades).
    No-trade case ⇒ $0.00 totals and no_trade_reason set by the caller.
    """
    if start_equity <= 0:
        raise ValueError("start_equity must be positive")
    total_pnl = sum((t.pnl for t in trades), Decimal("0"))
    if trades:
        # total_pnl_pct is recomputed from per-trade totals, NOT the sum of individual trade pnl_pct
        total_pnl_pct = total_pnl / start_equity
    else:
        total_pnl_pct = Decimal("0")
    wins = sum(1 for t in trades if t.pnl > 0)
    losses = sum(1 for t in trades if t.pnl < 0)
    breakevens = sum(1 for t in trades if t.pnl == 0)
    return models.SessionSummary(
        session_date=session_date,
        symbol=symbol,
        mode=mode,
        trades=list(trades),
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        start_equity=start_equity,
        end_equity=end_equity,
        no_trade_reason=no_trade_reason,
    )
