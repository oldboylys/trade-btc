"""策略注册表与工厂测试."""
from __future__ import annotations

import pytest

from src.strategies.factory import create_strategy, get_strategy_kind, is_enabled
from src.strategies.registry import bootstrap, get_entry, list_strategies, resolve_name


@pytest.fixture
def config():
    return {
        "strategies": {
            "btc_multi_indicator": {"enabled": True, "symbol": "BTCUSDT"},
            "volume_profile": {"enabled": False, "symbol": "BTCUSDT"},
            "ict": {"enabled": False, "symbol": "BTCUSDT"},
            "funding_arb": {"enabled": False, "min_funding_spread": "0.0002"},
        },
    }


def test_bootstrap_registers_builtin():
    bootstrap()
    names = list_strategies()
    assert "btc_multi_indicator" in names
    assert "btc_multi_indicator_v2" in names
    assert "volume_profile" in names
    assert "ict" in names
    assert "funding_arb" in names


def test_alias_resolves_btc():
    assert resolve_name("btc") == "btc_multi_indicator"
    assert resolve_name("btc_v2") == "btc_multi_indicator_v2"


def test_unknown_strategy_raises():
    bootstrap()
    with pytest.raises(KeyError, match="Unknown strategy"):
        get_entry("not_a_strategy")


def test_create_strategy_btc(config):
    bootstrap()
    strat = create_strategy("btc", config, check_enabled=True)
    assert strat.name == "btc_multi_indicator"


def test_disabled_strategy_raises(config):
    bootstrap()
    with pytest.raises(RuntimeError, match="disabled"):
        create_strategy("volume_profile", config, check_enabled=True)


def test_force_skips_enabled_check(config):
    bootstrap()
    strat = create_strategy("volume_profile", config, check_enabled=False)
    assert strat.name == "volume_profile"


def test_strategy_kinds(config):
    bootstrap()
    assert get_strategy_kind("btc_multi_indicator") == "kline"
    assert get_strategy_kind("funding_arb") == "polling"
    assert is_enabled("volume_profile", config) is False
