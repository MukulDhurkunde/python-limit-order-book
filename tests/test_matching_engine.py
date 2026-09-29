import asyncio

import pytest

from lob.matching_engine import MatchingEngine
from lob.models import OrderStatus, Side
from lob.pubsub import PubSub
from lob.wal import WriteAheadLog


async def make_engine(wal_path):
    wal = WriteAheadLog(wal_path)
    engine = MatchingEngine(wal, PubSub())
    engine.replay()
    engine.start()
    return engine


@pytest.mark.asyncio
async def test_submit_and_match_limit_orders(tmp_path):
    engine = await make_engine(tmp_path / "wal.jsonl")
    try:
        sell = await engine.submit_limit_order("alice", Side.SELL, 100, 10)
        assert sell["order"].status == OrderStatus.OPEN

        buy = await engine.submit_limit_order("bob", Side.BUY, 100, 10)
        assert buy["order"].status == OrderStatus.FILLED
        assert len(buy["trades"]) == 1
        assert buy["trades"][0].maker_id == "alice"
        assert buy["trades"][0].taker_id == "bob"
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_cancel_via_engine(tmp_path):
    engine = await make_engine(tmp_path / "wal.jsonl")
    try:
        result = await engine.submit_limit_order("alice", Side.BUY, 100, 10)
        order_id = result["order"].id

        cancel_result = await engine.cancel_order(order_id)
        assert cancel_result["cancelled"] is not None
        assert engine.book.best_bid() is None
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_concurrent_submissions_are_serialized_deterministically(tmp_path):
    """Many concurrent submitters must still produce price-time-priority FIFO matching."""
    engine = await make_engine(tmp_path / "wal.jsonl")
    try:
        await asyncio.gather(*[engine.submit_limit_order("maker", Side.SELL, 100, 1) for _ in range(20)])
        results = await asyncio.gather(*[engine.submit_limit_order("taker", Side.BUY, 100, 1) for _ in range(20)])

        assert all(r["order"].status == OrderStatus.FILLED for r in results)
        assert engine.book.best_ask() is None
        assert len(engine.book.order_map) == 0
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_wal_replay_restores_identical_book_state_after_restart(tmp_path):
    wal_path = tmp_path / "wal.jsonl"

    engine1 = await make_engine(wal_path)
    await engine1.submit_limit_order("alice", Side.SELL, 100, 10)
    await engine1.submit_limit_order("alice", Side.SELL, 101, 5)
    await engine1.submit_limit_order("bob", Side.BUY, 100, 4)  # partial fill of the first sell
    resting = await engine1.submit_limit_order("carol", Side.BUY, 95, 3)  # rests, no cross
    await engine1.stop()

    snapshot_before = engine1.book.snapshot()

    # Simulate a process restart: fresh engine, same WAL file, no state carried over in memory.
    engine2 = await make_engine(wal_path)
    try:
        snapshot_after = engine2.book.snapshot()
        assert snapshot_after == snapshot_before

        assert len(engine2.book.order_map) == len(engine1.book.order_map)
        assert resting["order"].id in engine2.book.order_map
    finally:
        await engine2.stop()


@pytest.mark.asyncio
async def test_wal_replay_survives_cancellation(tmp_path):
    wal_path = tmp_path / "wal.jsonl"

    engine1 = await make_engine(wal_path)
    result = await engine1.submit_limit_order("alice", Side.BUY, 100, 10)
    await engine1.cancel_order(result["order"].id)
    await engine1.stop()

    engine2 = await make_engine(wal_path)
    try:
        assert engine2.book.best_bid() is None
        assert len(engine2.book.order_map) == 0
    finally:
        await engine2.stop()
