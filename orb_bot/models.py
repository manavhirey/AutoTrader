"""Domain model: frozen dataclasses + enums shared across all layers (spec §5).

All prices are decimal.Decimal; all timestamps are tz-aware America/New_York.
This module imports nothing from feed/execution/approval/reporting/orchestrator
and is fully pure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


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


# --- Runtime/I-O dataclasses ------------------------------------------------

@dataclass(frozen=True)
class AccountSnapshot:
    equity: Decimal
    buying_power: Decimal
    shorting_enabled: bool


@dataclass(frozen=True)
class ClockInfo:
    is_open: bool
    next_close: datetime


@dataclass
class OrderResult:
    order_id: str
    client_order_id: str
    status: str
    filled_avg_price: Decimal | None
    filled_qty: int
    legs: list


@dataclass
class Fill:
    order_id: str
    client_order_id: str
    leg_role: str                # 'ENTRY' | 'TP' | 'SL' | 'FLATTEN'
    side: str
    price: Decimal
    qty: int
    ts: datetime
    position_qty: int
    exit_reason: str | None      # None on entry


@dataclass
class ApprovalRequest:
    setup: Setup
    symbol: str
    qty: int
    risk_dollars: Decimal
    mode: str                    # 'PAPER' | 'LIVE'
    feed: str
    or_high: Decimal
    or_low: Decimal
    bars_present: int
    data_warning: str | None
    approval_ttl_s: int          # = config approval_timeout_s, copied in by orchestrator


@dataclass(frozen=True)
class TradeResult:
    direction: Direction
    model: Model
    qty: int
    entry_price: Decimal         # share-weighted avg of partial fills
    exit_price: Decimal
    pnl: Decimal
    pnl_pct: Decimal             # STORED as a FRACTION (pnl/start_equity)
    exit_reason: str             # 'TARGET' | 'STOP' | 'FLATTEN'


@dataclass(frozen=True)
class SessionSummary:
    session_date: date
    symbol: str
    mode: str                    # 'PAPER' | 'LIVE'
    trades: list[TradeResult]
    total_pnl: Decimal           # realized
    total_pnl_pct: Decimal       # total_pnl / start_equity (fraction)
    wins: int
    losses: int                  # pnl>0 / pnl<0 / pnl==0; sum == len(trades)
    breakevens: int
    start_equity: Decimal        # live equity at preflight (ALWAYS)
    end_equity: Decimal
    no_trade_reason: str | None  # set when trades == []


# --- EngineEvent union -------------------------------------------------------
# Closed union of small frozen dataclasses the pure engine returns from each call.
@dataclass(frozen=True)
class RangeEstablished:
    opening_range: OpeningRange


@dataclass(frozen=True)
class DirectionConfirmed:
    direction: Direction
    break_level: Decimal


@dataclass(frozen=True)
class RangeDayDetected:
    pass


@dataclass(frozen=True)
class SetupProposed:
    setup: Setup


@dataclass(frozen=True)
class EntryConfirmed:
    pass


@dataclass(frozen=True)
class TradeRecorded:
    pnl: Decimal
    exit_reason: str


@dataclass(frozen=True)
class WindowExpired:
    pass


@dataclass(frozen=True)
class NoOp:
    pass


EngineEvent = (
    RangeEstablished
    | DirectionConfirmed
    | RangeDayDetected
    | SetupProposed
    | EntryConfirmed
    | TradeRecorded
    | WindowExpired
    | NoOp
)
