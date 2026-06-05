"""回测框架：多周期回放、驱动策略、输出胜率与绩效报告."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional

from src.backtest.replay import merge_klines_for_replay
from src.backtest.trade_ledger import BacktestTrade, BacktestTradeLedger, LedgerSummary
from src.core.clock import SimClock, set_clock
from src.core.models import Exchange, Kline, TradingMode
from src.core.mode import ModeGuard
from src.execution.position_helpers import register_trailing_if_needed, set_strategy_equity
from src.execution.router import ExecutionRouter
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig, RiskManager
from src.sim.paper_exchange import PaperExchange
from src.strategies.base import IStrategy


@dataclass
class BacktestReport:
    symbol: str
    exchange: Exchange
    intervals: list[str]
    start_ms: int
    end_ms: int
    total_klines: int = 0
    total_signals: int = 0
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: float = 0.0
    avg_hold_hours: float = 0.0
    exit_breakdown: dict[str, int] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0")
    total_fee: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    final_balance: Decimal = Decimal("0")
    initial_balance: Decimal = Decimal("0")
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    trades: list[BacktestTrade] = field(default_factory=list)

    @property
    def return_pct(self) -> Decimal:
        if self.initial_balance == 0:
            return Decimal("0")
        return (self.final_balance - self.initial_balance) / self.initial_balance * 100


def _warmup_indicators(strategy: IStrategy, klines: list[Kline]) -> None:
    """仅预热指标管道，不触发下单."""
    pipeline = getattr(strategy, "_pipeline", None)
    if pipeline is None:
        return
    for kline in klines:
        if kline.interval in pipeline.intervals:
            pipeline.feed(kline)


def _split_warmup_replay(
    merged: list[Kline],
    warmup_bars: int,
    primary_tf: str = "5m",
) -> tuple[list[Kline], list[Kline]]:
    """从 merged 时间线切分指标预热段与正式回测段（DB 无 start 前数据时使用）."""
    count = 0
    cutoff: int | None = None
    for k in merged:
        if k.interval == primary_tf:
            count += 1
            if count >= warmup_bars:
                cutoff = k.close_time
                break
    if cutoff is None:
        return merged, []
    warmup = [k for k in merged if k.close_time <= cutoff]
    replay = [k for k in merged if k.close_time > cutoff]
    return warmup, replay


class BacktestRunner:
    """
    回测运行器：
    1. 从 SQLite 读取多周期历史 K 线并 merge 回放
    2. 指标预热后推进模拟时钟
    3. 与纸交易 main 对齐：feed_kline → position_side → set_tp_sl → execute
    4. 汇总胜率与绩效
    """

    def __init__(
        self,
        storage: MarketDataStorage,
        strategy: IStrategy,
        initial_balance: Decimal = Decimal("100000"),
        risk_config: Optional[RiskConfig] = None,
        warmup_bars: int = 500,
        intervals: Optional[list[str]] = None,
        bar_hook: Callable[[Kline, PaperExchange], None] | None = None,
        trail_hook: Callable[[dict], None] | None = None,
        trade_close_hook: Callable[[dict], None] | None = None,
    ) -> None:
        self.storage = storage
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.risk_config = risk_config or RiskConfig()
        self.warmup_bars = warmup_bars
        self.intervals = intervals or ["5m", "1h"]
        self.bar_hook = bar_hook
        self.trail_hook = trail_hook
        self.trade_close_hook = trade_close_hook

    async def run(
        self,
        symbol: str,
        exchange: Exchange,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
    ) -> BacktestReport:
        if start_ms is None:
            raise ValueError("start_ms is required for multi-interval backtest")

        # 预热：优先 start 之前的历史；不足则从正式区间头部切 warmup_bars 根 5m
        primary_tf = getattr(self.strategy, "primary_tf", "5m")
        warmup_1h_limit = max(50, self.warmup_bars // 12 + 10)
        warmup_before = merge_klines_for_replay({
            "5m": await self.storage.load_klines_before(
                symbol, exchange, "5m", start_ms, self.warmup_bars,
            ),
            "1h": await self.storage.load_klines_before(
                symbol, exchange, "1h", start_ms, warmup_1h_limit,
            ),
        })

        klines_by_interval: dict[str, list[Kline]] = {}
        for interval in self.intervals:
            bars = await self.storage.load_klines(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            klines_by_interval[interval] = bars

        merged_all = merge_klines_for_replay(klines_by_interval)
        if not merged_all:
            raise ValueError("No klines found for backtest")

        warmup_in_range, replay_klines = _split_warmup_replay(
            merged_all, self.warmup_bars, primary_tf,
        )
        if not replay_klines:
            replay_klines = merged_all
            warmup_in_range = []

        first_ts = (
            warmup_before[0].open_time if warmup_before
            else warmup_in_range[0].open_time if warmup_in_range
            else replay_klines[0].open_time
        )
        clock = SimClock(first_ts)
        set_clock(clock)

        paper_ex = PaperExchange(initial_balance=self.initial_balance)
        await paper_ex.connect()

        ledger = BacktestTradeLedger()
        if self.trail_hook is not None:
            paper_ex.position_book.set_trail_update_hook(self.trail_hook)

        def _on_close(event: dict) -> None:
            if self.trade_close_hook is not None:
                self.trade_close_hook(event)
            on_closed = getattr(self.strategy, "on_trade_closed", None)
            if callable(on_closed):
                on_closed(event)
            ledger.on_trade_closed(event)

        paper_ex.position_book.set_trade_close_hook(_on_close)

        risk = RiskManager(self.risk_config)
        mode_guard = ModeGuard(TradingMode.PAPER)
        router = ExecutionRouter(paper_ex, risk, mode_guard)

        report = BacktestReport(
            symbol=symbol,
            exchange=exchange,
            intervals=list(self.intervals),
            start_ms=replay_klines[0].open_time,
            end_ms=replay_klines[-1].close_time,
            initial_balance=self.initial_balance,
            total_klines=len(replay_klines),
        )

        # 指标预热（不下单）
        _warmup_indicators(self.strategy, warmup_before)
        _warmup_indicators(self.strategy, warmup_in_range)

        peak_balance = self.initial_balance
        equity_samples: list[tuple[int, float]] = []
        bar_count = 0

        for kline in replay_klines:
            clock.set(kline.close_time)
            paper_ex.feed_kline(kline)
            if self.bar_hook is not None:
                self.bar_hook(kline, paper_ex)

            set_strategy_equity(self.strategy, paper_ex.total_equity())

            pos_entry = paper_ex.position_book.get_position(kline.symbol)
            current_side = pos_entry.side if pos_entry and pos_entry.qty > 0 else None

            target = self.strategy.on_kline(kline, position_side=current_side)
            if target is not None:
                report.total_signals += 1
                if target.tp_price and target.sl_price:
                    paper_ex.position_book.set_tp_sl(
                        kline.symbol,
                        float(target.tp_price),
                        float(target.sl_price or 0),
                    )
                await router.execute(target)
                register_trailing_if_needed(
                    paper_ex.position_book, self.strategy, kline.symbol,
                )

            bar_count += 1
            bal = paper_ex.total_equity()
            if bar_count % 100 == 0:
                equity_samples.append((kline.close_time, float(bal)))
            if bal > peak_balance:
                peak_balance = bal
            drawdown = (
                (peak_balance - bal) / peak_balance * 100
                if peak_balance > 0
                else Decimal("0")
            )
            if drawdown > report.max_drawdown:
                report.max_drawdown = Decimal(str(drawdown))

        summary: LedgerSummary = ledger.summary()
        report.final_balance = paper_ex.total_equity()
        report.realized_pnl = summary.total_net_pnl
        report.total_fee = paper_ex.position_book.total_fee
        report.equity_curve = equity_samples
        report.total_trades = summary.total_trades
        report.win_trades = summary.win_trades
        report.loss_trades = summary.loss_trades
        report.win_rate = summary.win_rate
        report.avg_hold_hours = summary.avg_hold_hours
        report.exit_breakdown = summary.exit_breakdown
        report.trades = summary.trades

        return report
