"""In-memory limit order book with deterministic price-time priority matching.

Data structures:
  * bids / asks: ``sortedcontainers.SortedDict[int, deque[Order]]`` mapping a
    price level to the FIFO queue of resting orders at that price. Insert /
    delete of a price level is O(log n) in the number of *distinct* price
    levels (backed by a skip-list-like structure); FIFO append/pop within a
    level is O(1) via ``collections.deque``.
  * order_map: ``dict[order_id, Order]`` gives O(1) lookup for cancellation
    and quantity introspection.

``SortedDict`` keeps keys in ascending order. Asks are naturally best-first
ascending (lowest ask = best), so the best ask is ``asks.peekitem(0)``. Bids
need best-first descending (highest bid = best), which we get by reading
from the *end* of the same ascending structure: ``bids.peekitem(-1)``. This
avoids maintaining two different comparator structures.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from sortedcontainers import SortedDict

from .models import Order, OrderStatus, OrderType, Side, TradeEvent, next_trade_id


class OrderBook:
    __slots__ = ("bids", "asks", "order_map")

    def __init__(self) -> None:
        self.bids: SortedDict[int, Deque[Order]] = SortedDict()
        self.asks: SortedDict[int, Deque[Order]] = SortedDict()
        self.order_map: Dict[str, Order] = {}

    # -- public API ---------------------------------------------------

    def add_limit_order(self, order: Order) -> List[TradeEvent]:
        """Match against the book, then rest any unfilled remainder."""
        trades = self._match(order)
        if order.remaining_qty > 0:
            book_side = self.bids if order.side == Side.BUY else self.asks
            book_side.setdefault(order.price, deque()).append(order)
            order.status = OrderStatus.PARTIALLY_FILLED if trades else OrderStatus.OPEN
            self.order_map[order.id] = order
        else:
            order.status = OrderStatus.FILLED
        return trades

    def add_market_order(self, order: Order) -> List[TradeEvent]:
        """Match against the book; any unfilled remainder is dropped (not rested)."""
        trades = self._match(order)
        if order.remaining_qty == 0:
            order.status = OrderStatus.FILLED
        elif trades:
            order.status = OrderStatus.PARTIALLY_FILLED
        else:
            order.status = OrderStatus.REJECTED  # no liquidity at all
        return trades

    def cancel(self, order_id: str) -> Optional[Order]:
        """Remove a resting order. Returns the cancelled Order, or None if unknown/already terminal."""
        order = self.order_map.get(order_id)
        if order is None:
            return None
        book_side = self.bids if order.side == Side.BUY else self.asks
        dq = book_side.get(order.price)
        if dq is not None:
            try:
                dq.remove(order)
            except ValueError:
                pass
            if not dq:
                del book_side[order.price]
        order.status = OrderStatus.CANCELLED
        del self.order_map[order_id]
        return order

    def best_bid(self) -> Optional[int]:
        return self.bids.peekitem(-1)[0] if self.bids else None

    def best_ask(self) -> Optional[int]:
        return self.asks.peekitem(0)[0] if self.asks else None

    def snapshot(self, depth: int = 20) -> Dict[str, List[Tuple[int, int]]]:
        """Top-``depth`` L2 levels as (price, aggregate_qty) pairs, best-first."""
        bid_levels: List[Tuple[int, int]] = []
        for price in reversed(self.bids):
            if len(bid_levels) >= depth:
                break
            dq = self.bids[price]
            bid_levels.append((price, sum(o.remaining_qty for o in dq)))

        ask_levels: List[Tuple[int, int]] = []
        for price in self.asks:
            if len(ask_levels) >= depth:
                break
            dq = self.asks[price]
            ask_levels.append((price, sum(o.remaining_qty for o in dq)))

        return {"bids": bid_levels, "asks": ask_levels}

    # -- matching core --------------------------------------------------

    def _match(self, incoming: Order) -> List[TradeEvent]:
        is_buy = incoming.side == Side.BUY
        opposite = self.asks if is_buy else self.bids
        best_index = 0 if is_buy else -1  # asks: best=min (front); bids: best=max (back)

        trades: List[TradeEvent] = []
        while incoming.remaining_qty > 0 and opposite:
            best_price = opposite.peekitem(best_index)[0]
            if incoming.order_type == OrderType.LIMIT:
                if is_buy and best_price > incoming.price:
                    break
                if not is_buy and best_price < incoming.price:
                    break

            dq = opposite[best_price]
            resting = dq[0]
            trade_qty = min(incoming.remaining_qty, resting.remaining_qty)

            incoming.remaining_qty -= trade_qty
            resting.remaining_qty -= trade_qty

            trades.append(
                TradeEvent(
                    trade_id=next_trade_id(),
                    price=best_price,
                    qty=trade_qty,
                    maker_order_id=resting.id,
                    taker_order_id=incoming.id,
                    maker_id=resting.trader_id,
                    taker_id=incoming.trader_id,
                    timestamp=incoming.timestamp,
                    aggressor_side=incoming.side,
                )
            )

            if resting.remaining_qty == 0:
                dq.popleft()
                resting.status = OrderStatus.FILLED
                del self.order_map[resting.id]
                if not dq:
                    del opposite[best_price]
            else:
                resting.status = OrderStatus.PARTIALLY_FILLED

        return trades
