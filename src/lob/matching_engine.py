"""Single-writer actor around the OrderBook.

All order/cancel commands are serialized through one `asyncio.Queue`
consumed by exactly one coroutine (`_run`). This gives the engine
lock-free, sequential, deterministic order processing: concurrent API
handlers may all `await` submission concurrently, but the book itself is
only ever touched by one coroutine at a time, so no mutex is needed around
`OrderBook` mutation.

Each command is WAL-appended (and fsync'd) *before* it is applied to the
book, satisfying the "durable before visible" invariant required for crash
recovery.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .models import Order, OrderStatus, OrderType, Side, TradeEvent, next_order_id
from .order_book import OrderBook
from .pubsub import PubSub
from .wal import WriteAheadLog


@dataclass(slots=True)
class _Command:
    payload: Dict[str, Any]
    future: "asyncio.Future[Dict[str, Any]]"


class MatchingEngine:
    def __init__(self, wal: WriteAheadLog, pubsub: PubSub) -> None:
        self._wal = wal
        self._pubsub = pubsub
        self.book = OrderBook()
        self._queue: "asyncio.Queue[_Command]" = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    # -- lifecycle --------------------------------------------------

    def replay(self) -> None:
        """Rebuild in-memory book state from the WAL. Call before `start()`."""
        for record in self._wal.replay():
            self._apply(record, persist=False)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="matching-engine-loop")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._wal.close()

    async def _run(self) -> None:
        while True:
            cmd = await self._queue.get()
            try:
                result = self._apply(cmd.payload, persist=True)
                if not cmd.future.done():
                    cmd.future.set_result(result)
            except Exception as exc:  # noqa: BLE001 - surface to the awaiting caller
                if not cmd.future.done():
                    cmd.future.set_exception(exc)

    # -- command application -----------------------------------------

    def _apply(self, record: Dict[str, Any], persist: bool) -> Dict[str, Any]:
        if persist:
            self._wal.append(record)

        kind = record["type"]
        if kind in ("NEW_LIMIT_ORDER", "NEW_MARKET_ORDER"):
            order = Order(
                id=record["id"],
                trader_id=record["trader_id"],
                side=Side(record["side"]),
                order_type=OrderType(record["order_type"]),
                price=record["price"],
                qty=record["qty"],
                timestamp=record["timestamp"],
                remaining_qty=record["qty"],
            )
            if order.order_type == OrderType.LIMIT:
                trades = self.book.add_limit_order(order)
            else:
                trades = self.book.add_market_order(order)
            if persist:
                self._publish_fill_events(trades)
            return {"order": order, "trades": trades}

        if kind == "CANCEL":
            cancelled = self.book.cancel(record["order_id"])
            if persist and cancelled is not None:
                self._pubsub.publish({"type": "L2_UPDATE", "data": self.book.snapshot()})
            return {"cancelled": cancelled}

        raise ValueError(f"Unknown WAL record type: {kind!r}")

    def _publish_fill_events(self, trades: List[TradeEvent]) -> None:
        for t in trades:
            self._pubsub.publish(
                {
                    "type": "TRADE_EXECUTION",
                    "data": {
                        "trade_id": t.trade_id,
                        "price": t.price,
                        "qty": t.qty,
                        "maker_id": t.maker_id,
                        "taker_id": t.taker_id,
                        "timestamp": t.timestamp,
                    },
                }
            )
        self._pubsub.publish({"type": "L2_UPDATE", "data": self.book.snapshot()})

    # -- public command API -------------------------------------------

    async def submit_limit_order(self, trader_id: str, side: Side, price: int, qty: int) -> Dict[str, Any]:
        record = {
            "type": "NEW_LIMIT_ORDER",
            "id": next_order_id(),
            "trader_id": trader_id,
            "side": side.value,
            "order_type": OrderType.LIMIT.value,
            "price": price,
            "qty": qty,
            "timestamp": time.time_ns(),
        }
        return await self._submit(record)

    async def submit_market_order(self, trader_id: str, side: Side, qty: int) -> Dict[str, Any]:
        record = {
            "type": "NEW_MARKET_ORDER",
            "id": next_order_id(),
            "trader_id": trader_id,
            "side": side.value,
            "order_type": OrderType.MARKET.value,
            "price": 0,
            "qty": qty,
            "timestamp": time.time_ns(),
        }
        return await self._submit(record)

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        record = {"type": "CANCEL", "order_id": order_id}
        return await self._submit(record)

    async def _submit(self, record: Dict[str, Any]) -> Dict[str, Any]:
        fut: "asyncio.Future[Dict[str, Any]]" = asyncio.get_running_loop().create_future()
        await self._queue.put(_Command(payload=record, future=fut))
        return await fut
