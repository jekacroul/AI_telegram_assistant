from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subscribers.discard(q)

    async def publish(self, event_type: str, data: dict) -> None:
        payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def stream(self, q: asyncio.Queue[str]) -> AsyncIterator[str]:
        try:
            while True:
                item = await q.get()
                yield item
        finally:
            self.unsubscribe(q)


message_bus = EventBus()
training_bus = EventBus()
replication_bus = EventBus()
