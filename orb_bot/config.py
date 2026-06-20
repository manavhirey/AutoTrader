"""Typed configuration for orb_bot. Loads strategy + run flags from YAML and
secrets from .env; validation runs at load time so a bad config fails fast,
before any network connection."""
from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


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

    # --- field-level validators (fail fast, per spec §7) ---
    @field_validator("flatten_buffer_min")
    @classmethod
    def _buffer_nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("flatten_buffer_min must be >= 0")
        return v

    @field_validator("risk_reward_ratio")
    @classmethod
    def _rr_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("risk_reward_ratio must be > 0")
        return v

    @field_validator("tick_size")
    @classmethod
    def _tick_size_positive(cls, v: Decimal) -> Decimal:
        # the engine divides by tick_size when quantizing stop/target prices;
        # fail fast at load rather than DivisionByZero deep in setup-construction.
        if v <= 0:
            raise ValueError("tick_size must be > 0")
        return v

    @field_validator("risk_per_trade_pct")
    @classmethod
    def _risk_pct_bounds(cls, v: float) -> float:
        if not (0 < v <= 100):
            raise ValueError("risk_per_trade_pct must satisfy 0 < pct <= 100")
        return v

    @field_validator("enable_reversal")
    @classmethod
    def _reversal_deferred(cls, v: bool) -> bool:
        if v:
            raise ValueError("enable_reversal is DEFERRED this build; must be False")
        return v

    @field_validator("require_higher_tf_bias")
    @classmethod
    def _htf_bias_deferred(cls, v: bool) -> bool:
        if v:
            raise ValueError("require_higher_tf_bias is DEFERRED this build; must be False")
        return v

    @field_validator("range_day_enables")
    @classmethod
    def _enables_must_be_implemented(cls, v: list[str]) -> list[str]:
        implemented = {"breakout", "retest"}  # reversal deferred this build
        bad = [m for m in v if m not in implemented]
        if bad:
            raise ValueError(
                f"range_day_enables lists unimplemented model(s): {bad}"
            )
        return v

    # --- whole-model validators (cross-field, per spec §7) ---
    @model_validator(mode="after")
    def _validate_cross_fields(self) -> StrategyConfig:
        T = self.range_timeframe_min

        # flatten_at > session_open
        if self.flatten_at <= self.session_open:
            raise ValueError("flatten_at must be after session_open")

        # single-timeframe gate: confirm == entry == range
        if not (self.confirm_timeframe_min == self.entry_timeframe_min == T):
            raise ValueError(
                "single-timeframe gate: confirm_timeframe_min == "
                "entry_timeframe_min == range_timeframe_min required"
            )

        # T in allowed set
        if T not in (1, 5, 15):
            raise ValueError("range_timeframe_min (T) must be one of {1, 5, 15}")

        # whole-candle trading window
        if self.trading_window_min % T != 0:
            raise ValueError(
                "trading_window_min must be a whole multiple of range_timeframe_min"
            )

        # retest wait must fit the window
        if self.retest_max_wait_candles > self.trading_window_min // T:
            raise ValueError(
                "retest_max_wait_candles must fit the trading window "
                "(<= trading_window_min / range_timeframe_min)"
            )

        # timeframe-scaling gate: ATR on the run's timeframe
        if self.atr_timeframe_min != T:
            raise ValueError("atr_timeframe_min must == range_timeframe_min")

        # opening T-bar holds at most T one-minute children
        if not (1 <= self.or_min_bars <= T):
            raise ValueError("or_min_bars must satisfy 1 <= or_min_bars <= range_timeframe_min")

        # swing window must cover a full fractal pivot
        if self.swing_lookback < 2 * self.swing_fractal_k + 1:
            raise ValueError("swing_lookback must be >= 2 * swing_fractal_k + 1")

        # fixed equity source requires a value
        if self.equity_source == "fixed" and self.fixed_equity is None:
            raise ValueError("equity_source == 'fixed' requires fixed_equity")

        return self


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    live: bool = False
    feed: Literal["IEX", "SIP"] = "IEX"
    allow_live_iex: bool = False

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, v: str) -> str:
        # Alpaca returns/stores symbols upper-cased; normalize at the boundary so
        # order submission AND the flatten safety gate (which compares the account's
        # position symbols against run.symbol) stay consistent regardless of how the
        # symbol was typed in config.
        s = v.strip().upper()
        if not s:
            raise ValueError("symbol must be non-empty")
        return s

    @model_validator(mode="after")
    def _live_gate(self) -> RunConfig:
        if self.live and self.feed != "SIP" and not self.allow_live_iex:
            raise ValueError(
                "live trading requires feed='SIP' (or set allow_live_iex=True to override)"
            )
        return self


class Settings(BaseSettings):
    """Top-level settings: secrets from env/.env, strategy+run from YAML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- secrets (env / .env) — no insecure defaults ---
    alpaca_key: SecretStr = Field(validation_alias="ALPACA_KEY")
    alpaca_secret: SecretStr = Field(validation_alias="ALPACA_SECRET")
    discord_token: SecretStr | None = Field(default=None, validation_alias="DISCORD_TOKEN")
    discord_channel_id: int | None = Field(
        default=None, validation_alias="DISCORD_CHANNEL_ID"
    )
    discord_approver_user_id: int | None = Field(
        default=None, validation_alias="DISCORD_APPROVER_USER_ID"
    )

    # --- nested config (YAML) ---
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    run: RunConfig

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # yaml_file is injected via model_config by load_config; read it here
        yaml_path = settings_cls.model_config.get("yaml_file")
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]
        if yaml_path is not None:
            sources.append(
                YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path)
            )
        sources.append(file_secret_settings)
        return tuple(sources)

    @model_validator(mode="after")
    def _validate_live_requirements(self) -> Settings:
        # live mode requires the full Discord approval gate
        if self.run.live and not (
            self.discord_token
            and self.discord_channel_id is not None
            and self.discord_approver_user_id is not None
        ):
            raise ValueError(
                "run.live requires DISCORD_TOKEN, DISCORD_CHANNEL_ID and "
                "DISCORD_APPROVER_USER_ID (the live approval gate)"
            )
        return self


def load_config(path: str | Path) -> Settings:
    """Load Settings from secrets (env/.env) + the given YAML file.

    Validation runs at load time so a bad config fails before any network
    connection is made.
    """
    yaml_path = Path(path)

    class _BoundSettings(Settings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
            yaml_file=yaml_path,
        )

    return _BoundSettings()  # type: ignore[call-arg]
