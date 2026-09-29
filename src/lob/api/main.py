"""FastAPI REST + WebSocket gateway.

An app *factory* (`create_app`) is used instead of a bare module-level
`app` so tests can spin up isolated instances pointed at a temp WAL file
without import-time global state leaking between tests. `uvicorn` still
gets a ready-made module-level `app` for `uvicorn lob.api.main:app`.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from ..config import settings
from ..matching_engine import MatchingEngine
from ..models import Side
from ..pubsub import PubSub
from ..wal import WriteAheadLog
from .schemas import CancelResponse, L2Level, NewOrderRequest, OrderResponse, SnapshotResponse, TradeFill

router = APIRouter()


def get_engine(request: Request) -> MatchingEngine:
    return request.app.state.engine


def get_pubsub(request: Request) -> PubSub:
    return request.app.state.pubsub


def get_pubsub_ws(websocket: WebSocket) -> PubSub:
    # WebSocket scope has no `Request`; FastAPI needs a WebSocket-typed
    # dependency to resolve app state on this path.
    return websocket.app.state.pubsub


@router.post("/api/v1/orders", response_model=OrderResponse, status_code=201)
async def submit_order(req: NewOrderRequest, engine: MatchingEngine = Depends(get_engine)) -> OrderResponse:
    side = Side(req.side)
    if req.order_type == "LIMIT":
        if req.price is None:
            raise HTTPException(status_code=422, detail="price is required for LIMIT orders")
        result = await engine.submit_limit_order(req.trader_id, side, req.price, req.qty)
    else:
        result = await engine.submit_market_order(req.trader_id, side, req.qty)

    order = result["order"]
    trades = result["trades"]
    return OrderResponse(
        order_id=order.id,
        status=order.status.value,
        filled_qty=order.qty - order.remaining_qty,
        remaining_qty=order.remaining_qty,
        trades=[TradeFill(price=t.price, qty=t.qty, maker_id=t.maker_id, taker_id=t.taker_id) for t in trades],
    )


@router.delete("/api/v1/orders/{order_id}", response_model=CancelResponse)
async def cancel_order(order_id: str, engine: MatchingEngine = Depends(get_engine)) -> CancelResponse:
    result = await engine.cancel_order(order_id)
    return CancelResponse(order_id=order_id, cancelled=result["cancelled"] is not None)


@router.get("/api/v1/orderbook/snapshot", response_model=SnapshotResponse)
async def get_snapshot(engine: MatchingEngine = Depends(get_engine)) -> SnapshotResponse:
    snap = engine.book.snapshot(depth=20)
    return SnapshotResponse(
        bids=[L2Level(price=p, qty=q) for p, q in snap["bids"]],
        asks=[L2Level(price=p, qty=q) for p, q in snap["asks"]],
    )


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, pubsub: PubSub = Depends(get_pubsub_ws)) -> None:
    await websocket.accept()
    queue = pubsub.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        pass
    finally:
        pubsub.unsubscribe(queue)


def create_app(wal_path: Optional[str] = None) -> FastAPI:
    resolved_wal_path = wal_path or settings.wal_path

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        wal = WriteAheadLog(resolved_wal_path)
        pubsub = PubSub()
        engine = MatchingEngine(wal, pubsub)
        engine.replay()
        engine.start()
        app.state.engine = engine
        app.state.pubsub = pubsub
        yield
        await engine.stop()

    app = FastAPI(
        title="LOB Matching Engine",
        version="0.1.0",
        description="High-throughput limit order book with price-time priority matching, WAL crash recovery, and real-time trade/depth streaming.",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
