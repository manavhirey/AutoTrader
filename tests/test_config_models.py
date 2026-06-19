# tests/test_config_models.py
from datetime import time
from decimal import Decimal

from orb_bot.config import RunConfig, StrategyConfig


def test_strategy_defaults_match_spec():
    cfg = StrategyConfig()
    assert cfg.range_timeframe_min == 15
    assert cfg.confirm_timeframe_min == 15
    assert cfg.entry_timeframe_min == 15
    assert cfg.atr_timeframe_min == 15
    assert cfg.or_min_bars == 15
    assert cfg.trading_window_min == 120
    assert cfg.session_open == time(9, 30)
    assert cfg.flatten_at == time(15, 55)
    assert cfg.flatten_buffer_min == 5
    assert cfg.bar_grace_seconds == 3
    assert cfg.risk_reward_ratio == 2.0
    assert cfg.risk_per_trade_pct == 0.5
    assert cfg.max_trades_per_day == 1
    assert cfg.allow_long is True and cfg.allow_short is True
    assert cfg.equity_source == "live"
    assert cfg.fixed_equity is None
    assert cfg.rearm_opposite_only is True
    assert cfg.enable_breakout is True
    assert cfg.enable_retest is True
    assert cfg.enable_reversal is False
    assert cfg.displacement_model == "IMPULSE"
    assert cfg.tick_size == Decimal("0.01")
    assert cfg.stop_buffer_atr == Decimal("0.10")
    assert cfg.stop_buffer_ticks == 2
    assert cfg.min_stop_distance == Decimal("0.02")
    assert cfg.swing_fractal_k == 1
    assert cfg.swing_lookback == 4
    assert cfg.range_day_sweep_both is True
    assert cfg.range_day_disables == ["breakout", "retest"]
    assert cfg.range_day_enables == []
    assert cfg.sweep_buffer == Decimal("0.0")
    assert cfg.require_higher_tf_bias is False
    assert cfg.atr_period == 14
    assert cfg.approval_timeout_s == 90
    assert cfg.logging_level == "INFO"
    assert cfg.logging_dir == "logs/"


def test_run_defaults_match_spec():
    rc = RunConfig(symbol="SPY")
    assert rc.symbol == "SPY"
    assert rc.live is False
    assert rc.feed == "IEX"
    assert rc.allow_live_iex is False
