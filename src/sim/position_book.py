"""持仓账本：追踪仓位、PnL、保证金、日内亏损."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from src.core.logging import get_logger
from src.core.models import Exchange, Fill, Order, OrderSide, Position, PositionSide

logger = get_logger("sim.position_book")


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
            pos.side = PositionSide.LONG if fill.side == OrderSide.BUY else PositionSide.SHORT
            pos.qty = fill.qty
            pos.entry_price = fill.price
        elif (pos.side == PositionSide.LONG and fill.side == OrderSide.BUY) or (
            pos.side == PositionSide.SHORT and fill.side == OrderSide.SELL
        ):
            # 加仓：加权平均
            total_cost = pos.entry_price * pos.qty + fill.price * fill.qty
            pos.qty += fill.qty
            pos.entry_price = total_cost / pos.qty
        else:
            # 减仓/平仓
            if fill.qty >= pos.qty:
                # 完全平仓
                if pos.side == PositionSide.LONG:
                    pnl = (fill.price - pos.entry_price) * pos.qty
                else:
                    pnl = (pos.entry_price - fill.price) * pos.qty
                pos.realized_pnl += pnl
                self._daily_realized_pnl += pnl
                remaining = fill.qty - pos.qty
                pos.qty = Decimal("0")
                pos.entry_price = Decimal("0")
                # 超出部分开反向仓
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
