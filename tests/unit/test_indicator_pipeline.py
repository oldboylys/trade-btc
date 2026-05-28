"""测试指标流水线（不依赖 pandas-ta 的基础版本）."""
from decimal import Decimal

import pytest

from src.core.models import Exchange, Kline
from src.indicators.pipeline import IndicatorPipeline


def make_kline(i: int, price: float = 50000.0) -> Kline:
    return Kline(
        symbol="BTCUSDT", exchange=Exchange.BINANCE, interval="5m",
        open_time=i * 300_000,
        close_time=i * 300_000 + 299_999,
        open=Decimal(str(price - 5)),
        high=Decimal(str(price + 10)),
        low=Decimal(str(price - 10)),
        close=Decimal(str(price)),
        volume=Decimal("50"),
        quote_volume=Decimal("2500000"),
        num_trades=200,
        is_closed=True,
    )


def test_pipeline_returns_features_after_warmup():
    pipeline = IndicatorPipeline(intervals=["5m"], max_bars=500)
    features = {}
    import random
    random.seed(42)
    for i in range(60):
        price = 50000.0 + random.uniform(-500, 500)
        features = pipeline.feed(make_kline(i, price))

    assert "rsi14" in features
    assert "ema20" in features
    assert "bb_upper" in features
    assert "close" in features

    rsi = features["rsi14"]
    assert 0 <= rsi <= 100


def test_pipeline_empty_before_warmup():
    pipeline = IndicatorPipeline(intervals=["5m"])
    features = pipeline.feed(make_kline(0))
    assert features == {}  # 数据不足


def test_pipeline_multiple_intervals():
    pipeline = IndicatorPipeline(intervals=["5m", "1h"], max_bars=500)
    import random
    random.seed(0)
    for i in range(60):
        price = 50000.0 + random.uniform(-200, 200)
        kline_5m = make_kline(i, price)
        pipeline.feed(kline_5m)

        kline_1h = Kline(
            symbol="BTCUSDT", exchange=Exchange.BINANCE, interval="1h",
            open_time=i * 3600_000, close_time=i * 3600_000 + 3599_999,
            open=Decimal(str(price - 5)),
            high=Decimal(str(price + 20)),
            low=Decimal(str(price - 20)),
            close=Decimal(str(price)),
            volume=Decimal("300"),
            quote_volume=Decimal("15000000"),
            num_trades=1000,
            is_closed=True,
        )
        pipeline.feed(kline_1h)

    all_feats = pipeline.get_all_features()
    assert "5m" in all_feats
    assert "1h" in all_feats
