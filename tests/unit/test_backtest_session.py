"""回测 session 配置合并与工厂构造."""
from __future__ import annotations

import pytest

from src.backtest.session import merge_strategy_config
from src.strategies.factory import create_strategy
from src.strategies.registry import bootstrap


@pytest.fixture
def base_config():
    return {
        "strategies": {
            "btc_multi_indicator_v2": {
                "enabled": False,
                "symbol": "BTCUSDT",
                "signal_threshold": 0.52,
                "tp_pct": 0.015,
            },
        },
        "risk": {"max_single_order_usdt": 5000},
    }


def test_merge_strategy_overrides(base_config):
    merged = merge_strategy_config(
        base_config,
        "btc_multi_indicator_v2",
        {"signal_threshold": 0.48, "vol_spike_ratio": 2.0},
        {"risk": {"max_single_order_usdt": 10000}},
    )
    v2 = merged["strategies"]["btc_multi_indicator_v2"]
    assert v2["signal_threshold"] == 0.48
    assert v2["vol_spike_ratio"] == 2.0
    assert merged["risk"]["max_single_order_usdt"] == 10000


def test_create_strategy_from_merged_config(base_config):
    bootstrap()
    merged = merge_strategy_config(
        base_config,
        "btc_multi_indicator_v2",
        {"signal_threshold": 0.55},
    )
    strat = create_strategy("btc_multi_indicator_v2", merged, check_enabled=False)
    assert strat.signal_threshold == 0.55
    assert strat.name == "btc_multi_indicator_v2"
