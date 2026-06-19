# tests/test_config_validators.py
import pytest
from pydantic import ValidationError

from orb_bot.config import RunConfig, StrategyConfig


def _ok(**over):
    return StrategyConfig(**over)


def test_flatten_at_must_be_after_session_open():
    with pytest.raises(ValidationError):
        _ok(flatten_at="09:00")  # before 09:30 session_open


def test_flatten_buffer_min_nonnegative():
    with pytest.raises(ValidationError):
        _ok(flatten_buffer_min=-1)


def test_risk_reward_ratio_must_be_positive():
    with pytest.raises(ValidationError):
        _ok(risk_reward_ratio=0.0)


def test_risk_per_trade_pct_bounds():
    with pytest.raises(ValidationError):
        _ok(risk_per_trade_pct=0.0)
    with pytest.raises(ValidationError):
        _ok(risk_per_trade_pct=100.1)


def test_single_timeframe_gate_confirm_must_equal_range():
    with pytest.raises(ValidationError):
        _ok(confirm_timeframe_min=5)  # != range 15


def test_single_timeframe_gate_entry_must_equal_range():
    with pytest.raises(ValidationError):
        _ok(entry_timeframe_min=5)


def test_timeframe_must_be_in_allowed_set():
    with pytest.raises(ValidationError):
        _ok(
            range_timeframe_min=7,
            confirm_timeframe_min=7,
            entry_timeframe_min=7,
            atr_timeframe_min=7,
            or_min_bars=7,
            trading_window_min=126,  # divisible by 7 to isolate the T-set failure
            retest_max_wait_candles=4,
        )


def test_atr_timeframe_must_equal_range():
    with pytest.raises(ValidationError):
        _ok(atr_timeframe_min=5)


def test_or_min_bars_upper_bound():
    with pytest.raises(ValidationError):
        _ok(or_min_bars=16)  # > range 15


def test_or_min_bars_lower_bound():
    with pytest.raises(ValidationError):
        _ok(or_min_bars=0)  # < 1


def test_swing_lookback_must_cover_fractal():
    with pytest.raises(ValidationError):
        _ok(swing_fractal_k=2, swing_lookback=4)  # need >= 2*2+1 = 5


def test_trading_window_must_be_whole_candles():
    with pytest.raises(ValidationError):
        _ok(trading_window_min=121)  # 121 % 15 != 0


def test_retest_max_wait_must_fit_window():
    with pytest.raises(ValidationError):
        _ok(retest_max_wait_candles=9)  # > 120/15 = 8


def test_equity_source_fixed_requires_fixed_equity():
    with pytest.raises(ValidationError):
        _ok(equity_source="fixed", fixed_equity=None)


def test_enable_reversal_true_rejected():
    with pytest.raises(ValidationError):
        _ok(enable_reversal=True)


def test_require_higher_tf_bias_true_rejected():
    with pytest.raises(ValidationError):
        _ok(require_higher_tf_bias=True)


def test_nonempty_range_day_enables_rejected():
    with pytest.raises(ValidationError):
        _ok(range_day_enables=["reversal"])


def test_valid_t5_config_passes():
    cfg = StrategyConfig(
        range_timeframe_min=5,
        confirm_timeframe_min=5,
        entry_timeframe_min=5,
        atr_timeframe_min=5,
        or_min_bars=5,
        trading_window_min=120,
        retest_max_wait_candles=4,
    )
    assert cfg.range_timeframe_min == 5
    assert cfg.or_min_bars == 5


def test_live_gate_iex_rejected_without_flag():
    with pytest.raises(ValidationError):
        RunConfig(symbol="SPY", live=True, feed="IEX")


def test_live_gate_iex_allowed_with_flag():
    rc = RunConfig(symbol="SPY", live=True, feed="IEX", allow_live_iex=True)
    assert rc.live is True
    assert rc.feed == "IEX"


def test_live_gate_sip_allowed():
    rc = RunConfig(symbol="SPY", live=True, feed="SIP")
    assert rc.live is True
    assert rc.feed == "SIP"
