"""Domain model: frozen dataclasses + enums shared across all layers (spec §5).

All prices are decimal.Decimal; all timestamps are tz-aware America/New_York.
This module imports nothing from feed/execution/approval/reporting/orchestrator
and is fully pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Union


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Model(Enum):
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"
    REVERSAL = "REVERSAL"


class State(Enum):
    IDLE = "IDLE"
    BUILDING_RANGE = "BUILDING_RANGE"
    RANGE_SET = "RANGE_SET"
    WAIT_CONFIRMATION = "WAIT_CONFIRMATION"
    WAIT_ENTRY = "WAIT_ENTRY"
    IN_TRADE = "IN_TRADE"
    DONE = "DONE"


@dataclass(frozen=True)
class Candle:
    ts_open: datetime            # tz-aware ET; bar start
    ts_close: datetime           # ts_open + timeframe
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    timeframe_min: int           # 1 (transport) | T = range_timeframe_min (default 15)
    data_incomplete: bool = False
    bars_present: int | None = None


@dataclass(frozen=True)
class OpeningRange:
    high: Decimal
    low: Decimal
    established_at: datetime
    width: Decimal
    feed: str
    bars_present: int
    low_confidence: bool


@dataclass(frozen=True)
class Setup:
    direction: Direction
    model: Model
    entry: Decimal
    stop: Decimal
    target: Decimal
    rr: float
    reason: list[str]


@dataclass(frozen=True)
class Displacement:
    type: str                    # 'IMPULSE' | 'FVG'
    upper_candle: Candle
    lower_candle: Candle
    size: Decimal
