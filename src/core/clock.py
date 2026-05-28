"""时钟抽象：支持真实时间与回测时间."""
from __future__ import annotations

import time
from datetime import datetime, timezone


class Clock:
    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def now(self) -> datetime:
        return datetime.now(tz=timezone.utc)


class SimClock(Clock):
    """回测/模拟时钟，可手动推进时间."""

    def __init__(self, start_ms: int = 0) -> None:
        self._ts_ms = start_ms

    def set(self, ts_ms: int) -> None:
        self._ts_ms = ts_ms

    def advance(self, ms: int) -> None:
        self._ts_ms += ms

    def now_ms(self) -> int:
        return self._ts_ms

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._ts_ms / 1000, tz=timezone.utc)


_clock: Clock = Clock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> None:
    global _clock
    _clock = clock
