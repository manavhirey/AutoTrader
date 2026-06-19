"""LogReporter: logs trade-taken notices + the end-of-session P/L summary to the
structured logger / console. Used in BOTH modes when Discord is unconfigured (§17/§17a)."""
from __future__ import annotations

import structlog

from orb_bot import models


class LogReporter:
    """Reporter Protocol impl that emits structured log lines (no Discord)."""

    def __init__(self, logger=None) -> None:
        self._log = logger if logger is not None else structlog.get_logger("orb_bot.report")

    async def start(self) -> None:
        return None

    async def trade_taken(self, setup: models.Setup, qty: int, mode: str) -> None:
        self._log.info(
            "trade_taken",
            direction=setup.direction.value,
            model=setup.model.value,
            entry=f"{setup.entry:.2f}",
            stop=f"{setup.stop:.2f}",
            target=f"{setup.target:.2f}",
            rr=setup.rr,
            qty=qty,
            mode=mode,
        )

    async def session_report(self, summary: models.SessionSummary) -> None:
        self._log.info(
            "session_report",
            session_date=summary.session_date.isoformat(),
            symbol=summary.symbol,
            mode=summary.mode,
            n_trades=len(summary.trades),
            total_pnl=f"{summary.total_pnl:.2f}",
            total_pnl_pct=f"{summary.total_pnl_pct * 100:.2f}",
            wins=summary.wins,
            losses=summary.losses,
            breakevens=summary.breakevens,
            start_equity=f"{summary.start_equity:.2f}",
            end_equity=f"{summary.end_equity:.2f}",
            no_trade_reason=summary.no_trade_reason,
            trades=[
                {
                    "direction": t.direction.value,
                    "model": t.model.value,
                    "qty": t.qty,
                    "entry": f"{t.entry_price:.2f}",
                    "exit": f"{t.exit_price:.2f}",
                    "pnl": f"{t.pnl:.2f}",
                    "pnl_pct": f"{t.pnl_pct * 100:.2f}",
                    "exit_reason": t.exit_reason,
                }
                for t in summary.trades
            ],
        )

    async def close(self) -> None:
        return None
