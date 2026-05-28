"""K线聚合器：将 tick/1m K线 聚合到多周期."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Callable

from src.core.models import Exchange, Kline

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


def _floor_ts(ts_ms: int, interval_ms: int) -> int:
    return (ts_ms // interval_ms) * interval_ms


class KlineAggregator:
    """将原始 K线（通常1m）在内存中聚合到目标周期."""

    def __init__(
        self,
        symbol: str,
        exchange: Exchange,
        target_intervals: list[str],
        on_kline_closed: Callable[[Kline], None] | None = None,
    ) -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.target_intervals = target_intervals
        self.on_kline_closed = on_kline_closed

        # interval -> 当前未完成 K线 dict
        self._current: dict[str, dict] = {}

    def feed(self, kline: Kline) -> list[Kline]:
        """喂入一根 K线（一般是1m闭合K），返回所有因此而闭合的目标周期K线."""
        closed: list[Kline] = []

        for interval in self.target_intervals:
            interval_ms = INTERVAL_MS.get(interval)
            if interval_ms is None:
                continue

            bar_open = _floor_ts(kline.open_time, interval_ms)
            bar_close = bar_open + interval_ms - 1

            cur = self._current.get(interval)
            if cur is None or cur["open_time"] != bar_open:
                # 新的 bar 开始，先把旧的 bar 关掉
                if cur is not None:
                    bar = self._to_kline(cur, interval, is_closed=True)
                    closed.append(bar)
                    if self.on_kline_closed:
                        self.on_kline_closed(bar)
                self._current[interval] = {
                    "open_time": bar_open,
                    "close_time": bar_close,
                    "open": kline.open,
                    "high": kline.high,
                    "low": kline.low,
                    "close": kline.close,
                    "volume": kline.volume,
                    "quote_volume": kline.quote_volume,
                    "num_trades": kline.num_trades,
                }
            else:
                cur["high"] = max(cur["high"], kline.high)
                cur["low"] = min(cur["low"], kline.low)
                cur["close"] = kline.close
                cur["volume"] += kline.volume
                cur["quote_volume"] += kline.quote_volume
                cur["num_trades"] += kline.num_trades

        return closed

    def current_bar(self, interval: str) -> Kline | None:
        cur = self._current.get(interval)
        if cur is None:
            return None
        return self._to_kline(cur, interval, is_closed=False)

    def _to_kline(self, cur: dict, interval: str, is_closed: bool) -> Kline:
        return Kline(
            symbol=self.symbol,
            exchange=self.exchange,
            interval=interval,
            open_time=cur["open_time"],
            close_time=cur["close_time"],
            open=cur["open"],
            high=cur["high"],
            low=cur["low"],
            close=cur["close"],
            volume=cur["volume"],
            quote_volume=cur["quote_volume"],
            num_trades=cur["num_trades"],
            is_closed=is_closed,
        )
