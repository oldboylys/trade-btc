"""测试K线聚合器."""
from decimal import Decimal

import pytest

from src.core.models import Exchange, Kline
from src.marketdata.aggregator import KlineAggregator


def make_kline(open_time: int, close_val: float = 50000.0, interval: str = "1m") -> Kline:
    return Kline(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        interval=interval,
        open_time=open_time,
        close_time=open_time + 59999,
        open=Decimal(str(close_val - 10)),
        high=Decimal(str(close_val + 20)),
        low=Decimal(str(close_val - 20)),
        close=Decimal(str(close_val)),
        volume=Decimal("10"),
        quote_volume=Decimal("500000"),
        num_trades=100,
        is_closed=True,
    )


def test_aggregator_closes_5m_bar():
    closed_bars = []
    agg = KlineAggregator(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        target_intervals=["5m"],
        on_kline_closed=closed_bars.append,
    )

    base = 1_700_000_000_000
    interval_1m = 60_000

    # 喂入 5 根 1m K线，第 6 根应触发 5m 闭合
    for i in range(5):
        agg.feed(make_kline(base + i * interval_1m))

    # 进入新的 5m 周期
    new_base = base + 5 * 60_000  # 下一个 5m 周期
    agg.feed(make_kline(new_base))

    assert len(closed_bars) == 1
    bar_5m = closed_bars[0]
    assert bar_5m.interval == "5m"
    assert bar_5m.is_closed is True
    assert bar_5m.volume == Decimal("50")  # 5 * 10


def test_aggregator_high_low():
    agg = KlineAggregator(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        target_intervals=["5m"],
    )
    base = 1_700_000_000_000
    prices = [49900, 50100, 49800, 50200, 50050]
    for i, p in enumerate(prices):
        agg.feed(make_kline(base + i * 60_000, close_val=p))

    bar = agg.current_bar("5m")
    assert bar is not None
    assert bar.high == max(Decimal(str(p + 20)) for p in prices)
    assert bar.low == min(Decimal(str(p - 20)) for p in prices)
