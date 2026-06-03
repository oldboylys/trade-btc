"""多策略回测集成：合成数据跑通 BacktestRunner."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.backtest.runner import BacktestRunner
from src.core.models import Exchange, Kline
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig
from src.strategies.factory import create_strategy
from src.strategies.registry import bootstrap


def _kline(interval: str, open_time: int, close: str = "50000") -> Kline:
    step = {"5m": 300_000, "1h": 3_600_000}[interval]
    return Kline(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        interval=interval,
        open_time=open_time,
        close_time=open_time + step - 1,
        open=Decimal(close),
        high=Decimal(str(float(close) * 1.001)),
        low=Decimal(str(float(close) * 0.999)),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("500000"),
        num_trades=100,
        is_closed=True,
    )


@pytest.fixture
def base_config():
    return {
        "strategies": {
            "btc_multi_indicator": {
                "enabled": True,
                "symbol": "BTCUSDT",
                "signal_threshold": 0.99,
                "reversal_threshold": 0.99,
            },
            "volume_profile": {
                "enabled": True,
                "symbol": "BTCUSDT",
                "lookback_bars": 30,
            },
            "ict": {
                "enabled": True,
                "symbol": "BTCUSDT",
                "signal_threshold": 0.99,
            },
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_name", ["btc_multi_indicator", "volume_profile", "ict"])
async def test_backtest_runner_per_strategy(tmp_path, base_config, strategy_name):
    bootstrap()
    db_path = str(tmp_path / f"{strategy_name}.db")
    storage = MarketDataStorage(db_path)
    await storage.connect()

    base_ms = 1_700_000_000_000
    warmup_5m = [_kline("5m", base_ms + i * 300_000, str(50000 + i)) for i in range(-520, 0)]
    warmup_1h = [_kline("1h", base_ms + i * 3_600_000, str(50000 + i * 10)) for i in range(-60, 0)]
    replay_5m = [_kline("5m", base_ms + i * 300_000, str(50100 + i * 5)) for i in range(200)]
    replay_1h = [_kline("1h", base_ms + i * 3_600_000, str(50100 + i * 50)) for i in range(20)]

    await storage.save_klines_bulk(warmup_5m + warmup_1h + replay_5m + replay_1h)

    strategy = create_strategy(strategy_name, base_config, check_enabled=False)
    runner = BacktestRunner(
        storage=storage,
        strategy=strategy,
        risk_config=RiskConfig(max_position_usdt=Decimal("20000")),
        warmup_bars=500,
        intervals=["5m", "1h"],
    )

    report = await runner.run(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        start_ms=base_ms,
    )

    assert report.total_klines == 220
    assert report.win_rate >= 0.0
    await storage.close()
