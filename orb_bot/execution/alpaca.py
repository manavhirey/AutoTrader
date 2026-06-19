from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

from ..config import RunConfig
from ..models import AccountSnapshot, ClockInfo

_KNOWN_LEGS = {"ENTRY", "TP", "SL", "FLATTEN"}


def client_order_id(session_date: date, symbol: str, seq: int, leg_role: str) -> str:
    """Deterministic id: prefix ``{date}-{symbol}-{seq}`` shared across a trade's legs,
    suffix ``-{leg_role}``. Prefix is the P/L grouping key (spec §5/§17a)."""
    if leg_role not in _KNOWN_LEGS:
        raise ValueError(f"unknown leg_role {leg_role!r}")
    return f"{session_date.isoformat()}-{symbol}-{seq}-{leg_role}"


def leg_role_for(coid: str, parent_coid: str) -> str:
    """Classify a fill's client_order_id by its suffix. Unknown/auto ids (e.g. a
    broker-initiated close_all_positions) are treated as FLATTEN (spec §13/§16)."""
    suffix = coid.rsplit("-", 1)[-1]
    return suffix if suffix in _KNOWN_LEGS else "FLATTEN"


def whole_share_qty(qty: int) -> int:
    """Defense-in-depth: brackets are whole-share; reject qty < 1 (spec §16)."""
    if qty < 1:
        raise ValueError(f"qty must be >= 1, got {qty}")
    return int(qty)


class AlpacaBroker:
    """Concrete Broker over alpaca-py. Every sync TradingClient call is offloaded with
    asyncio.to_thread so it never blocks the event loop (spec §13/§16)."""

    def __init__(
        self,
        run: RunConfig,
        key: str,
        secret: str,
        *,
        client: Any = None,
        stream: Any = None,
        session_date: date,
        _client_factory: Any = None,
    ) -> None:
        self._run = run
        self._session_date = session_date
        self._seq = 0
        self._parent_coid: str | None = None
        if client is None:
            if _client_factory is not None:
                client = _client_factory(key, secret, paper=not run.live)
            else:
                from alpaca.trading.client import TradingClient

                client = TradingClient(key, secret, paper=not run.live)
        if stream is None:
            from alpaca.trading.stream import TradingStream

            stream = TradingStream(key, secret, paper=not run.live)
        self._client = client
        self._stream = stream
        self._queue: asyncio.Queue = asyncio.Queue()

    async def get_account(self) -> AccountSnapshot:
        acct = await asyncio.to_thread(self._client.get_account)
        return AccountSnapshot(
            equity=Decimal(str(acct.equity)),
            buying_power=Decimal(str(acct.buying_power)),
            shorting_enabled=bool(acct.shorting_enabled),
        )

    async def get_clock(self) -> ClockInfo:
        clk = await asyncio.to_thread(self._client.get_clock)
        return ClockInfo(is_open=bool(clk.is_open), next_close=clk.next_close)
