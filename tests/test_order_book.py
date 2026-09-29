import time

from lob.models import Order, OrderStatus, OrderType, Side
from lob.order_book import OrderBook


def make_order(side, price, qty, order_type=OrderType.LIMIT, order_id=None, trader_id="t"):
    return Order(
        id=order_id or f"{side}-{price}-{qty}-{time.time_ns()}",
        trader_id=trader_id,
        side=side,
        order_type=order_type,
        price=price,
        qty=qty,
        timestamp=time.time_ns(),
        remaining_qty=qty,
    )


def test_resting_order_no_cross():
    book = OrderBook()
    order = make_order(Side.BUY, 100, 10, order_id="b1")
    trades = book.add_limit_order(order)
    assert trades == []
    assert book.best_bid() == 100
    assert order.status == OrderStatus.OPEN
    assert "b1" in book.order_map


def test_exact_full_fill_removes_price_level():
    book = OrderBook()
    book.add_limit_order(make_order(Side.SELL, 100, 10, order_id="s1"))
    buy = make_order(Side.BUY, 100, 10, order_id="b1")
    trades = book.add_limit_order(buy)

    assert len(trades) == 1
    assert trades[0].price == 100
    assert trades[0].qty == 10
    assert buy.status == OrderStatus.FILLED
    assert book.best_ask() is None
    assert "s1" not in book.order_map
    assert "b1" not in book.order_map  # fully filled, never rested


def test_partial_fill_leaves_remainder_resting():
    book = OrderBook()
    book.add_limit_order(make_order(Side.SELL, 100, 5, order_id="s1"))
    buy = make_order(Side.BUY, 100, 10, order_id="b1")
    trades = book.add_limit_order(buy)

    assert len(trades) == 1
    assert trades[0].qty == 5
    assert buy.remaining_qty == 5
    assert buy.status == OrderStatus.PARTIALLY_FILLED
    assert book.best_bid() == 100
    assert book.order_map["b1"].remaining_qty == 5


def test_price_time_priority_fifo_at_same_price():
    book = OrderBook()
    first = make_order(Side.SELL, 100, 5, order_id="s1")
    second = make_order(Side.SELL, 100, 5, order_id="s2")
    book.add_limit_order(first)
    book.add_limit_order(second)

    buy = make_order(Side.BUY, 100, 5, order_id="b1")
    trades = book.add_limit_order(buy)

    assert len(trades) == 1
    assert trades[0].maker_order_id == "s1"  # first-in-first-matched
    assert "s1" not in book.order_map
    assert "s2" in book.order_map


def test_best_price_priority_across_levels():
    book = OrderBook()
    book.add_limit_order(make_order(Side.SELL, 102, 5, order_id="s_high"))
    book.add_limit_order(make_order(Side.SELL, 100, 5, order_id="s_low"))

    buy = make_order(Side.BUY, 105, 5, order_id="b1")
    trades = book.add_limit_order(buy)

    assert trades[0].maker_order_id == "s_low"
    assert trades[0].price == 100  # trade executes at the resting (maker) price


def test_limit_order_does_not_cross_outside_price():
    book = OrderBook()
    book.add_limit_order(make_order(Side.SELL, 110, 5, order_id="s1"))
    buy = make_order(Side.BUY, 100, 5, order_id="b1")
    trades = book.add_limit_order(buy)

    assert trades == []
    assert book.best_bid() == 100
    assert book.best_ask() == 110


def test_market_order_sweeps_multiple_levels():
    book = OrderBook()
    book.add_limit_order(make_order(Side.SELL, 100, 5, order_id="s1"))
    book.add_limit_order(make_order(Side.SELL, 101, 5, order_id="s2"))
    book.add_limit_order(make_order(Side.SELL, 102, 10, order_id="s3"))

    market_buy = make_order(Side.BUY, 0, 12, order_type=OrderType.MARKET, order_id="mb1")
    trades = book.add_market_order(market_buy)

    assert len(trades) == 3
    assert [t.price for t in trades] == [100, 101, 102]
    assert market_buy.remaining_qty == 0
    assert market_buy.status == OrderStatus.FILLED
    assert book.best_ask() == 102
    assert book.order_map["s3"].remaining_qty == 8  # 10 - (12 - 5 - 5)


def test_market_order_partial_liquidity_remainder_dropped():
    book = OrderBook()
    book.add_limit_order(make_order(Side.SELL, 100, 5, order_id="s1"))
    market_buy = make_order(Side.BUY, 0, 20, order_type=OrderType.MARKET, order_id="mb1")
    trades = book.add_market_order(market_buy)

    assert len(trades) == 1
    assert market_buy.remaining_qty == 15
    assert market_buy.status == OrderStatus.PARTIALLY_FILLED
    assert "mb1" not in book.order_map  # market orders are never rested


def test_market_order_no_liquidity_is_rejected():
    book = OrderBook()
    market_buy = make_order(Side.BUY, 0, 10, order_type=OrderType.MARKET, order_id="mb1")
    trades = book.add_market_order(market_buy)

    assert trades == []
    assert market_buy.status == OrderStatus.REJECTED


def test_cancel_removes_resting_order():
    book = OrderBook()
    book.add_limit_order(make_order(Side.BUY, 100, 10, order_id="b1"))
    cancelled = book.cancel("b1")

    assert cancelled is not None
    assert cancelled.status == OrderStatus.CANCELLED
    assert book.best_bid() is None
    assert "b1" not in book.order_map


def test_cancel_empties_price_level_dict_entry():
    book = OrderBook()
    book.add_limit_order(make_order(Side.BUY, 100, 10, order_id="b1"))
    book.cancel("b1")
    assert 100 not in book.bids


def test_cancel_unknown_order_returns_none():
    book = OrderBook()
    assert book.cancel("does-not-exist") is None


def test_snapshot_depth_and_ordering():
    book = OrderBook()
    for i, price in enumerate([98, 99, 100]):
        book.add_limit_order(make_order(Side.BUY, price, 1, order_id=f"b{i}"))
    for i, price in enumerate([101, 102, 103]):
        book.add_limit_order(make_order(Side.SELL, price, 1, order_id=f"s{i}"))

    snap = book.snapshot(depth=2)
    assert snap["bids"] == [(100, 1), (99, 1)]  # best-first descending
    assert snap["asks"] == [(101, 1), (102, 1)]  # best-first ascending
