"""Minimal in-memory Pub/Sub fanout for broadcasting engine events to WebSocket clients."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Set


class PubSub:
    def __init__(self, maxsize: int = 1000) -> None:
        self._subscribers: Set["asyncio.Queue[Dict[str, Any]]"] = set()
        self._maxsize = maxsize

    def subscribe(self) -> "asyncio.Queue[Dict[str, Any]]":
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        self._subscribers.discard(queue)

    def publish(self, event: Dict[str, Any]) -> None:
        """Fan out an event to every subscriber. Never blocks the hot path.

        A slow consumer whose queue is full has its oldest event dropped
        rather than backpressuring the matching engine -- a laggy WebSocket
        client must never be able to stall order processing.
        """
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass
