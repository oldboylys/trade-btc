"""内存状态存储：trader 进程实时写入，Web 接口读取."""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class TradeRecord:
    id: int
    direction: str          # LONG / SHORT
    qty: float
    open_time: str
    open_price: float
    close_time: str
    close_price: float
    tp: float
    sl: float
    gross_pnl: float
    fee: float
    net_pnl: float
    close_reason: str       # 止盈 / 止损 / 信号反转


@dataclass
class StatusStore:
    # 系统信息
    mode: str = "paper"
    strategy: str = "btc_multi_indicator"
    started_at: str = ""
    symbol: str = "BTCUSDT"

    # 行情
    mark_price: float = 0.0
    price_updated_at: str = ""

    # 账户
    balance: float = 100000.0
    unrealized_pnl: float = 0.0
    daily_realized_pnl: float = 0.0
    total_fee: float = 0.0

    # 持仓
    has_position: bool = False
    pos_side: str = ""
    pos_qty: float = 0.0
    pos_entry_price: float = 0.0
    pos_mark_price: float = 0.0
    pos_upnl: float = 0.0
    pos_tp: float = 0.0
    pos_sl: float = 0.0

    # 风控
    circuit_breaker: bool = False
    daily_loss_limit: float = 1000.0
    max_position_usdt: float = 10000.0

    # 成交记录（最近50笔）
    trades: list = field(default_factory=list)
    _trade_counter: int = 0

    def add_trade(
        self,
        direction: str,
        qty: float,
        open_time: str,
        open_price: float,
        close_time: str,
        close_price: float,
        tp: float,
        sl: float,
        gross_pnl: float,
        fee: float,
        net_pnl: float,
        close_reason: str,
    ) -> None:
        self._trade_counter += 1
        record = {
            "id": self._trade_counter,
            "direction": direction,
            "qty": round(qty, 4),
            "open_time": open_time,
            "open_price": open_price,
            "close_time": close_time,
            "close_price": close_price,
            "tp": round(tp, 2),
            "sl": round(sl, 2),
            "gross_pnl": round(gross_pnl, 2),
            "fee": round(fee, 2),
            "net_pnl": round(net_pnl, 2),
            "close_reason": close_reason,
            "win": net_pnl >= 0,
        }
        self.trades.insert(0, record)
        if len(self.trades) > 50:
            self.trades.pop()

    def to_dict(self) -> dict:
        uptime = ""
        if self.started_at:
            try:
                start = datetime.datetime.fromisoformat(self.started_at)
                delta = datetime.datetime.now() - start
                h, rem = divmod(int(delta.total_seconds()), 3600)
                m, s = divmod(rem, 60)
                uptime = f"{h:02d}:{m:02d}:{s:02d}"
            except Exception:
                uptime = ""

        win_trades = [t for t in self.trades if t["win"]]
        win_rate = round(len(win_trades) / len(self.trades) * 100, 1) if self.trades else 0.0

        return {
            "system": {
                "mode": self.mode,
                "strategy": self.strategy,
                "started_at": self.started_at,
                "uptime": uptime,
                "symbol": self.symbol,
            },
            "market": {
                "price": self.mark_price,
                "updated_at": self.price_updated_at,
            },
            "account": {
                "balance": round(self.balance, 2),
                "equity": round(self.balance + self.unrealized_pnl, 2),
                "unrealized_pnl": round(self.unrealized_pnl, 2),
                "daily_realized_pnl": round(self.daily_realized_pnl, 2),
                "total_fee": round(self.total_fee, 2),
            },
            "position": {
                "active": self.has_position,
                "side": self.pos_side,
                "qty": self.pos_qty,
                "entry_price": self.pos_entry_price,
                "mark_price": self.pos_mark_price,
                "unrealized_pnl": round(self.pos_upnl, 2),
                "notional": round(self.pos_qty * self.pos_mark_price, 2),
                "tp": self.pos_tp,
                "sl": self.pos_sl,
            } if self.has_position else {"active": False},
            "risk": {
                "circuit_breaker": self.circuit_breaker,
                "daily_realized_pnl": round(self.daily_realized_pnl, 2),
                "daily_loss_limit": self.daily_loss_limit,
                "max_position_usdt": self.max_position_usdt,
            },
            "stats": {
                "total_trades": len(self.trades),
                "win_trades": len(win_trades),
                "win_rate": win_rate,
                "total_net_pnl": round(sum(t["net_pnl"] for t in self.trades), 2),
            },
            "trades": self.trades,
        }


# 全局单例
_store: Optional[StatusStore] = None


def init_store(mode: str = "paper", strategy: str = "btc", symbol: str = "BTCUSDT") -> StatusStore:
    global _store
    _store = StatusStore(
        mode=mode,
        strategy=strategy,
        symbol=symbol,
        started_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return _store


def get_store() -> Optional[StatusStore]:
    return _store
