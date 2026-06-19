"""PEP-544 Protocols — the swap seam between the pure engine and live/paper I/O.

The engine imports NONE of these; only the orchestrator does. Concrete live
adapters (alpaca feed/broker, Discord approver/reporter) and backtest doubles
(ReplayFeed, SimBroker, AutoApprover) satisfy the identical Protocols.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from orb_bot.models import (
    AccountSnapshot,
    ApprovalRequest,
    Candle,
    ClockInfo,
    Fill,
    OrderResult,
    SessionSummary,
    Setup,
)


@runtime_checkable
class DataFeed(Protocol):
    """Yields 1m transport candles; the orchestrator aggregates 1m -> T."""

    def candles(self) -> AsyncIterator[Candle]:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class Broker(Protocol):
    async def get_account(self) -> AccountSnapshot:
        ...

    async def get_clock(self) -> ClockInfo:
        ...

    async def submit_bracket(self, setup: Setup, qty: int) -> OrderResult:
        ...

    def trade_updates(self) -> AsyncIterator[Fill]:
        ...

    async def get_order(self, order_id: str) -> OrderResult:
        ...

    async def cancel_all(self) -> None:
        ...

    async def flatten(self) -> None:
        ...


@runtime_checkable
class Approver(Protocol):
    """AutoApprover (paper) | DiscordApprover (live)."""

    async def start(self) -> None:
        ...

    async def request(self, req: ApprovalRequest) -> str:
        ...  # 'APPROVE' | 'REJECT' | 'TIMEOUT'

    async def close(self) -> None:
        ...


@runtime_checkable
class Reporter(Protocol):
    """DiscordReporter | LogReporter."""

    async def start(self) -> None:
        ...

    async def trade_taken(self, setup: Setup, qty: int, mode: str) -> None:
        ...

    async def session_report(self, summary: SessionSummary) -> None:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class Clock(Protocol):
    """tz-aware ET; used ONLY by the orchestrator."""

    def now(self) -> datetime:
        ...
