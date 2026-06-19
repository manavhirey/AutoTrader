"""In-memory doubles for alpaca-py used by execution tests — NO network, NO real SDK.

FakeTradingClient records every request object passed to submit_order so tests can
assert MarketOrderRequest fields, and tracks to_thread offloading by recording the
OS thread id each sync method runs on (must differ from the event-loop thread)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FakeAccount:
    equity: str = "100000.00"
    buying_power: str = "200000.00"
    shorting_enabled: bool = True


@dataclass
class FakeClock:
    is_open: bool = True
    next_close: datetime | None = None


@dataclass
class FakeOrderLeg:
    id: str
    client_order_id: str
    status: str = "new"
    filled_avg_price: str | None = None
    filled_qty: str = "0"


@dataclass
class FakeOrder:
    id: str = "ord-1"
    client_order_id: str = "coid-1"
    status: str = "accepted"
    filled_avg_price: str | None = None
    filled_qty: str = "0"
    legs: list = field(default_factory=list)


@dataclass
class FakeTradingClient:
    account: FakeAccount = field(default_factory=FakeAccount)
    clock: FakeClock = field(default_factory=FakeClock)
    next_order: FakeOrder = field(default_factory=FakeOrder)
    submitted: list = field(default_factory=list)        # request objects
    canceled_all: int = 0
    closed_all: list = field(default_factory=list)        # cancel_orders kwargs
    thread_ids: list = field(default_factory=list)        # records the thread each sync call ran on

    def get_account(self):
        self.thread_ids.append(threading.get_ident())
        return self.account

    def get_clock(self):
        self.thread_ids.append(threading.get_ident())
        return self.clock

    def submit_order(self, order_data):
        self.thread_ids.append(threading.get_ident())
        self.submitted.append(order_data)
        return self.next_order

    def get_order_by_id(self, order_id):
        self.thread_ids.append(threading.get_ident())
        return self.next_order

    def cancel_orders(self):
        self.thread_ids.append(threading.get_ident())
        self.canceled_all += 1

    def close_all_positions(self, cancel_orders: bool = False):
        self.thread_ids.append(threading.get_ident())
        self.closed_all.append(cancel_orders)


class FakeTradingStream:
    """Captures the registered trade-updates handler; lets tests push events."""

    def __init__(self) -> None:
        self.handler = None
        self.run_forever_started = False
        self.stopped = False

    def subscribe_trade_updates(self, handler):
        self.handler = handler

    async def _run_forever(self):
        self.run_forever_started = True

    async def stop_ws(self):
        self.stopped = True
