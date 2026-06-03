"""btc_multi_indicator_v2 极端 RSI 与放量门控."""
from __future__ import annotations

from src.core.models import SignalDirection
from src.strategies.btc_multi_indicator.intraday import BTCMultiIndicatorIntradayStrategy


def _strat() -> BTCMultiIndicatorIntradayStrategy:
    return BTCMultiIndicatorIntradayStrategy(
        signal_threshold=0.40,
        min_score_edge=0.03,
        require_5m_trend=False,
        use_1h_soft_filter=False,
    )


def test_rsi_extreme_long_only_when_oversold():
    s = _strat()
    assert s._passes_rsi_extreme_gate(SignalDirection.LONG, 14.0)
    assert not s._passes_rsi_extreme_gate(SignalDirection.LONG, 50.0)
    assert s._passes_rsi_extreme_gate(SignalDirection.SHORT, 80.0)
    assert not s._passes_rsi_extreme_gate(SignalDirection.SHORT, 70.0)


def test_vol_gate_blocks_spike_without_extreme_rsi():
    s = _strat()
    assert not s._passes_vol_gate(SignalDirection.LONG, 50.0, 2.5)
    assert s._passes_vol_gate(SignalDirection.LONG, 12.0, 2.5)
    assert s._passes_vol_gate(SignalDirection.SHORT, 78.0, 2.0)


def test_score_boosts_oversold_with_surge():
    s = _strat()
    s._prev_vol_ratio = 1.0
    primary = {
        "close": 50000,
        "ema20": 50100,
        "ema50": 49900,
        "macd_hist": -10,
        "rsi14": 12.0,
        "bb_mid": 50000,
        "bb_pct": 0.1,
        "vol_ratio": 2.5,
    }
    trend = {"ema20": 50000, "ema50": 49000, "rsi14": 50}
    long_score, short_score = s._score(primary, trend)
    assert long_score > short_score
