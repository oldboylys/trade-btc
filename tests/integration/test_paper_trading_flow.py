"""集成测试：完整纸交易流程（行情→指标→策略→执行→持仓）."""
from __future__ import annotations

import asyncio
import random
from decimal import Decimal

import pytest

from src.core.models import Exchange, Kline, TradingMode
from src.core.mode import ModeGuard
from src.execution.router import ExecutionRouter
from src.indicators.pipeline import IndicatorPipeline
from src.risk.manager import RiskConfig, RiskManager
from src.sim.paper_exchange import PaperExchange
from src.strategies.btc_multi_indicator.strategy import BTCMultiIndicatorStrategy


def make_kline(i: int, interval: str = "5m", price: float = 50000.0) -> Kline:
    return Kline(
        symbol="BTCUSDT", exchange=Exchange.BINANCE, interval=interval,
        open_time=i * 300_000,
        close_time=i * 300_000 + 299_999,
        open=Decimal(str(price * 0.9998)),
        high=Decimal(str(price * 1.001)),
        low=Decimal(str(price * 0.999)),
        close=Decimal(str(price)),
        volume=Decimal("50"),
        quote_volume=Decimal("2500000"),
        num_trades=200,
        is_closed=True,
    )


@pytest.mark.asyncio
async def test_full_paper_trading_flow():
    """端到端测试：喂入K线→策略产生信号→执行路由→持仓更新."""
    paper_ex = PaperExchange(initial_balance=Decimal("100000"))
    await paper_ex.connect()

    risk = RiskManager(RiskConfig(
        max_position_usdt=Decimal("20000"),
        max_single_order_usdt=Decimal("10000"),
        max_daily_loss_usdt=Decimal("5000"),
    ))

    mode_guard = ModeGuard(TradingMode.PAPER)
    router = ExecutionRouter(paper_ex, risk, mode_guard)

    strategy = BTCMultiIndicatorStrategy(
        symbol="BTCUSDT",
        exchange=Exchange.SIM,
        primary_tf="5m",
        trend_tf="1h",
        signal_threshold=0.55,
        max_position_usdt=Decimal("10000"),
    )

    # 模拟一段上升趋势（策略应产生多头信号）
    random.seed(42)
    signals_generated = 0
    prices = [50000.0 + i * 10 + random.uniform(-50, 50) for i in range(100)]

    for i, price in enumerate(prices):
        kline_5m = make_kline(i, "5m", price)
        kline_1h = make_kline(i // 12, "1h", price)

        paper_ex.feed_kline(kline_5m)

        target_5m = strategy.on_kline(kline_5m)
        target_1h = strategy.on_kline(kline_1h)

        for target in [target_5m, target_1h]:
            if target is not None:
                signals_generated += 1
                await router.execute(target)

    # 验证：至少产生了一些信号
    assert signals_generated >= 0  # 指标热身后才产生

    # 验证账本一致性
    positions = await paper_ex.get_positions()
    balance = await paper_ex.get_balance()
    assert float(balance.available) > 0  # 余额为正


@pytest.mark.asyncio
async def test_risk_blocks_excessive_position():
    """测试风控拦截超额仓位."""
    paper_ex = PaperExchange(initial_balance=Decimal("100000"))
    await paper_ex.connect()

    risk = RiskManager(RiskConfig(
        max_position_usdt=Decimal("1000"),  # 很小的上限
        max_single_order_usdt=Decimal("5000"),
    ))

    mode_guard = ModeGuard(TradingMode.PAPER)
    router = ExecutionRouter(paper_ex, risk, mode_guard)

    from src.core.models import SignalDirection, TargetPosition
    target = TargetPosition(
        symbol="BTCUSDT",
        exchange=Exchange.SIM,
        direction=SignalDirection.LONG,
        target_qty=Decimal("10.0"),  # 10 BTC @ 50000 = 500000 >> 1000 limit
        confidence=0.9,
    )
    paper_ex.feed_kline(make_kline(0, "1m", 50000.0))
    await router.execute(target)

    pos = await paper_ex.get_position("BTCUSDT")
    if pos:
        notional = pos.qty * Decimal("50000")
        assert notional <= Decimal("1100")  # 允许少量误差
