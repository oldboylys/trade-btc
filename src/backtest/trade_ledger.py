"""回测交易账本：统计胜率、平仓原因与持仓时长."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class BacktestTrade:
    direction: str
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    fee: float
    net_pnl: float
    hold_ms: int
    close_reason: str
    open_time_ms: int
    close_time_ms: int


@dataclass
class LedgerSummary:
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: float = 0.0
    avg_hold_hours: float = 0.0
    total_net_pnl: Decimal = Decimal("0")
    exit_breakdown: dict[str, int] = field(default_factory=dict)
    trades: list[BacktestTrade] = field(default_factory=list)


class BacktestTradeLedger:
    """监听 PositionBook 完全平仓事件，汇总回合绩效."""

    def __init__(self) -> None:
        self._trades: list[BacktestTrade] = []

    def on_trade_closed(self, event: dict) -> None:
        trade = BacktestTrade(
            direction=event["direction"],
            entry_price=float(event["entry_price"]),
            exit_price=float(event["exit_price"]),
            qty=float(event["qty"]),
            gross_pnl=float(event["gross_pnl"]),
            fee=float(event["fee"]),
            net_pnl=float(event["net_pnl"]),
            hold_ms=int(event["hold_ms"]),
            close_reason=event["close_reason"],
            open_time_ms=int(event["open_time_ms"]),
            close_time_ms=int(event["close_time_ms"]),
        )
        self._trades.append(trade)

    def summary(self) -> LedgerSummary:
        total = len(self._trades)
        if total == 0:
            return LedgerSummary()

        wins = sum(1 for t in self._trades if t.net_pnl > 0)
        losses = total - wins
        total_hold_ms = sum(t.hold_ms for t in self._trades)
        breakdown: dict[str, int] = {}
        total_net = Decimal("0")
        for t in self._trades:
            breakdown[t.close_reason] = breakdown.get(t.close_reason, 0) + 1
            total_net += Decimal(str(t.net_pnl))

        return LedgerSummary(
            total_trades=total,
            win_trades=wins,
            loss_trades=losses,
            win_rate=wins / total if total else 0.0,
            avg_hold_hours=total_hold_ms / total / 3_600_000 if total else 0.0,
            total_net_pnl=total_net,
            exit_breakdown=breakdown,
            trades=list(self._trades),
        )
