"""DiscordReporter: posts trade-taken notices + the end-of-session P/L embed via the
shared discordbot client (spec §14/§17a). The bars_present/T denominator and the
low_confidence caveat are formatted here. Distinct from the `discord` library by
absolute import (orb_bot.reporting.discord)."""
from __future__ import annotations

import logging
from decimal import Decimal

import discord

from orb_bot import models

logger = logging.getLogger(__name__)


def _money(amount: Decimal) -> str:
    """+$20.00 / -$5.00 (sign always shown, two decimals)."""
    sign = "-" if amount < 0 else "+"
    return f"{sign}${abs(amount):.2f}"


def _pct(fraction: Decimal) -> str:
    """Stored fraction -> +0.40% / -0.05% (x100 only at display, spec §17a)."""
    value = fraction * Decimal("100")
    sign = "-" if value < 0 else "+"
    return f"{sign}{abs(value):.2f}%"


def format_trade_line(t: models.TradeResult) -> str:
    """One per-trade embed line, e.g.
    'LONG 10sh @ 100.00 → 102.00  +$20.00 (+0.40%)  [TARGET]'."""
    return (
        f"{t.direction.value} {t.qty}sh @ {t.entry_price:.2f} → {t.exit_price:.2f}  "
        f"{_money(t.pnl)} ({_pct(t.pnl_pct)})  [{t.exit_reason}]"
    )


def format_session_embed(
    summary: models.SessionSummary,
    bars_present: int,
    timeframe_min: int,
    low_confidence: bool = False,
) -> discord.Embed:
    """Compact P/L embed: date, symbol, mode, per-trade lines, total realized P/L
    $ and %, win/loss/breakeven counts, and the bars_present/T data caveat (§17a)."""
    title = f"ORB P/L - {summary.symbol} - {summary.session_date.isoformat()} ({summary.mode})"
    color = discord.Color.green() if summary.total_pnl > 0 else (
        discord.Color.red() if summary.total_pnl < 0 else discord.Color.light_grey()
    )
    embed = discord.Embed(title=title, color=color)

    if summary.trades:
        # Guard: Discord rejects embed field values exceeding 1024 chars.
        # Keep leading complete lines that fit within ~980 chars, then append
        # "…(N more)" so the whole field stays under 1024.
        lines = [format_trade_line(t) for t in summary.trades]
        body = "\n".join(lines)
        if len(body) > 1000:
            kept: list[str] = []
            for line in lines:
                candidate = "\n".join(kept + [line])
                if len(candidate) > 980:
                    break
                kept.append(line)
            dropped = len(lines) - len(kept)
            body = "\n".join(kept) + f"\n…({dropped} more)"
    else:
        reason = summary.no_trade_reason or "no trade"
        body = f"No trades - {reason}"
    embed.add_field(name="Trades", value=body, inline=False)

    embed.add_field(
        name="Total realized P/L",
        value=f"{_money(summary.total_pnl)} ({_pct(summary.total_pnl_pct)})",
        inline=False,
    )
    embed.add_field(
        name="Record",
        value=f"W {summary.wins} / L {summary.losses} / BE {summary.breakevens}",
        inline=False,
    )
    embed.add_field(
        name="Opening range data",
        value=f"bars_present {bars_present}/{timeframe_min}"
        + ("  (LOW CONFIDENCE - partial range)" if low_confidence else ""),
        inline=False,
    )
    embed.set_footer(text=f"start {summary.start_equity} -> end {summary.end_equity}")
    return embed


class DiscordReporter:
    """Reporter Protocol impl posting to a Discord channel via the shared client."""

    def __init__(self, client, *, timeframe_min: int = 15) -> None:
        self._client = client
        self._timeframe_min = timeframe_min

    async def start(self) -> None:
        # Start the shared Discord gateway. Idempotent via the client's
        # double-start guard: in live mode the approver already started the same
        # shared client, so this no-ops; in paper-with-Discord-reporter the
        # reporter is the one that brings the gateway up.
        await self._client.start_in_background()

    async def trade_taken(self, setup: models.Setup, qty: int, mode: str) -> None:
        embed = discord.Embed(
            title=f"Order submitted ({mode})",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Direction", value=setup.direction.value, inline=True)
        embed.add_field(name="Model", value=setup.model.value, inline=True)
        embed.add_field(name="Qty", value=str(qty), inline=True)
        embed.add_field(name="Entry", value=f"{setup.entry:.2f}", inline=True)
        embed.add_field(name="Stop", value=f"{setup.stop:.2f}", inline=True)
        embed.add_field(name="Target", value=f"{setup.target:.2f}", inline=True)
        try:
            await self._client.send_embed(embed)
        except Exception:
            logger.warning("trade_taken Discord send failed; continuing", exc_info=True)

    async def session_report(self, summary: models.SessionSummary) -> None:
        bars_present, low_confidence = self._range_meta()
        embed = format_session_embed(
            summary,
            bars_present=bars_present,
            timeframe_min=self._timeframe_min,
            low_confidence=low_confidence,
        )
        try:
            await self._client.send_embed(embed)
        except Exception:
            logger.warning("session_report Discord send failed; continuing", exc_info=True)

    def _range_meta(self) -> tuple[int, bool]:
        """bars_present/low_confidence sourced from the shared client when the
        orchestrator has stashed the day's OpeningRange (an OpeningRange dataclass)
        there at runtime. Absent means no data arrived yet — treat as low-confidence
        with 0 bars so the embed shows '0/T  (LOW CONFIDENCE - partial range)'."""
        rng = getattr(self._client, "opening_range", None)
        if rng is None:
            return (0, True)
        return (rng.bars_present, rng.low_confidence)

    async def close(self) -> None:
        # Idempotent close of the shared client (no-op if the approver already
        # closed it).
        await self._client.close()
