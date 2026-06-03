"""ICT 模块检测测试."""
from __future__ import annotations

from decimal import Decimal

from src.core.models import Exchange, Kline
from src.strategies.ict.fvg import FVGTracker
from src.strategies.ict.liquidity import LiquidityTracker
from src.strategies.ict.order_block import OrderBlockTracker
from src.strategies.ict.structure import StructureTracker


def _k(o: float, h: float, l: float, c: float, t: int = 0) -> Kline:
    return Kline(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        interval="5m",
        open_time=t,
        close_time=t + 299_999,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal("100"),
        quote_volume=Decimal("1"),
        num_trades=10,
        is_closed=True,
    )


def test_bull_fvg_detected():
    tracker = FVGTracker(min_gap_pct=0.0001)
    b0 = _k(100, 101, 99, 100, 0)
    b1 = _k(100, 102, 99, 101, 300_000)
    b2 = _k(103, 105, 103, 104, 600_000)
    tracker.feed(b0)
    tracker.feed(b1)
    tracker.feed(b2)
    active = [g for g in tracker.gaps if g.active and g.direction == "bull"]
    assert len(active) >= 1
    assert active[0].bottom < active[0].top


def test_order_block_on_displacement():
    ob = OrderBlockTracker(displacement_atr_mult=1.0, max_age_bars=48)
    bars = [
        _k(100, 101, 99, 99, i * 300_000) for i in range(5)
    ]
    bars[-1] = _k(99, 110, 98, 108, 5 * 300_000)
    for b in bars:
        ob.feed(b, atr=5.0)
    bull_obs = [x for x in ob.blocks if x.direction == "bull" and x.active]
    assert len(bull_obs) >= 1


def test_liquidity_sweep_bull():
    liq = LiquidityTracker(equal_threshold_pct=0.001)
    for i in range(8):
        liq.feed(_k(100, 101, 99.5, 100, i * 300_000))
    bull, bear = liq.feed(_k(100, 101, 98, 100.5, 8 * 300_000))
    assert isinstance(bull, bool)
    assert isinstance(bear, bool)


def test_structure_bias_updates():
    st = StructureTracker(swing_lookback=2)
    for i in range(20):
        c = 100 + i * 2
        st.feed(_k(c - 1, c + 1, c - 2, c, i * 300_000))
    assert st.state.bias.value in ("bullish", "bearish", "neutral")
