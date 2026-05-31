"""持仓账本：追踪仓位、PnL、保证金、日内亏损."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.core.logging import get_logger
from src.core.models import Exchange, Fill, Order, OrderSide, Position, PositionSide

logger = get_logger("sim.position_book")

try:
    from src.core.telegram import TelegramNotifier as _TelegramNotifier
except ImportError:
    _TelegramNotifier = None  # type: ignore


@dataclass
class PositionEntry:
    symbol: str
    exchange: Exchange
    side: PositionSide
    qty: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    total_fee: Decimal = Decimal("0")
    mark_price: Decimal = Decimal("0")

    @property
    def unrealized_pnl(self) -> Decimal:
        if self.qty == 0:
            return Decimal("0")
        if self.side == PositionSide.LONG:
            return (self.mark_price - self.entry_price) * self.qty
        return (self.entry_price - self.mark_price) * self.qty

    @property
    def notional(self) -> Decimal:
        return self.qty * self.mark_price

    def to_position(self) -> Position:
        return Position(
            symbol=self.symbol,
            exchange=self.exchange,
            side=self.side,
            qty=self.qty,
            entry_price=self.entry_price,
            mark_price=self.mark_price,
            unrealized_pnl=self.unrealized_pnl,
            realized_pnl=self.realized_pnl,
        )


class PositionBook:
    """
    持仓账本（纸交易）：
    - 根据 Fill 更新仓位（FIFO）
    - 追踪日内已实现亏损（用于熔断）
    - 维护每个 symbol 的 mark_price
    """

    def __init__(self, exchange: Exchange = Exchange.SIM) -> None:
        self.exchange = exchange
        self._positions: dict[str, PositionEntry] = {}  # symbol -> entry
        self._daily_realized_pnl: Decimal = Decimal("0")
        self._total_fee: Decimal = Decimal("0")
        self._notifier: Optional["_TelegramNotifier"] = None
        self._store: Optional[object] = None
        self._open_time: dict[str, str] = {}  # symbol -> open_time str
        self._tp_sl: dict[str, tuple[float, float]] = {}  # symbol -> (tp, sl)

    def set_notifier(self, notifier: "_TelegramNotifier") -> None:
        self._notifier = notifier

    def set_store(self, store: object) -> None:
        self._store = store

    def set_tp_sl(self, symbol: str, tp: float, sl: float) -> None:
        self._tp_sl[symbol] = (tp, sl)

    def _sync_store(self, symbol: str) -> None:
        """成交后同步 Web 看板持仓状态."""
        if self._store is None:
            return
        pos = self._positions.get(symbol)
        if pos and pos.qty > 0:
            tp, sl = self._tp_sl.get(symbol, (0.0, 0.0))
            self._store.has_position = True
            self._store.pos_side = pos.side.value
            self._store.pos_qty = float(pos.qty)
            self._store.pos_entry_price = float(pos.entry_price)
            self._store.pos_mark_price = float(pos.mark_price)
            self._store.pos_upnl = float(pos.unrealized_pnl)
            self._store.pos_tp = tp
            self._store.pos_sl = sl
        else:
            self._store.has_position = False
            self._store.pos_qty = 0.0
            self._store.pos_upnl = 0.0
            self._tp_sl.pop(symbol, None)

    def on_fill(self, fill: Fill) -> None:
        key = fill.symbol
        pos = self._positions.get(key)
        if pos is None:
            side = (
                PositionSide.LONG if fill.side == OrderSide.BUY else PositionSide.SHORT
            )
            pos = PositionEntry(
                symbol=fill.symbol,
                exchange=fill.exchange,
                side=side,
            )
            self._positions[key] = pos

        self._total_fee += fill.fee

        if pos.qty == 0:
            # 开新仓
            import datetime as _dt
            open_time_str = _dt.datetime.now().strftime("%m-%d %H:%M")
            self._open_time[fill.symbol] = open_time_str
            pos.side = PositionSide.LONG if fill.side == OrderSide.BUY else PositionSide.SHORT
            pos.qty = fill.qty
            pos.entry_price = fill.price
            logger.info(
                "position_opened",
                symbol=fill.symbol,
                side=pos.side.value,
                qty=float(pos.qty),
                entry_price=float(pos.entry_price),
                notional=round(float(pos.qty * pos.entry_price), 2),
            )
            # Telegram 开仓通知（仅在真实成交时发送）
            if self._notifier:
                direction_label = "多头 LONG" if pos.side == PositionSide.LONG else "空头 SHORT"
                tp, sl = self._tp_sl.get(fill.symbol, (0.0, 0.0))
                self._notifier.notify_open(
                    symbol=fill.symbol,
                    direction=direction_label,
                    qty=float(pos.qty),
                    price=float(fill.price),
                    notional=round(float(pos.qty * fill.price), 2),
                    tp_price=tp if tp > 0 else None,
                    sl_price=sl if sl > 0 else None,
                )
            self._sync_store(fill.symbol)
        elif (pos.side == PositionSide.LONG and fill.side == OrderSide.BUY) or (
            pos.side == PositionSide.SHORT and fill.side == OrderSide.SELL
        ):
            # 加仓：加权平均
            total_cost = pos.entry_price * pos.qty + fill.price * fill.qty
            pos.qty += fill.qty
            pos.entry_price = total_cost / pos.qty
            logger.info(
                "position_increased",
                symbol=fill.symbol,
                side=pos.side.value,
                qty=float(pos.qty),
                avg_entry=round(float(pos.entry_price), 2),
            )
            self._sync_store(fill.symbol)
        else:
            # 减仓/平仓
            if fill.qty >= pos.qty:
                # 完全平仓
                closed_qty = pos.qty
                entry_price = pos.entry_price
                if pos.side == PositionSide.LONG:
                    pnl = (fill.price - pos.entry_price) * pos.qty
                else:
                    pnl = (pos.entry_price - fill.price) * pos.qty
                fee = fill.fee
                net_pnl = pnl - fee
                pos.realized_pnl += pnl
                self._daily_realized_pnl += pnl
                remaining = fill.qty - pos.qty
                pos.qty = Decimal("0")
                pos.entry_price = Decimal("0")

                pnl_sign = "+" if net_pnl >= 0 else ""
                result_label = "盈利" if net_pnl >= 0 else "亏损"
                print(
                    f"\n{'*'*60}\n"
                    f"  【平仓结算】{fill.symbol}  {pos.side.value}  {result_label}\n"
                    f"  开仓价 : ${float(entry_price):,.2f}\n"
                    f"  平仓价 : ${float(fill.price):,.2f}\n"
                    f"  数量   : {float(closed_qty):.4f} BTC\n"
                    f"  毛盈亏 : {pnl_sign}{float(pnl):,.2f} USDT\n"
                    f"  手续费 : -{float(fee):.2f} USDT\n"
                    f"  净盈亏 : {pnl_sign}{float(net_pnl):,.2f} USDT\n"
                    f"  累计已实现PnL: {float(pos.realized_pnl):,.2f} USDT\n"
                    f"{'*'*60}\n"
                )
                logger.info(
                    "position_closed",
                    symbol=fill.symbol,
                    entry_price=float(entry_price),
                    close_price=float(fill.price),
                    qty=float(closed_qty),
                    gross_pnl=round(float(pnl), 2),
                    fee=round(float(fee), 2),
                    net_pnl=round(float(net_pnl), 2),
                    total_realized=round(float(pos.realized_pnl), 2),
                )

                # 更新 Web 状态存储（成交记录）
                if self._store is not None:
                    import datetime as _dt
                    close_time_str = _dt.datetime.now().strftime("%m-%d %H:%M")
                    open_time_str = self._open_time.pop(fill.symbol, "--")
                    if fill.order_id.startswith("tp_"):
                        close_reason = "止盈"
                    elif fill.order_id.startswith("sl_"):
                        close_reason = "止损"
                    else:
                        close_reason = "信号反转"
                    tp, sl = self._tp_sl.get(fill.symbol, (0.0, 0.0))
                    self._store.add_trade(
                        direction=pos.side.value,
                        qty=float(closed_qty),
                        open_time=open_time_str,
                        open_price=float(entry_price),
                        close_time=close_time_str,
                        close_price=float(fill.price),
                        tp=tp,
                        sl=sl,
                        gross_pnl=float(pnl),
                        fee=float(fee),
                        net_pnl=float(net_pnl),
                        close_reason=close_reason,
                    )

                # Telegram 平仓通知
                if self._notifier:
                    self._notifier.notify_close(
                        symbol=fill.symbol,
                        side=pos.side.value,
                        qty=float(closed_qty),
                        entry_price=float(entry_price),
                        close_price=float(fill.price),
                        gross_pnl=round(float(pnl), 2),
                        fee=round(float(fee), 2),
                        net_pnl=round(float(net_pnl), 2),
                        total_realized=round(float(pos.realized_pnl), 2),
                    )

                self._sync_store(fill.symbol)
                if remaining > Decimal("0.0001"):
                    pos.side = PositionSide.LONG if fill.side == OrderSide.BUY else PositionSide.SHORT
                    pos.qty = remaining
                    pos.entry_price = fill.price
            else:
                # 部分减仓
                if pos.side == PositionSide.LONG:
                    pnl = (fill.price - pos.entry_price) * fill.qty
                else:
                    pnl = (pos.entry_price - fill.price) * fill.qty
                pos.realized_pnl += pnl
                self._daily_realized_pnl += pnl
                pos.qty -= fill.qty
                logger.info(
                    "position_partial_close",
                    symbol=fill.symbol,
                    closed_qty=float(fill.qty),
                    remaining_qty=float(pos.qty),
                    pnl=round(float(pnl), 2),
                )
                self._sync_store(fill.symbol)

        logger.debug(
            "position_updated",
            symbol=fill.symbol,
            side=pos.side.value,
            qty=float(pos.qty),
            entry=float(pos.entry_price),
            upnl=float(pos.unrealized_pnl),
        )

    def update_mark_price(self, symbol: str, price: Decimal) -> None:
        pos = self._positions.get(symbol)
        if pos:
            pos.mark_price = price

    def get_position(self, symbol: str) -> Optional[PositionEntry]:
        return self._positions.get(symbol)

    def get_all_positions(self) -> list[PositionEntry]:
        return [p for p in self._positions.values() if p.qty > 0]

    def total_unrealized_pnl(self) -> Decimal:
        return sum((p.unrealized_pnl for p in self._positions.values()), Decimal("0"))

    def reset_daily_pnl(self) -> None:
        self._daily_realized_pnl = Decimal("0")

    @property
    def daily_realized_pnl(self) -> Decimal:
        return self._daily_realized_pnl

    @property
    def total_fee(self) -> Decimal:
        return self._total_fee
