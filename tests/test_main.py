"""Wiring tests for orb_bot.__main__ (mode-based dependency injection).

All concrete impl names are monkeypatched — no network calls.
"""
from zoneinfo import ZoneInfo

import pytest

from orb_bot import __main__ as main_mod
from orb_bot.config import RunConfig, Settings, StrategyConfig

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _alpaca_env(monkeypatch):
    # Function-scoped, auto-reverted: inject Alpaca + Discord secrets via
    # monkeypatch so Settings() can load them (including live mode),
    # WITHOUT mutating global os.environ at import time.
    monkeypatch.setenv("ALPACA_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET", "test-secret")
    monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123456789")
    monkeypatch.setenv("DISCORD_APPROVER_USER_ID", "987654321")


class _Stub:
    def __init__(self, *a, **k):
        self.args = a
        self.kwargs = k


@pytest.fixture
def patched(monkeypatch):
    created = {}

    def mk(name):
        def factory(*a, **k):
            inst = _Stub(*a, **k)
            created.setdefault(name, []).append(inst)
            return inst
        return factory

    monkeypatch.setattr(main_mod, "AlpacaFeed", mk("AlpacaFeed"))
    monkeypatch.setattr(main_mod, "AlpacaBroker", mk("AlpacaBroker"))
    monkeypatch.setattr(main_mod, "AutoApprover", mk("AutoApprover"))
    monkeypatch.setattr(main_mod, "DiscordApprover", mk("DiscordApprover"))
    monkeypatch.setattr(main_mod, "DiscordReporter", mk("DiscordReporter"))
    monkeypatch.setattr(main_mod, "LogReporter", mk("LogReporter"))
    monkeypatch.setattr(main_mod, "DiscordClient", mk("DiscordClient"))
    return created


def _shared_client_factory(created):
    """Factory recording each DiscordClient construction (to assert ONE shared client)."""
    def factory(*a, **k):
        inst = _Stub(*a, **k)
        created.setdefault("DiscordClient", []).append(inst)
        return inst
    return factory


def _paper_settings():
    return Settings(strategy=StrategyConfig(), run=RunConfig(symbol="AAPL", live=False))


def _live_settings():
    return Settings(strategy=StrategyConfig(), run=RunConfig(symbol="AAPL", live=True, feed="SIP"))


def test_build_orchestrator_paper_uses_auto_approver(patched, monkeypatch):
    # no discord secrets (FLAT fields) => LogReporter
    s = _paper_settings()
    monkeypatch.setattr(s, "discord_token", None, raising=False)
    o = main_mod.build_orchestrator(s)
    assert "AutoApprover" in patched
    assert "DiscordApprover" not in patched
    assert "LogReporter" in patched
    assert o.run_cfg.live is False


def test_build_orchestrator_live_uses_discord_approver(patched, monkeypatch):
    s = _live_settings()
    monkeypatch.setattr(s, "discord_token", "tok", raising=False)
    monkeypatch.setattr(s, "discord_channel_id", 123, raising=False)
    monkeypatch.setattr(s, "discord_approver_user_id", 999, raising=False)
    # shared DiscordClient is built once and passed to both approver + reporter
    monkeypatch.setattr(main_mod, "DiscordClient", _shared_client_factory(patched))
    main_mod.build_orchestrator(s)
    assert "DiscordApprover" in patched
    assert "AutoApprover" not in patched
    assert "DiscordReporter" in patched
    assert len(patched["DiscordClient"]) == 1  # ONE shared client


def test_build_orchestrator_uses_log_reporter_when_discord_absent(patched, monkeypatch):
    s = _paper_settings()
    monkeypatch.setattr(s, "discord_token", None, raising=False)
    main_mod.build_orchestrator(s)
    assert "LogReporter" in patched
    assert "DiscordReporter" not in patched


def test_build_orchestrator_live_without_discord_raises(patched, monkeypatch):
    # Hard live-gate: live mode with no Discord config must raise RuntimeError.
    s = _live_settings()
    monkeypatch.setattr(s, "discord_token", None, raising=False)
    monkeypatch.setattr(s, "discord_channel_id", None, raising=False)
    with pytest.raises(RuntimeError, match="live mode requires Discord"):
        main_mod.build_orchestrator(s)


def test_reveal_str_resolves_secrets_before_sdk_call(patched):
    # _reveal_str ensures that a SecretStr object NEVER reaches the Alpaca/Discord SDK;
    # instead, a plain str is extracted and passed. Build a paper orchestrator
    # to verify AlpacaFeed and AlpacaBroker receive plain-str credentials.
    s = _paper_settings()
    main_mod.build_orchestrator(s)

    # Check AlpacaFeed received plain-str credentials
    feed_kwargs = patched["AlpacaFeed"][0].kwargs
    assert isinstance(feed_kwargs["api_key"], str)
    assert not hasattr(feed_kwargs["api_key"], "get_secret_value")  # not a SecretStr
    assert isinstance(feed_kwargs["secret_key"], str)
    assert not hasattr(feed_kwargs["secret_key"], "get_secret_value")  # not a SecretStr

    # Check AlpacaBroker received plain-str credentials
    broker_kwargs = patched["AlpacaBroker"][0].kwargs
    assert isinstance(broker_kwargs["key"], str)
    assert not hasattr(broker_kwargs["key"], "get_secret_value")  # not a SecretStr
    assert isinstance(broker_kwargs["secret"], str)
    assert not hasattr(broker_kwargs["secret"], "get_secret_value")  # not a SecretStr
