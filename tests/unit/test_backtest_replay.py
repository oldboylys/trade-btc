"""回测 merge 回放与胜率统计测试."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.backtest.replay import merge_klines_for_replay
from src.backtest.trade_ledger import BacktestTradeLedger
from src.backtest.runner import BacktestRunner
from src.core.models import Exchange, Kline
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig
from src.strategies.btc_multi_indicator.strategy import BTCMultiIndicatorStrategy


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
        volume=Decimal("10"),
        quote_volume=Decimal("500000"),
        num_trades=100,
        is_closed=True,
    )


def test_merge_klines_same_close_time_1h_before_5m():
    """同 close_time 时 1h 应排在 5m 之前."""
    ts = 1_000_000
    k5 = _kline("5m", ts - 300_000)
    k5.close_time = ts
    k1 = _kline("1h", ts - 3_600_000)
    k1.close_time = ts

    merged = merge_klines_for_replay({"5m": [k5], "1h": [k1]})
    assert len(merged) == 2
    assert merged[0].interval == "1h"
    assert merged[1].interval == "5m"


def test_trade_ledger_win_rate():
    ledger = BacktestTradeLedger()
    base = {
        "direction": "LONG",
        "qty": 0.1,
        "entry_price": 50000.0,
        "exit_price": 51000.0,
        "gross_pnl": 100.0,
        "fee": 1.0,
        "open_time_ms": 0,
        "close_time_ms": 3_600_000,
        "hold_ms": 3_600_000,
    }
    ledger.on_trade_closed({**base, "net_pnl": 99.0, "close_reason": "止盈"})
    ledger.on_trade_closed({
        **base,
        "net_pnl": -50.0,
        "close_reason": "止损",
        "exit_price": 49500.0,
    })

    s = ledger.summary()
    assert s.total_trades == 2
    assert s.win_trades == 1
    assert s.loss_trades == 1
    assert s.win_rate == 0.5
    assert s.exit_breakdown["止盈"] == 1
    assert s.exit_breakdown["止损"] == 1
    assert s.avg_hold_hours == 1.0


@pytest.mark.asyncio
async def test_backtest_runner_with_synthetic_data(tmp_path):
    """集成：写入样本 K 线 → 跑通回测 → 报告字段可用."""
    db_path = str(tmp_path / "test.db")
    storage = MarketDataStorage(db_path)
    await storage.connect()

    base_ms = 1_700_000_000_000
    warmup_5m = [_kline("5m", base_ms + i * 300_000, str(50000 + i)) for i in range(-520, 0)]
    warmup_1h = [_kline("1h", base_ms + i * 3_600_000, str(50000 + i * 10)) for i in range(-60, 0)]
    replay_5m = [_kline("5m", base_ms + i * 300_000, str(50100 + i * 5)) for i in range(200)]
    replay_1h = [_kline("1h", base_ms + i * 3_600_000, str(50100 + i * 50)) for i in range(20)]

    await storage.save_klines_bulk(warmup_5m + warmup_1h + replay_5m + replay_1h)

    strategy = BTCMultiIndicatorStrategy(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        signal_threshold=0.99,
        reversal_threshold=0.99,
    )
    runner = BacktestRunner(
        storage=storage,
        strategy=strategy,
        risk_config=RiskConfig(
            max_position_usdt=Decimal("20000"),
            max_single_order_usdt=Decimal("5000"),
        ),
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
    assert isinstance(report.exit_breakdown, dict)
    await storage.close()
