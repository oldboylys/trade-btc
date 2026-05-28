"""回测框架：读取历史数据，驱动策略，输出绩效报告."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from src.core.clock import SimClock, set_clock
from src.core.models import Exchange, Kline, TradingMode
from src.core.mode import ModeGuard
from src.execution.router import ExecutionRouter
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig, RiskManager
from src.sim.paper_exchange import PaperExchange
from src.strategies.base import IStrategy


@dataclass
class BacktestReport:
    symbol: str
    exchange: Exchange
    interval: str
    start_ms: int
    end_ms: int
    total_klines: int = 0
    total_signals: int = 0
    total_trades: int = 0
    realized_pnl: Decimal = Decimal("0")
    total_fee: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    final_balance: Decimal = Decimal("0")
    initial_balance: Decimal = Decimal("0")
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    @property
    def return_pct(self) -> Decimal:
        if self.initial_balance == 0:
            return Decimal("0")
        return (self.final_balance - self.initial_balance) / self.initial_balance * 100


class BacktestRunner:
    """
    回测运行器：
    1. 从 SQLite 读取历史 K线
    2. 推进模拟时钟
    3. 调用策略 on_kline
    4. 执行路由下单到 PaperExchange
    5. 汇总绩效
    """

    def __init__(
        self,
        storage: MarketDataStorage,
        strategy: IStrategy,
        initial_balance: Decimal = Decimal("100000"),
        risk_config: Optional[RiskConfig] = None,
    ) -> None:
        self.storage = storage
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.risk_config = risk_config or RiskConfig()

    async def run(
        self,
        symbol: str,
        exchange: Exchange,
        interval: str,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
    ) -> BacktestReport:
        klines = await self.storage.load_klines(
            symbol=symbol, exchange=exchange, interval=interval,
            start_ms=start_ms, end_ms=end_ms,
        )
        if not klines:
            raise ValueError("No klines found for backtest")

        # 初始化纸交易环境
        clock = SimClock(klines[0].open_time)
        set_clock(clock)

        paper_ex = PaperExchange(initial_balance=self.initial_balance)
        await paper_ex.connect()

        risk = RiskManager(self.risk_config)
        mode_guard = ModeGuard(TradingMode.PAPER)
        router = ExecutionRouter(paper_ex, risk, mode_guard)

        report = BacktestReport(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_ms=klines[0].open_time,
            end_ms=klines[-1].close_time,
            initial_balance=self.initial_balance,
            total_klines=len(klines),
        )

        peak_balance = self.initial_balance
        equity_samples: list[tuple[int, float]] = []

        for kline in klines:
            clock.set(kline.close_time)
            paper_ex.feed_kline(kline)

            target = self.strategy.on_kline(kline)
            if target is not None:
                report.total_signals += 1
                await router.execute(target)

            # 权益曲线采样（每100根K线）
            if len(equity_samples) % 100 == 0:
                bal = paper_ex.balance + paper_ex.position_book.total_unrealized_pnl()
                equity_samples.append((kline.close_time, float(bal)))
                if bal > peak_balance:
                    peak_balance = bal
                drawdown = (peak_balance - bal) / peak_balance * 100 if peak_balance > 0 else Decimal("0")
                if drawdown > report.max_drawdown:
                    report.max_drawdown = Decimal(str(drawdown))

        # 最终汇总
        report.final_balance = paper_ex.balance + paper_ex.position_book.total_unrealized_pnl()
        report.realized_pnl = paper_ex.position_book.daily_realized_pnl
        report.total_fee = paper_ex.position_book.total_fee
        report.equity_curve = equity_samples

        return report
