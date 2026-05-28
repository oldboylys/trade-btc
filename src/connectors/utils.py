"""连接器公共工具：限频、重试、签名辅助."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Any, Callable, TypeVar
from urllib.parse import urlencode

T = TypeVar("T")


def sign_hmac_sha256(secret: str, params: dict) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


class RateLimiter:
    """令牌桶限速器."""

    def __init__(self, rate: int = 1200, per_seconds: float = 60.0) -> None:
        self.rate = rate
        self.per_seconds = per_seconds
        self._tokens = float(rate)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(
                self.rate,
                self._tokens + elapsed * (self.rate / self.per_seconds),
            )
            if self._tokens < tokens:
                wait = (tokens - self._tokens) * (self.per_seconds / self.rate)
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= tokens


async def with_retry(
    coro_fn: Callable[[], Any],
    max_retry: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
) -> Any:
    last_exc = None
    for attempt in range(max_retry):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retry - 1:
                await asyncio.sleep(delay * (backoff ** attempt))
    raise last_exc
