import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from orb_bot.config import load_config

REPO_YAML = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_loads_repo_config_yaml(monkeypatch):
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("DISCORD_APPROVER_USER_ID", raising=False)
    cfg = load_config(REPO_YAML)
    assert cfg.alpaca_key.get_secret_value() == "k"
    assert cfg.alpaca_secret.get_secret_value() == "s"
    assert cfg.run.symbol == "SPY"
    assert cfg.run.live is False
    assert cfg.strategy.range_timeframe_min == 15


def test_env_overrides_yaml_for_secrets(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "QQQ"}
    """)
    monkeypatch.setenv("ALPACA_KEY", "envkey")
    monkeypatch.setenv("ALPACA_SECRET", "envsecret")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    cfg = load_config(p)
    assert cfg.alpaca_key.get_secret_value() == "envkey"
    assert cfg.run.symbol == "QQQ"


def test_missing_required_secret_fails(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY"}
    """)
    monkeypatch.delenv("ALPACA_KEY", raising=False)
    monkeypatch.setenv("ALPACA_SECRET", "s")
    with pytest.raises(ValidationError):
        load_config(p)


def test_live_requires_sip_unless_allow_live_iex(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY", live: true, feed: "IEX", allow_live_iex: false}
    """)
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "1")
    monkeypatch.setenv("DISCORD_APPROVER_USER_ID", "2")
    with pytest.raises(ValidationError):
        load_config(p)


def test_live_iex_allowed_with_override(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY", live: true, feed: "IEX", allow_live_iex: true}
    """)
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "1")
    monkeypatch.setenv("DISCORD_APPROVER_USER_ID", "2")
    cfg = load_config(p)
    assert cfg.run.live is True and cfg.run.feed == "IEX"


def test_live_requires_all_discord_values(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY", live: true, feed: "SIP"}
    """)
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)  # missing one => reject
    monkeypatch.setenv("DISCORD_APPROVER_USER_ID", "2")
    with pytest.raises(ValidationError):
        load_config(p)


def test_paper_discord_optional(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY", live: false, feed: "SIP"}
    """)
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("DISCORD_APPROVER_USER_ID", raising=False)
    cfg = load_config(p)
    assert cfg.discord_token is None
    assert cfg.run.live is False


def test_secrets_not_exposed_in_repr(monkeypatch, tmp_path):
    p = _write_yaml(tmp_path, """
        strategy: {}
        run: {symbol: "SPY"}
    """)
    monkeypatch.setenv("ALPACA_KEY", "envkey")
    monkeypatch.setenv("ALPACA_SECRET", "envsecret")
    monkeypatch.setenv("DISCORD_TOKEN", "discordtoken")
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("DISCORD_APPROVER_USER_ID", raising=False)
    cfg = load_config(p)
    blob = repr(cfg) + str(cfg)
    assert "envkey" not in blob
    assert "envsecret" not in blob
    assert "discordtoken" not in blob
