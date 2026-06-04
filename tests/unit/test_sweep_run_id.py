"""run_id 稳定性测试."""
from __future__ import annotations

from src.backtest.sweep import compute_run_id, expand_grid, parse_set_args


def test_run_id_stable_for_same_params():
    params = {"signal_threshold": 0.52, "tp_pct": 0.015}
    a = compute_run_id("btc_multi_indicator_v2", "2024-06-01", "2026-06-03", params)
    b = compute_run_id("btc_multi_indicator_v2", "2024-06-01", "2026-06-03", params)
    assert a == b
    assert len(a) == 12


def test_run_id_differs_for_params():
    a = compute_run_id("btc_multi_indicator_v2", "2024-06-01", "2026-06-03", {"signal_threshold": 0.52})
    b = compute_run_id("btc_multi_indicator_v2", "2024-06-01", "2026-06-03", {"signal_threshold": 0.54})
    assert a != b


def test_expand_grid_cartesian():
    combos = expand_grid({"a": [1, 2], "b": [10]})
    assert len(combos) == 2
    assert {"a": 1, "b": 10} in combos


def test_parse_set_args():
    d = parse_set_args(["signal_threshold=0.52", "require_5m_trend=true", "x=abc"])
    assert d["signal_threshold"] == 0.52
    assert d["require_5m_trend"] is True
    assert d["x"] == "abc"
