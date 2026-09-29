from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class NewOrderRequest(BaseModel):
    trader_id: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    price: Optional[int] = Field(default=None, description="Integer ticks/cents. Required for LIMIT orders.")
    qty: int = Field(gt=0)


class TradeFill(BaseModel):
    price: int
    qty: int
    maker_id: str
    taker_id: str


class OrderResponse(BaseModel):
    order_id: str
    status: str
    filled_qty: int
    remaining_qty: int
    trades: List[TradeFill]


class CancelResponse(BaseModel):
    order_id: str
    cancelled: bool


class L2Level(BaseModel):
    price: int
    qty: int


class SnapshotResponse(BaseModel):
    bids: List[L2Level]
    asks: List[L2Level]
