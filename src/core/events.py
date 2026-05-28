"""异步事件总线：发布/订阅模式，支持多订阅者、类型过滤."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")
Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._running = False

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event_type: str, payload: Any) -> None:
        await self._queue.put((event_type, payload))

    def publish_nowait(self, event_type: str, payload: Any) -> None:
        self._queue.put_nowait((event_type, payload))

    async def _dispatch(self, event_type: str, payload: Any) -> None:
        for handler in self._handlers.get(event_type, []):
            try:
                await handler(payload)
            except Exception as exc:  # noqa: BLE001
                from src.core.logging import get_logger
                get_logger("event_bus").error(
                    "handler_error", event=event_type, error=str(exc)
                )

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                event_type, payload = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                await self._dispatch(event_type, payload)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._running = False


# 全局单例
_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
