#!/usr/bin/env python
"""Latency benchmark for the matching engine's core operations.

Measures p50/p90/p99/max latency (microseconds) for three operations —
resting-order insert, crossing (matching) insert, and cancel — at order
book depths ranging from 1,000 to 50,000 live orders. Pure in-memory,
no asyncio/FastAPI/WAL overhead, so this isolates `OrderBook` itself.

Usage:
    python benches/benchmark_matching.py
    python benches/benchmark_matching.py --depths 1000 10000 50000 --iterations 2000
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lob.models import Order, OrderType, Side, next_order_id  # noqa: E402
from lob.order_book import OrderBook  # noqa: E402


def make_order(side: Side, price: int, qty: int = 10, order_type: OrderType = OrderType.LIMIT) -> Order:
    return Order(
        id=next_order_id(),
        trader_id="bench",
        side=side,
        order_type=order_type,
        price=price,
        qty=qty,
        timestamp=time.time_ns(),
        remaining_qty=qty,
    )


def populate(book: OrderBook, depth: int, mid: int, spread: int) -> None:
    rng = random.Random(42)
    for _ in range(depth // 2):
        bid_price = mid - spread - rng.randint(0, spread)
        ask_price = mid + spread + rng.randint(0, spread)
        book.add_limit_order(make_order(Side.BUY, bid_price))
        book.add_limit_order(make_order(Side.SELL, ask_price))


def bench_resting_insert(depth: int, iterations: int, mid: int, spread: int) -> List[float]:
    """Non-crossing limit order that simply rests in the book."""
    book = OrderBook()
    populate(book, depth, mid, spread)
    rng = random.Random(1)
    latencies = []
    for _ in range(iterations):
        price = mid - spread - rng.randint(0, spread)
        order = make_order(Side.BUY, price)
        t0 = time.perf_counter_ns()
        book.add_limit_order(order)
        t1 = time.perf_counter_ns()
        latencies.append((t1 - t0) / 1_000)
    return latencies


def bench_crossing_match(depth: int, iterations: int, mid: int, spread: int) -> List[float]:
    """Aggressive limit order that sweeps the best ask and fills immediately."""
    book = OrderBook()
    populate(book, depth, mid, spread)
    latencies = []
    for _ in range(iterations):
        order = make_order(Side.BUY, mid + spread * 3, qty=10)
        t0 = time.perf_counter_ns()
        book.add_limit_order(order)
        t1 = time.perf_counter_ns()
        latencies.append((t1 - t0) / 1_000)
        # replenish liquidity so depth stays roughly constant across iterations
        book.add_limit_order(make_order(Side.SELL, mid + spread + 1))
    return latencies


def bench_cancel(depth: int, iterations: int, mid: int, spread: int) -> List[float]:
    book = OrderBook()
    populate(book, depth, mid, spread)
    ids = list(book.order_map.keys())
    random.Random(7).shuffle(ids)
    latencies = []
    for order_id in ids[:iterations]:
        t0 = time.perf_counter_ns()
        book.cancel(order_id)
        t1 = time.perf_counter_ns()
        latencies.append((t1 - t0) / 1_000)
    return latencies


def summarize(name: str, depth: int, samples: List[float]) -> str:
    s = sorted(samples)
    n = len(s)

    def pct(p: float) -> float:
        idx = min(n - 1, int(n * p / 100))
        return s[idx]

    return (
        f"{name:<16} depth={depth:<7} n={n:<6} "
        f"p50={pct(50):8.2f}us  p90={pct(90):8.2f}us  p99={pct(99):8.2f}us  max={s[-1]:8.2f}us"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--depths", type=int, nargs="+", default=[1_000, 5_000, 10_000, 25_000, 50_000])
    parser.add_argument("--iterations", type=int, default=5_000)
    parser.add_argument("--mid", type=int, default=10_000)
    parser.add_argument("--spread", type=int, default=2_000)
    args = parser.parse_args()

    for depth in args.depths:
        print(summarize("resting_insert", depth, bench_resting_insert(depth, args.iterations, args.mid, args.spread)))
        print(summarize("crossing_match", depth, bench_crossing_match(depth, args.iterations, args.mid, args.spread)))
        print(summarize("cancel", depth, bench_cancel(depth, min(args.iterations, depth), args.mid, args.spread)))
        print()


if __name__ == "__main__":
    main()
