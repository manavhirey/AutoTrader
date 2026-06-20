# tests/test_discordbot.py
import asyncio

import discord.abc
import pytest

from orb_bot.discordbot import DiscordClient


class _FakeChannel(discord.abc.Messageable):
    """Fake channel that satisfies isinstance(ch, discord.abc.Messageable)."""

    def __init__(self, cid: int) -> None:
        self.id = cid

    async def _get_channel(self):  # type: ignore[override]
        return self  # type: ignore[return-value]


class _FakeBot:
    """Stand-in for discord.Client: records start/close, fakes channel lookups."""

    def __init__(self, *, raise_on_start: Exception | None = None):
        self.started_with = None
        self.closed = False
        self._ready = asyncio.Event()
        self._cache: dict = {}
        self._fetchable: dict = {}
        self._raise_on_start = raise_on_start

    async def start(self, token):
        if self._raise_on_start is not None:
            raise self._raise_on_start
        self.started_with = token
        self._ready.set()
        # emulate a long-lived connection until cancelled
        await asyncio.Event().wait()

    async def wait_until_ready(self):
        await self._ready.wait()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def get_channel(self, cid):
        return self._cache.get(cid)

    async def fetch_channel(self, cid):
        if cid in self._fetchable:
            return self._fetchable[cid]
        raise RuntimeError(f"no channel {cid}")

    async def close(self):
        self.closed = True


async def test_start_in_background_starts_and_awaits_ready():
    bot = _FakeBot()
    dc = DiscordClient(token="tok", channel_id=42, bot=bot)
    await dc.start_in_background()
    assert bot.started_with == "tok"
    assert dc._task is not None and not dc._task.done()
    await dc.close()
    assert bot.closed is True


async def test_resolve_channel_prefers_cache():
    bot = _FakeBot()
    ch = _FakeChannel(42)
    bot._cache[42] = ch
    dc = DiscordClient(token="tok", channel_id=42, bot=bot)
    await dc.start_in_background()
    resolved = await dc.resolve_channel()
    assert resolved is ch
    await dc.close()


async def test_resolve_channel_falls_back_to_fetch():
    bot = _FakeBot()
    ch = _FakeChannel(99)
    bot._fetchable[99] = ch  # not in get_channel cache
    dc = DiscordClient(token="tok", channel_id=99, bot=bot)
    await dc.start_in_background()
    resolved = await dc.resolve_channel()
    assert resolved is ch
    await dc.close()


async def test_close_cancels_task_when_bot_close_hangs():
    bot = _FakeBot()
    dc = DiscordClient(token="tok", channel_id=42, bot=bot)
    await dc.start_in_background()
    task = dc._task
    await dc.close()
    assert task.cancelled() or task.done()


async def test_start_in_background_raises_on_failed_start():
    """Fix A: start_in_background must raise (not hang) when bot.start() fails."""
    err = RuntimeError("bad token / LoginFailure")
    bot = _FakeBot(raise_on_start=err)
    dc = DiscordClient(token="tok", channel_id=42, bot=bot)
    with pytest.raises(RuntimeError, match="bad token"):
        await asyncio.wait_for(dc.start_in_background(), timeout=2)


async def test_resolve_channel_raises_when_not_messageable():
    """Fix D: resolve_channel must raise RuntimeError for non-messageable channels."""

    class _NotAChannel:
        """Returned by the fake bot but does NOT implement Messageable."""
        pass

    class _NonMsgBot(_FakeBot):
        def get_channel(self, cid):
            return _NotAChannel()

    bot = _NonMsgBot()
    dc = DiscordClient(token="tok", channel_id=42, bot=bot)
    await dc.start_in_background()
    with pytest.raises(RuntimeError, match="not messageable"):
        await dc.resolve_channel()
    await dc.close()


async def test_send_embed_forwards_embed_to_channel_send():
    """send_embed resolves the channel and calls channel.send(embed=...)."""
    import discord as discord_lib

    class _RecordingChannel(discord.abc.Messageable):
        def __init__(self, cid: int) -> None:
            self.id = cid
            self.sent_embeds: list[discord_lib.Embed] = []

        async def _get_channel(self):  # type: ignore[override]
            return self  # type: ignore[return-value]

        async def send(self, *, embed=None, **kwargs):  # type: ignore[override]
            self.sent_embeds.append(embed)

    bot = _FakeBot()
    ch = _RecordingChannel(42)
    bot._cache[42] = ch
    dc = DiscordClient(token="tok", channel_id=42, bot=bot)
    await dc.start_in_background()
    embed = discord_lib.Embed(title="Test embed")
    await dc.send_embed(embed)
    assert len(ch.sent_embeds) == 1
    assert ch.sent_embeds[0] is embed
    await dc.close()
