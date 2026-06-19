"""`python -m orb_bot`: load config, build mode-appropriate dependencies, run.

Wiring rules (spec §4, §14, §17a):
  - approver: AutoApprover (paper) | DiscordApprover (live)
  - reporter: DiscordReporter when Discord is configured, else LogReporter
  - a single shared discordbot.DiscordClient is built ONCE when Discord is
    configured and passed to BOTH the DiscordApprover and the DiscordReporter.
Secrets are FLAT on Settings (settings.alpaca_key, settings.discord_token, ...);
there is no settings.secrets. The Orchestrator and Engine are mode-agnostic;
only the injected impls differ.
"""
from __future__ import annotations

import asyncio
import datetime
from zoneinfo import ZoneInfo

from orb_bot.approval.auto import AutoApprover
from orb_bot.approval.manual import DiscordApprover
from orb_bot.config import Settings, load_config
from orb_bot.discordbot import DiscordClient
from orb_bot.engine import Engine
from orb_bot.execution.alpaca import AlpacaBroker
from orb_bot.feed.alpaca import AlpacaFeed
from orb_bot.orchestrator import Orchestrator
from orb_bot.reporting.discord import DiscordReporter
from orb_bot.reporting.log import LogReporter

# All concrete impls are imported at module level so __main__ is the only
# network-aware wiring point. Tests monkeypatch these names on this module.


def _reveal_str(v: object) -> str:
    """Extract a plain str from a SecretStr, or cast a plain str/None to str.

    Handles both a real pydantic.SecretStr (has .get_secret_value()) and a
    plain str that a test may inject via monkeypatch.
    NEVER log the return value — it may be a secret.
    """
    getter = getattr(v, "get_secret_value", None)
    if getter is not None:
        return str(getter())
    return str(v) if v is not None else ""


class _SystemClock:
    """Tz-aware ET wall clock; orchestrator-only (engine stays pure)."""

    def __init__(self, tz: str) -> None:
        self._tz = ZoneInfo(tz)

    def now(self) -> datetime.datetime:
        return datetime.datetime.now(self._tz)


def _discord_configured(settings: Settings) -> bool:
    """Return True iff both a non-empty discord_token and discord_channel_id are set.

    Uses _reveal_str() to avoid SecretStr always-truthy pitfall.
    """
    raw_token = getattr(settings, "discord_token", None)
    if raw_token is None:
        return False
    revealed = _reveal_str(raw_token)
    return bool(revealed) and bool(getattr(settings, "discord_channel_id", None))


def build_orchestrator(settings: Settings) -> Orchestrator:
    """Select mode-appropriate dependencies and build the Orchestrator.

    Paper mode: AutoApprover, LogReporter (unless Discord configured).
    Live mode:  DiscordApprover + DiscordReporter via a single shared DiscordClient.
    Hard live-gate (defense-in-depth): raises RuntimeError if live and no Discord.
    """
    cfg = settings.strategy
    run = settings.run
    clock = _SystemClock(cfg.timezone)
    session_date = clock.now().date()

    # Resolve SecretStr → plain str before passing to SDK constructors.
    api_key: str = _reveal_str(settings.alpaca_key)
    secret_key: str = _reveal_str(settings.alpaca_secret)

    feed = AlpacaFeed(
        api_key=api_key,
        secret_key=secret_key,
        symbol=run.symbol,
        feed=run.feed,
    )
    broker = AlpacaBroker(
        run=run,
        key=api_key,
        secret=secret_key,
        session_date=session_date,
    )

    # Build ONE shared DiscordClient when Discord is configured and pass it to
    # both the live approver and the Discord reporter (spec §14).
    discord_client: DiscordClient | None = None
    if _discord_configured(settings):
        channel_id = settings.discord_channel_id
        assert channel_id is not None  # guarded by _discord_configured
        discord_client = DiscordClient(
            _reveal_str(settings.discord_token),
            channel_id,
        )

    # Hard live-gate: live mode MUST have a Discord client (approval gate).
    # Config validators enforce this at load time; this is defense-in-depth at
    # the wiring layer — AutoApprover is never used for live.
    if run.live and discord_client is None:
        raise RuntimeError(
            "live mode requires Discord configured (approval gate): "
            "set DISCORD_TOKEN and DISCORD_CHANNEL_ID"
        )

    approver: DiscordApprover | AutoApprover
    if run.live:
        assert discord_client is not None  # guaranteed by live-gate above
        assert settings.discord_approver_user_id is not None  # enforced by config validator
        approver = DiscordApprover(discord_client, settings.discord_approver_user_id)
    else:
        approver = AutoApprover()

    reporter: DiscordReporter | LogReporter
    if discord_client is not None:
        reporter = DiscordReporter(discord_client, timeframe_min=cfg.range_timeframe_min)
    else:
        reporter = LogReporter()

    engine = Engine(cfg, session_date)

    return Orchestrator(settings, feed, broker, approver, reporter, clock, engine)


def main() -> None:
    """Entry point for `python -m orb_bot`."""
    settings = load_config("config/config.yaml")
    orch = build_orchestrator(settings)
    asyncio.run(orch.run())


if __name__ == "__main__":
    main()
