"""Typed configuration for orb_bot. Loads strategy + run flags from YAML and
secrets from .env; validation runs at load time so a bad config fails fast,
before any network connection."""
from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # session / timing
    timezone: str = "America/New_York"
    session_open: time = time(9, 30)
    range_timeframe_min: int = 15
    confirm_timeframe_min: int = 15
    entry_timeframe_min: int = 15
    trading_window_min: int = 120
    flatten_at: time = time(15, 55)
    flatten_buffer_min: int = 5
    bar_grace_seconds: int = 3

    # risk / sizing
    risk_reward_ratio: float = 2.0
    risk_per_trade_pct: float = 0.5
    max_trades_per_day: int = 1
    allow_long: bool = True
    allow_short: bool = True
    equity_source: Literal["live", "fixed"] = "live"
    fixed_equity: Decimal | None = None
    rearm_opposite_only: bool = True

    # entry-model toggles
    enable_breakout: bool = True
    enable_retest: bool = True
    enable_reversal: bool = False

    # strong close
    strong_close_body_ratio: float = 0.60
    strong_close_location: float = 0.70

    # displacement
    displacement_model: Literal["IMPULSE", "FVG", "TRUE_GAP"] = "IMPULSE"
    fvg_min_size_ticks: int = 2
    impulse_atr_mult: float = 1.5

    # retest
    retest_tolerance_atr: float = 0.25
    retest_max_wait_candles: int = 4
    retest_confirm_body_ratio: float = 0.50

    # stops / structure
    tick_size: Decimal = Decimal("0.01")
    stop_buffer_atr: Decimal = Decimal("0.10")
    stop_buffer_ticks: int = 2
    min_stop_distance: Decimal = Decimal("0.02")
    swing_fractal_k: int = 1
    swing_lookback: int = 4

    # day-type filter
    range_day_sweep_both: bool = True
    range_day_disables: list[str] = Field(default_factory=lambda: ["breakout", "retest"])
    range_day_enables: list[str] = Field(default_factory=list)
    sweep_buffer: Decimal = Decimal("0.0")
    require_higher_tf_bias: bool = False

    # ATR
    atr_period: int = 14
    atr_timeframe_min: int = 15
    or_min_bars: int = 15

    # approval
    approval_timeout_s: int = 90

    # logging
    logging_level: str = "INFO"
    logging_dir: str = "logs/"


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    live: bool = False
    feed: Literal["IEX", "SIP"] = "IEX"
    allow_live_iex: bool = False
