"""测试资金费率套利信号生成."""
from decimal import Decimal

import pytest

from src.core.models import Exchange, FundingRate
from src.strategies.funding_arb.collector import FundingRateSnapshot
from src.strategies.funding_arb.signal import ArbSignalGenerator


@pytest.fixture
def snapshot():
    s = FundingRateSnapshot()
    s.update(FundingRate(
        symbol="BTCUSDT", exchange=Exchange.BINANCE,
        rate=Decimal("0.001"),  # 高
        mark_price=Decimal("50000"), ts_ms=0,
    ))
    s.update(FundingRate(
        symbol="BTCUSDT", exchange=Exchange.HYPERLIQUID,
        rate=Decimal("0.0002"),  # 低
        mark_price=Decimal("50000"), ts_ms=0,
    ))
    return s


def test_signal_generated_above_threshold(snapshot):
    gen = ArbSignalGenerator(min_spread=Decimal("0.0002"))
    signal = gen.generate(snapshot, "BTCUSDT")
    assert signal is not None
    assert signal.short_exchange == Exchange.BINANCE   # 高费率做空
    assert signal.long_exchange == Exchange.HYPERLIQUID  # 低费率做多
    assert signal.spread == Decimal("0.0008")  # 0.001 - 0.0002
    assert signal.is_valid


def test_no_signal_below_threshold(snapshot):
    gen = ArbSignalGenerator(
        min_spread=Decimal("0.002"),
        estimated_fee_pct=Decimal("0.0008"),
        estimated_slippage_pct=Decimal("0.001"),
    )
    signal = gen.generate(snapshot, "BTCUSDT")
    assert signal is None  # spread(0.0008) < min(0.002) + costs


def test_no_signal_single_exchange():
    s = FundingRateSnapshot()
    s.update(FundingRate(
        symbol="BTCUSDT", exchange=Exchange.BINANCE,
        rate=Decimal("0.001"), ts_ms=0,
    ))
    gen = ArbSignalGenerator()
    signal = gen.generate(s, "BTCUSDT")
    assert signal is None


def test_snapshot_max_spread():
    s = FundingRateSnapshot()
    s.update(FundingRate("BTCUSDT", Exchange.BINANCE, Decimal("0.003"), ts_ms=0))
    s.update(FundingRate("BTCUSDT", Exchange.HYPERLIQUID, Decimal("0.001"), ts_ms=0))
    s.update(FundingRate("BTCUSDT", Exchange.ASTER, Decimal("-0.0005"), ts_ms=0))

    high, low, spread = s.max_spread("BTCUSDT")
    assert high.exchange == Exchange.BINANCE
    assert low.exchange == Exchange.ASTER
    assert spread == Decimal("0.0035")
