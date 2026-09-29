"""Core value types for the matching engine.

Orders use `__slots__` (via `@dataclass(slots=True)`) to avoid per-instance
`__dict__` allocation -- with order books holding tens of thousands of live
orders, this measurably cuts memory footprint and attribute-access overhead
on the hot matching path. Equality is left as default identity comparison
(`eq=False`) so `deque.remove()` during cancellation is a cheap identity
scan rather than a field-by-field comparison.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass
from enum import Enum

_order_seq = itertools.count(1)
_trade_seq = itertools.count(1)


def next_order_id() -> str:
    return f"O{next(_order_seq)}-{time.time_ns()}"


def next_trade_id() -> str:
    return f"T{next(_trade_seq)}-{time.time_ns()}"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(slots=True, eq=False)
class Order:
    id: str
    trader_id: str
    side: Side
    order_type: OrderType
    price: int  # integer ticks/cents; 0 for MARKET orders
    qty: int
    timestamp: int  # nanoseconds, time.time_ns()
    remaining_qty: int
    status: OrderStatus = OrderStatus.OPEN


@dataclass(slots=True, frozen=True)
class TradeEvent:
    trade_id: str
    price: int
    qty: int
    maker_order_id: str
    taker_order_id: str
    maker_id: str
    taker_id: str
    timestamp: int
    aggressor_side: Side
