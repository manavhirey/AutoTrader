# orb_bot/discordbot.py
"""Shared discord.py client.

A single ``discord.Client`` is started via ``bot.start(token)`` as an asyncio
TASK (never ``bot.run()``, which blocks the loop) so it can share the event loop
with the Alpaca stream. The same client is reused by the live ``DiscordApprover``
and the ``DiscordReporter`` (both modes whenever Discord is configured).
"""
from __future__ import annotations

import asyncio
import logging

import discord

logger = logging.getLogger(__name__)


def _default_bot() -> discord.Client:
    # Buttons (discord.ui.View) need NO privileged message_content intent.
    intents = discord.Intents.none()
    intents.guilds = True
    return discord.Client(intents=intents)


class DiscordClient:
    def __init__(
        self,
        token: str | None,
        channel_id: int,
        bot: discord.Client | None = None,
    ) -> None:
        self._token = token
        self._channel_id = int(channel_id)
        self.bot: discord.Client = bot if bot is not None else _default_bot()
        self._task: asyncio.Task[None] | None = None

    async def start_in_background(self) -> None:
        """Start ``bot.start(token)`` as a task and wait until the gateway is ready."""
        if self._task is not None and self._task.done():
            # Re-entrant call: surface any exception from a previously failed start
            self._task.result()
            return
        if self._task is None:
            self._task = asyncio.create_task(self.bot.start(self._token))  # type: ignore[arg-type]
            self._task.add_done_callback(_log_task_exception)
            self._token = None  # Fix F: drop token reference after scheduling

        ready = asyncio.ensure_future(self.bot.wait_until_ready())
        done, _pending = await asyncio.wait(
            {self._task, ready}, return_when=asyncio.FIRST_COMPLETED
        )
        if self._task in done:
            # start() returned or raised before the gateway became ready
            ready.cancel()
            try:
                await ready
            except (asyncio.CancelledError, Exception):
                pass
            self._task.result()  # re-raises start() exception or returns cleanly

    @property
    def is_ready(self) -> bool:
        """True when the gateway is connected and the background task is still live."""
        return (
            self.bot.is_ready()
            and self._task is not None
            and not self._task.done()
        )

    async def resolve_channel(self) -> discord.abc.Messageable:
        """Return the configured channel: cache first, then HTTP fetch fallback."""
        ch = self.bot.get_channel(self._channel_id)
        if ch is None:
            ch = await self.bot.fetch_channel(self._channel_id)
        if not isinstance(ch, discord.abc.Messageable):
            raise RuntimeError(
                f"channel {self._channel_id} is not messageable or not found:"
                f" {type(ch).__name__}"
            )
        return ch

    async def send_embed(self, embed: discord.Embed) -> None:
        """Send an embed to the configured channel."""
        channel = await self.resolve_channel()
        await channel.send(embed=embed)

    async def close(self) -> None:
        """Close the gateway connection and cancel the background task."""
        try:
            await self.bot.close()
        finally:
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.warning(
                        "error awaiting cancelled discord task", exc_info=True
                    )
                self._task = None


def _log_task_exception(task: asyncio.Task[None]) -> None:
    """Done-callback: log unexpected exceptions from the background start task."""
    if not task.cancelled() and task.exception() is not None:
        logger.error("Discord background task failed", exc_info=task.exception())
