"""资金费率套利策略：采集→信号→执行→风控→再平衡."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Optional

from src.connectors.base import IExchange
from src.core.logging import get_logger
from src.strategies.funding_arb.collector import FundingRateCollector, FundingRateSnapshot
from src.strategies.funding_arb.executor import ArbPosition, FundingArbExecutor
from src.strategies.funding_arb.signal import ArbSignal, ArbSignalGenerator

logger = get_logger("strategy.funding_arb")


class FundingArbStrategy:
    """
    资金费率套利策略主类。
    调度：轮询资金费率 → 生成信号 → 风控检查 → 执行双腿 → 监控仓位 → 结算后平仓
    """

    def __init__(
        self,
        symbols: list[str],
        min_spread: Decimal = Decimal("0.0002"),
        max_position_usdt: Decimal = Decimal("5000"),
        max_total_positions: int = 3,
        poll_interval_s: float = 30.0,
        auto_close_after_funding: bool = True,
    ) -> None:
        self.symbols = symbols
        self.min_spread = min_spread
        self.max_position_usdt = max_position_usdt
        self.max_total_positions = max_total_positions
        self.poll_interval_s = poll_interval_s
        self.auto_close_after_funding = auto_close_after_funding

        self.snapshot = FundingRateSnapshot()
        self.collector = FundingRateCollector(self.snapshot, poll_interval=poll_interval_s)
        self.signal_gen = ArbSignalGenerator(min_spread=min_spread)
        self._executor: Optional[FundingArbExecutor] = None
        self._running = False

    def register_exchange(self, name: str, exchange: IExchange) -> None:
        self.collector.register(name, exchange, self.symbols)

    def set_executor(self, executor: FundingArbExecutor) -> None:
        self._executor = executor

    async def start(self) -> None:
        self._running = True
        await self.collector.start()
        asyncio.create_task(self._main_loop())
        logger.info("funding_arb_strategy_started", symbols=self.symbols)

    async def stop(self) -> None:
        self._running = False
        await self.collector.stop()

    async def _main_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.poll_interval_s)
            await self._check_and_trade()

    async def _check_and_trade(self) -> None:
        if self._executor is None:
            return

        # 检查当前开仓数量
        open_count = len(self._executor.open_positions)
        if open_count >= self.max_total_positions:
            return

        for symbol in self.symbols:
            signal = self.signal_gen.generate(self.snapshot, symbol)
            if signal is None:
                continue

            # 检查是否已对该 symbol 持仓
            existing = [
                p for p in self._executor.open_positions
                if p.signal.symbol == symbol
            ]
            if existing:
                continue

            logger.info(
                "arb_signal_found",
                symbol=symbol,
                spread=float(signal.spread),
                expected_pnl=float(signal.expected_pnl_pct),
                reason=signal.reason,
            )

            await self._executor.open_position(signal)

    async def rebalance_after_funding(self) -> None:
        """资金费率结算后，检查是否应该平仓."""
        if self._executor is None:
            return
        for pos in list(self._executor.open_positions):
            signal = self.signal_gen.generate(self.snapshot, pos.signal.symbol)
            if signal is None or signal.expected_pnl_pct < self.min_spread / 2:
                logger.info("arb_rebalance_close", symbol=pos.signal.symbol)
                await self._executor.close_position(pos)
