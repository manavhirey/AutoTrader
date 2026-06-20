"""Live-mode approver: posts a rich embed + Approve/Reject buttons (discord.ui.View).

Buttons (not message replies) mean NO privileged ``message_content`` intent is
needed. ``interaction_check`` allows only ``DISCORD_APPROVER_USER_ID``. The
button callback first ACKs Discord via ``interaction.response.edit_message``
(within the ~3s window; this also disables the buttons), THEN resolves the
in-process ``asyncio.Future``. Timeout / unauthorized / error => non-approval;
never auto-approve.
"""
from __future__ import annotations

import asyncio
import logging

import discord

from orb_bot.discordbot import DiscordClient
from orb_bot.models import ApprovalRequest, Direction

logger = logging.getLogger(__name__)


def build_proposal_embed(req: ApprovalRequest) -> discord.Embed:
    s = req.setup
    arrow = "🟢" if s.direction is Direction.LONG else "🔴"
    title = f"{arrow} {req.symbol} {s.direction.value} {s.model.value} — {req.mode}"
    color = discord.Color.green() if s.direction is Direction.LONG else discord.Color.red()
    em = discord.Embed(title=title, color=color)
    em.add_field(name="Entry", value=f"{s.entry:.2f}", inline=True)
    em.add_field(name="Stop", value=f"{s.stop:.2f}", inline=True)
    em.add_field(name="Target", value=f"{s.target:.2f}", inline=True)
    em.add_field(name="RR", value=f"{s.rr:.1f}", inline=True)
    em.add_field(name="Qty", value=str(req.qty), inline=True)
    em.add_field(name="Risk $", value=f"{req.risk_dollars:.2f}", inline=True)
    em.add_field(name="OR", value=f"{req.or_low:.2f} – {req.or_high:.2f}", inline=True)
    em.add_field(name="Feed", value=req.feed, inline=True)
    em.add_field(name="Bars", value=f"{req.bars_present}/T", inline=True)
    if req.feed != "SIP":
        em.add_field(name="⚠ Feed warning", value=f"{req.feed} data", inline=False)
    if req.data_warning:
        em.add_field(name="⚠ Data warning", value=req.data_warning, inline=False)
    em.add_field(name="Reasons", value="\n".join(f"• {r}" for r in s.reason), inline=False)
    em.set_footer(text=f"Mode: {req.mode} · expires in {req.approval_ttl_s}s")
    return em


class ApprovalView(discord.ui.View):
    def __init__(self, approver_user_id: int, fut: asyncio.Future, timeout: float) -> None:
        super().__init__(timeout=timeout)
        self._approver_user_id = int(approver_user_id)
        self._fut = fut

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the configured approver's clicks count; log and reject all others.
        if interaction.user is not None and interaction.user.id == self._approver_user_id:
            return True
        logger.warning(
            "ignoring approval click from unauthorized user id=%s",
            getattr(interaction.user, "id", None),
        )
        return False

    async def _resolve(self, interaction: discord.Interaction, decision: str, label: str) -> None:
        # ACK Discord FIRST (edit_message both acks and removes the buttons in one HTTP call),
        # THEN resolve the Future, guarded against double-resolution.
        try:
            await interaction.response.edit_message(content=label, view=None)
        except Exception:
            decision = "REJECT"  # error path => non-approval, never auto-approve
        # The outer asyncio.wait_for in request() may have already CANCELLED the
        # future, so we check done() (not just cancelled()) — this guard is
        # load-bearing and must NOT be weakened to a cancelled()-only check.
        if not self._fut.done():
            self._fut.set_result(decision)
        self.stop()

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "APPROVE", "✅ Approved")

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._resolve(interaction, "REJECT", "❌ Rejected")

    async def on_timeout(self) -> None:
        # Same load-bearing done() guard as in _resolve: wait_for may have already
        # cancelled the future, so check done() not just cancelled().
        if not self._fut.done():
            self._fut.set_result("TIMEOUT")


class DiscordApprover:
    def __init__(self, client: DiscordClient, approver_user_id: int) -> None:
        self._client = client
        self._approver_user_id = int(approver_user_id)

    async def start(self) -> None:
        # Start the shared Discord gateway (idempotent: start_in_background has a
        # double-start guard, so if the reporter shares this client only one ws
        # connection is made). Without this the live approval gate is dead and
        # every trade is REJECTED (is_ready False -> fail-closed).
        await self._client.start_in_background()

    async def request(self, req: ApprovalRequest) -> str:
        # Fail closed: if the gateway is not ready, reject without sending.
        if not getattr(self._client, "is_ready", False):
            logger.warning(
                "DiscordApprover: client not ready — rejecting without posting"
            )
            return "REJECT"

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        view = ApprovalView(self._approver_user_id, fut, float(req.approval_ttl_s))

        try:
            channel = await self._client.resolve_channel()
            embed = build_proposal_embed(req)
            await channel.send(embed=embed, view=view)
        except Exception:
            logger.exception("DiscordApprover: failed to post approval request — rejecting")
            if not fut.done():
                fut.set_result("REJECT")
            view.stop()
            return "REJECT"

        try:
            decision = await asyncio.wait_for(fut, timeout=float(req.approval_ttl_s) + 5.0)
        except TimeoutError:
            logger.warning("DiscordApprover: wait_for timed out — returning TIMEOUT")
            view.stop()
            return "TIMEOUT"
        # Do NOT catch asyncio.CancelledError — let it propagate.
        return decision

    async def close(self) -> None:
        # Idempotent close of the shared client (no-op if already closed).
        await self._client.close()
