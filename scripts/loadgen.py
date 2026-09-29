#!/usr/bin/env python
"""Async synthetic load generator for the LOB REST API.

Spins up N concurrent async workers, each submitting random LIMIT orders
at a share of the target aggregate rate, and reports achieved
throughput/error counts. Requires the API server to be running.

Usage:
    python scripts/loadgen.py --url http://localhost:8000 --rate 1000 --concurrency 50 --duration 10
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from typing import Dict

import httpx


async def worker(
    client: httpx.AsyncClient,
    base_url: str,
    rate_per_worker: float,
    duration: float,
    stats: Dict[str, int],
    rng: random.Random,
    mid: int,
) -> None:
    interval = 1.0 / rate_per_worker if rate_per_worker > 0 else 0.0
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        payload = {
            "trader_id": f"loadgen-{rng.randint(1, 500)}",
            "side": rng.choice(["BUY", "SELL"]),
            "order_type": "LIMIT",
            "price": mid + rng.randint(-500, 500),
            "qty": rng.randint(1, 50),
        }
        try:
            resp = await client.post(f"{base_url}/api/v1/orders", json=payload, timeout=5.0)
            if resp.status_code == 201:
                stats["ok"] += 1
            else:
                stats["error"] += 1
        except Exception:
            stats["error"] += 1
        stats["total"] += 1

        elapsed = time.monotonic() - t0
        if interval > elapsed:
            await asyncio.sleep(interval - elapsed)


async def report_progress(stats: Dict[str, int], start: float) -> None:
    try:
        while True:
            await asyncio.sleep(2.0)
            elapsed = time.monotonic() - start
            rate = stats["total"] / elapsed if elapsed > 0 else 0.0
            print(
                f"\r[{elapsed:5.1f}s] sent={stats['total']:6d}  rate={rate:7.1f}/s  errors={stats['error']}",
                end="",
                flush=True,
            )
    except asyncio.CancelledError:
        pass


async def main_async(args: argparse.Namespace) -> None:
    stats = {"ok": 0, "error": 0, "total": 0}
    rate_per_worker = args.rate / args.concurrency
    limits = httpx.Limits(max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency)

    async with httpx.AsyncClient(limits=limits) as client:
        start = time.monotonic()
        progress = asyncio.create_task(report_progress(stats, start))
        workers = [
            asyncio.create_task(
                worker(client, args.url, rate_per_worker, args.duration, stats, random.Random(i), args.mid)
            )
            for i in range(args.concurrency)
        ]
        await asyncio.gather(*workers)
        progress.cancel()
        elapsed = time.monotonic() - start

    print()
    print(f"Sent {stats['total']} orders in {elapsed:.2f}s -> {stats['total'] / elapsed:.1f} orders/sec")
    print(f"  ok={stats['ok']}  error={stats['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--rate", type=float, default=1000.0, help="target orders/sec across all workers")
    parser.add_argument("--concurrency", type=int, default=50, help="number of concurrent async workers")
    parser.add_argument("--duration", type=float, default=10.0, help="seconds to run")
    parser.add_argument("--mid", type=int, default=10_000, help="mid price (ticks/cents) to generate orders around")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
