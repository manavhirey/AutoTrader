from __future__ import annotations

import asyncio
import logging
from datetime import date
from decimal import Decimal
from typing import Any

from ..config import RunConfig
from ..models import AccountSnapshot, ClockInfo, Direction, Fill, OrderResult, Setup

logger = logging.getLogger(__name__)

_KNOWN_LEGS = {"ENTRY", "TP", "SL", "FLATTEN"}
_EXIT_REASON = {"TP": "TARGET", "SL": "STOP", "FLATTEN": "FLATTEN"}
_FILL_EVENTS = {"fill", "partial_fill"}


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


def _to_order_result(order: Any) -> OrderResult:
    """Map an alpaca-py Order (or fake) to our broker-agnostic OrderResult.

    The SDK's ``status`` is an ``OrderStatus`` str-Enum whose ``str()`` is the member
    repr (``'OrderStatus.ACCEPTED'``) on 3.11+, so we take ``.value`` to preserve the
    wire string our orchestrator compares against (spec §16). ``filled_qty`` is typed
    ``str | float | None`` and may arrive float-shaped (``'10.0'``), so route through
    ``Decimal`` before ``int`` rather than ``int('10.0')`` (which raises)."""
    status = getattr(order.status, "value", order.status)
    fap = getattr(order, "filled_avg_price", None)
    return OrderResult(
        order_id=str(order.id),
        client_order_id=str(order.client_order_id),
        status=str(status),
        filled_avg_price=None if fap is None else Decimal(str(fap)),
        filled_qty=int(Decimal(str(getattr(order, "filled_qty", 0) or 0))),
        legs=list(getattr(order, "legs", []) or []),
    )


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

    @property
    def current_seq(self) -> int:
        """Seq of the most recently submitted bracket. The orchestrator's EOD
        FLATTEN synthesis reuses this so both sides format identical coid prefixes
        via :func:`client_order_id` (MED #12 / spec §8 step 9)."""
        return self._seq

    async def submit_bracket(self, setup: Setup, qty: int) -> OrderResult:
        """Submit a BRACKET market order for ``setup`` at ``qty`` shares.

        Prices come straight from the engine's ``Setup`` (TP above / SL below for
        LONG, inverted for SHORT — spec §13), so the broker is direction-agnostic
        on prices and only flips ``side``. Offloaded via ``to_thread`` so the
        blocking SDK call never stalls the event loop (spec §13/§16)."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        qty = whole_share_qty(qty)  # defense-in-depth; rejects qty < 1 before any state change
        self._seq += 1
        entry_coid = client_order_id(self._session_date, self._run.symbol, self._seq, "ENTRY")
        self._parent_coid = entry_coid
        side = OrderSide.BUY if setup.direction is Direction.LONG else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=self._run.symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            client_order_id=entry_coid,
            take_profit=TakeProfitRequest(limit_price=float(setup.target)),
            stop_loss=StopLossRequest(stop_price=float(setup.stop)),
        )
        order = await asyncio.to_thread(self._client.submit_order, req)
        return _to_order_result(order)

    async def get_order(self, order_id: str) -> OrderResult:
        """Fetch an order by id and map via _to_order_result."""
        order = await asyncio.to_thread(self._client.get_order_by_id, order_id)
        return _to_order_result(order)

    async def cancel_all(self) -> None:
        """Cancel ALL open orders account-wide (alpaca cancel_orders). Unguarded primitive
        — the orchestrator owns cancel-before-flatten ordering (spec §13/§16)."""
        await asyncio.to_thread(self._client.cancel_orders)

    async def flatten(self) -> None:
        """Liquidate ALL open positions account-wide via close_all_positions(cancel_orders=True),
        idempotent server-side. Unguarded primitive — caller owns safety/ordering."""
        await asyncio.to_thread(self._client.close_all_positions, cancel_orders=True)

    async def _on_trade_update(self, data: Any) -> None:
        """SDK async callback → enqueue a Fill (only for (partial_)fill events).
        canceled/rejected are not fills; the orchestrator handles those via reconciliation."""
        try:
            raw = getattr(data, "event", "")
            event = str(getattr(raw, "value", raw))
            if event not in _FILL_EVENTS:
                return
            order = data.order
            coid = str(order.client_order_id)
            role = leg_role_for(coid, self._parent_coid or "")
            fap = order.filled_avg_price
            price_raw = fap if fap is not None else getattr(data, "price", None)
            if price_raw is None:
                logger.warning("dropping trade update (no price): event=%s coid=%s", event, coid)
                return
            fill = Fill(
                order_id=str(order.id),
                client_order_id=coid,
                leg_role=role,
                side=str(getattr(order.side, "value", order.side)),
                price=Decimal(str(price_raw)),
                qty=int(Decimal(str(getattr(order, "filled_qty", 0) or 0))),
                ts=data.timestamp,
                position_qty=int(Decimal(str(getattr(data, "position_qty", 0) or 0))),
                exit_reason=_EXIT_REASON.get(role),
            )
            await self._queue.put(fill)
        except Exception:
            raw_event = getattr(data, "event", "?")
            coid_ctx = getattr(getattr(data, "order", None), "client_order_id", "?")
            logger.exception("dropping trade update: event=%s coid=%s", raw_event, coid_ctx)
            return

    async def start_stream(self) -> None:
        """Register the trade-updates handler and schedule _run_forever as an asyncio task.
        Never calls stream.run() or asyncio.run() — those block the event loop (spec §13).
        Guard against double-start: if a live task already exists, return immediately."""
        existing = getattr(self, "_stream_task", None)
        if existing is not None and not existing.done():
            return
        self._stream.subscribe_trade_updates(self._on_trade_update)
        self._stream_task = asyncio.create_task(self._stream._run_forever())

    async def stop_stream(self) -> None:
        """Gracefully stop the stream: await stop_ws() first, then cancel the task.
        Never calls the sync stop() method (misbehaves on the same event loop)."""
        await self._stream.stop_ws()
        task = getattr(self, "_stream_task", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def trade_updates(self):
        """Async generator that drains self._queue and yields Fill objects.
        Sync def with async generator body — matches AsyncIterator[Fill] Protocol."""
        async def _gen():
            while True:
                fill = await self._queue.get()
                yield fill

        return _gen()
