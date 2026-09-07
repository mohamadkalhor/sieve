"""`GET /v1/events` — server-sent events for `pull`, `ranking`, `decision`, `apply`.

An in-process fan-out: every subscriber gets its own bounded queue, and a slow
reader drops events rather than holding the publisher up.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

EventKind = Literal["pull", "ranking", "decision", "apply", "ping"]

QUEUE_SIZE = 64


class Events:
    """The bus. One per application."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    def publish(self, kind: EventKind, data: dict[str, Any]) -> None:
        payload = json.dumps({"at": datetime.now(UTC).isoformat(), **data})
        frame = f"event: {kind}\ndata: {payload}\n\n"
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                continue

    async def stream(self, *, ping_seconds: float = 20.0) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield "event: ping\ndata: {}\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=ping_seconds)
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self._subscribers.discard(queue)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)


events = Events()
