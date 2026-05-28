"""双腿套利执行：同步开仓、失败补偿、再平衡."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional

from src.connectors.base import IExchange
from src.core.clock import get_clock
from src.core.logging import get_logger
from src.core.models import (
    Exchange, Order, OrderSide, OrderStatus, OrderType, PositionSide,
)
from src.strategies.funding_arb.signal import ArbSignal

logger = get_logger("funding_arb.executor")


class LegState(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIAL = "partial"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass
class ArbPosition:
    """套利持仓：两腿状态."""
    signal: ArbSignal
    target_qty: Decimal
    long_order: Optional[Order] = None
    short_order: Optional[Order] = None
    long_state: LegState = LegState.PENDING
    short_state: LegState = LegState.PENDING
    opened_at_ms: int = 0
    closed_at_ms: int = 0
    realized_pnl: Decimal = Decimal("0")

    @property
    def is_open(self) -> bool:
        return (
            self.long_state == LegState.OPEN
            and self.short_state == LegState.OPEN
        )

    @property
    def has_leg_imbalance(self) -> bool:
        """是否存在腿间不平衡（风险状态）."""
        return (
            self.long_state == LegState.OPEN and self.short_state != LegState.OPEN
        ) or (
            self.short_state == LegState.OPEN and self.long_state != LegState.OPEN
        )


class FundingArbExecutor:
    """
    资金费率套利执行器：
    1. 双腿同步开仓（先尝试双侧市价单）
    2. 任一腿失败时执行安全关闭（平掉已成交腿）
    3. 支持资金费率结算后平仓再平衡
    """

    def __init__(
        self,
        exchanges: dict[str, IExchange],
        max_position_usdt: Decimal = Decimal("5000"),
        order_timeout_s: float = 10.0,
    ) -> None:
        self.exchanges = exchanges
        self.max_position_usdt = max_position_usdt
        self.order_timeout_s = order_timeout_s
        self._positions: list[ArbPosition] = []

    def get_exchange(self, exchange: Exchange) -> Optional[IExchange]:
        return self.exchanges.get(exchange.value)

    async def open_position(self, signal: ArbSignal) -> Optional[ArbPosition]:
        """开双腿套利仓位."""
        long_ex = self.get_exchange(signal.long_exchange)
        short_ex = self.get_exchange(signal.short_exchange)
        if not long_ex or not short_ex:
            logger.error("missing_exchange",
                         long=signal.long_exchange.value,
                         short=signal.short_exchange.value)
            return None

        # 计算目标数量
        ref_price = signal.long_mark_price or signal.short_mark_price or Decimal("50000")
        target_qty = (self.max_position_usdt / ref_price).quantize(Decimal("0.001"))

        arb_pos = ArbPosition(signal=signal, target_qty=target_qty,
                              opened_at_ms=get_clock().now_ms())

        long_order = Order(
            client_order_id=f"arb_long_{uuid.uuid4().hex[:8]}",
            exchange=signal.long_exchange,
            symbol=signal.symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            qty=target_qty,
        )
        short_order = Order(
            client_order_id=f"arb_short_{uuid.uuid4().hex[:8]}",
            exchange=signal.short_exchange,
            symbol=signal.symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            qty=target_qty,
        )

        # 同步下双腿
        try:
            long_result, short_result = await asyncio.gather(
                long_ex.place_order(long_order),
                short_ex.place_order(short_order),
                return_exceptions=True,
            )

            long_ok = isinstance(long_result, Order) and long_result.status in (
                OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED
            )
            short_ok = isinstance(short_result, Order) and short_result.status in (
                OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED
            )

            arb_pos.long_order = long_result if isinstance(long_result, Order) else None
            arb_pos.short_order = short_result if isinstance(short_result, Order) else None
            arb_pos.long_state = LegState.OPEN if long_ok else LegState.FAILED
            arb_pos.short_state = LegState.OPEN if short_ok else LegState.FAILED

            if long_ok and short_ok:
                self._positions.append(arb_pos)
                logger.info(
                    "arb_position_opened",
                    symbol=signal.symbol,
                    long_ex=signal.long_exchange.value,
                    short_ex=signal.short_exchange.value,
                    qty=float(target_qty),
                    spread=float(signal.spread),
                )
                return arb_pos

            # 一腿失败：安全关闭
            logger.warning("arb_open_partial_failure", arb_pos=arb_pos)
            await self._emergency_close_leg(arb_pos, long_ex, short_ex)
            return None

        except Exception as exc:
            logger.error("arb_open_error", error=str(exc))
            return None

    async def close_position(self, arb_pos: ArbPosition) -> bool:
        """平掉套利仓位."""
        long_ex = self.get_exchange(arb_pos.signal.long_exchange)
        short_ex = self.get_exchange(arb_pos.signal.short_exchange)
        if not long_ex or not short_ex:
            return False

        close_long = Order(
            client_order_id=f"arb_close_long_{uuid.uuid4().hex[:8]}",
            exchange=arb_pos.signal.long_exchange,
            symbol=arb_pos.signal.symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            qty=arb_pos.target_qty,
            reduce_only=True,
        )
        close_short = Order(
            client_order_id=f"arb_close_short_{uuid.uuid4().hex[:8]}",
            exchange=arb_pos.signal.short_exchange,
            symbol=arb_pos.signal.symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            qty=arb_pos.target_qty,
            reduce_only=True,
        )

        try:
            await asyncio.gather(
                long_ex.place_order(close_long),
                short_ex.place_order(close_short),
            )
            arb_pos.long_state = LegState.CLOSED
            arb_pos.short_state = LegState.CLOSED
            arb_pos.closed_at_ms = get_clock().now_ms()
            logger.info("arb_position_closed", symbol=arb_pos.signal.symbol)
            return True
        except Exception as exc:
            logger.error("arb_close_error", error=str(exc))
            return False

    async def _emergency_close_leg(
        self,
        arb_pos: ArbPosition,
        long_ex: IExchange,
        short_ex: IExchange,
    ) -> None:
        """紧急关闭已成交的腿，优先降低风险."""
        if arb_pos.long_state == LegState.OPEN and arb_pos.long_order:
            try:
                close = Order(
                    client_order_id=f"arb_emg_{uuid.uuid4().hex[:8]}",
                    exchange=arb_pos.signal.long_exchange,
                    symbol=arb_pos.signal.symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    qty=arb_pos.long_order.filled_qty or arb_pos.target_qty,
                    reduce_only=True,
                )
                await long_ex.place_order(close)
                arb_pos.long_state = LegState.CLOSED
                logger.warning("arb_emergency_close_long")
            except Exception as exc:
                logger.error("arb_emergency_close_long_failed", error=str(exc))

        if arb_pos.short_state == LegState.OPEN and arb_pos.short_order:
            try:
                close = Order(
                    client_order_id=f"arb_emg_{uuid.uuid4().hex[:8]}",
                    exchange=arb_pos.signal.short_exchange,
                    symbol=arb_pos.signal.symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    qty=arb_pos.short_order.filled_qty or arb_pos.target_qty,
                    reduce_only=True,
                )
                await short_ex.place_order(close)
                arb_pos.short_state = LegState.CLOSED
                logger.warning("arb_emergency_close_short")
            except Exception as exc:
                logger.error("arb_emergency_close_short_failed", error=str(exc))

    @property
    def open_positions(self) -> list[ArbPosition]:
        return [p for p in self._positions if p.is_open]
