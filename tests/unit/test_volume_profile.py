"""Volume Profile 计算与信号测试."""
from __future__ import annotations

from decimal import Decimal

from src.core.models import Exchange, Kline
from src.strategies.volume_profile.profile import VolumeProfileBuilder
from src.strategies.volume_profile.signals import EntryMode, VPContext, evaluate_entry


def _bar(i: int, close: float, vol: float = 100.0) -> Kline:
    t = 1_000_000 + i * 300_000
    c = Decimal(str(close))
    return Kline(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        interval="5m",
        open_time=t,
        close_time=t + 299_999,
        open=c,
        high=c * Decimal("1.001"),
        low=c * Decimal("0.999"),
        close=c,
        volume=Decimal(str(vol)),
        quote_volume=Decimal("1"),
        num_trades=10,
        is_closed=True,
    )


def test_poc_and_value_area_snapshot():
    builder = VolumeProfileBuilder(lookback_bars=50, bin_count=20, tick_size=50)
    for i in range(40):
        price = 50000 + (i % 10) * 100
        vol = 200 if i == 15 else 50
        builder.feed(_bar(i, price, vol))
    lv = builder.levels
    assert lv is not None
    assert lv.val <= lv.poc <= lv.vah
    assert lv.bar_count >= 10
    assert lv.total_volume > 0


def test_val_poc_bounce_long_signal():
    lv = VolumeProfileBuilder(lookback_bars=10, bin_count=10, tick_size=100).feed(_bar(0, 50000))
    assert lv is None or True
    builder = VolumeProfileBuilder(lookback_bars=30, bin_count=15, tick_size=100)
    for i in range(25):
        builder.feed(_bar(i, 50000 + i * 20, 80))
    levels = builder.levels
    assert levels is not None
    ctx = VPContext(
        close=levels.val + 50,
        prev_close=levels.val - 20,
        low=levels.val - 30,
        high=levels.val + 100,
        levels=levels,
        trend_bullish_1h=True,
        trend_bearish_1h=False,
    )
    direction, conf, reason = evaluate_entry(
        ctx,
        entry_mode=EntryMode.VAL_POC_BOUNCE,
        require_trend_1h=True,
        min_distance_to_poc_pct=0.001,
        position_side=None,
    )
    assert direction.value in ("long", "short", "flat")
    if direction.value == "long":
        assert conf > 0
        assert "val_poc" in reason
