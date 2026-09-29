import pytest
from httpx import ASGITransport, AsyncClient

from lob.api.main import create_app


@pytest.fixture
async def client(tmp_path):
    app = create_app(wal_path=str(tmp_path / "wal.jsonl"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            yield ac


@pytest.mark.asyncio
async def test_submit_limit_order_rests_when_no_cross(client):
    resp = await client.post(
        "/api/v1/orders",
        json={"trader_id": "alice", "side": "BUY", "order_type": "LIMIT", "price": 100, "qty": 10},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "OPEN"
    assert body["remaining_qty"] == 10
    assert body["trades"] == []


@pytest.mark.asyncio
async def test_submit_limit_orders_match_and_report_trade(client):
    await client.post(
        "/api/v1/orders",
        json={"trader_id": "alice", "side": "SELL", "order_type": "LIMIT", "price": 100, "qty": 10},
    )
    resp = await client.post(
        "/api/v1/orders",
        json={"trader_id": "bob", "side": "BUY", "order_type": "LIMIT", "price": 100, "qty": 10},
    )
    body = resp.json()
    assert body["status"] == "FILLED"
    assert len(body["trades"]) == 1
    assert body["trades"][0] == {"price": 100, "qty": 10, "maker_id": "alice", "taker_id": "bob"}


@pytest.mark.asyncio
async def test_limit_order_missing_price_is_rejected(client):
    resp = await client.post(
        "/api/v1/orders",
        json={"trader_id": "alice", "side": "BUY", "order_type": "LIMIT", "qty": 10},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_cancel_order(client):
    submit = await client.post(
        "/api/v1/orders",
        json={"trader_id": "alice", "side": "BUY", "order_type": "LIMIT", "price": 100, "qty": 10},
    )
    order_id = submit.json()["order_id"]

    resp = await client.delete(f"/api/v1/orders/{order_id}")
    assert resp.status_code == 200
    assert resp.json() == {"order_id": order_id, "cancelled": True}


@pytest.mark.asyncio
async def test_cancel_unknown_order_returns_false(client):
    resp = await client.delete("/api/v1/orders/does-not-exist")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is False


@pytest.mark.asyncio
async def test_snapshot_reflects_resting_depth(client):
    await client.post(
        "/api/v1/orders",
        json={"trader_id": "alice", "side": "BUY", "order_type": "LIMIT", "price": 99, "qty": 5},
    )
    await client.post(
        "/api/v1/orders",
        json={"trader_id": "bob", "side": "SELL", "order_type": "LIMIT", "price": 101, "qty": 7},
    )

    resp = await client.get("/api/v1/orderbook/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bids"] == [{"price": 99, "qty": 5}]
    assert body["asks"] == [{"price": 101, "qty": 7}]


@pytest.mark.asyncio
async def test_market_order_fills_against_resting_liquidity(client):
    await client.post(
        "/api/v1/orders",
        json={"trader_id": "alice", "side": "SELL", "order_type": "LIMIT", "price": 100, "qty": 5},
    )
    resp = await client.post(
        "/api/v1/orders",
        json={"trader_id": "bob", "side": "BUY", "order_type": "MARKET", "qty": 5},
    )
    body = resp.json()
    assert body["status"] == "FILLED"
    assert body["trades"][0]["price"] == 100
