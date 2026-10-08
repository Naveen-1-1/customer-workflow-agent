"""In-process pub/sub for server-sent events (single-process app)."""

import asyncio
from collections import defaultdict

from fastapi.sse import ServerSentEvent

CLOSE = None  # sentinel: the stream should end (server shutting down)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[topic].add(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        self._subs[topic].discard(q)

    def publish(self, topic: str, event: ServerSentEvent) -> None:
        for q in list(self._subs.get(topic, ())):
            q.put_nowait(event)

    def publish_all(self, event: ServerSentEvent | None) -> None:
        for subs in self._subs.values():
            for q in list(subs):
                q.put_nowait(event)

    def close_all(self) -> None:
        self.publish_all(CLOSE)
