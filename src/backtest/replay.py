"""多周期 K 线合并回放工具."""
from __future__ import annotations

from src.core.models import Kline

# 同 close_time 时较长周期优先（1h 先于 5m 更新趋势特征）
_INTERVAL_PRIORITY: dict[str, int] = {
    "1d": 0,
    "4h": 1,
    "1h": 2,
    "15m": 3,
    "5m": 4,
    "1m": 5,
}


def merge_klines_for_replay(klines_by_interval: dict[str, list[Kline]]) -> list[Kline]:
    """按 close_time 升序合并多周期 K 线；同 timestamp 时较长周期先处理."""
    merged: list[Kline] = []
    for klines in klines_by_interval.values():
        merged.extend(klines)
    merged.sort(key=lambda k: (k.close_time, _INTERVAL_PRIORITY.get(k.interval, 99)))
    return merged
