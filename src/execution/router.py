"""执行路由：将策略目标仓位转换为实际下单指令，含幂等/重试/止盈止损管理."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Optional

from src.connectors.base import IExchange
from src.core.clock import get_clock
from src.core.logging import get_logger
from src.core.mode import ModeGuard
from src.core.models import (
    Exchange, Order, OrderSide, OrderStatus, OrderType,
    Position, PositionSide, SignalDirection, TargetPosition,
    TradingMode,
)
from src.risk.manager import RiskAction, RiskManager

logger = get_logger("execution.router")

try:
    from src.core.telegram import TelegramNotifier
except ImportError:
    TelegramNotifier = None  # type: ignore


class ExecutionRouter:
    """
    执行路由器：
    - 接收 TargetPosition，和当前持仓对比，决定是否开/平/加仓
    - 调用风控检查
    - 管理止盈止损条件单（交易所条件单 + 策略侧备选）
    - 幂等去重（相同 symbol 未完成的操作不重复下单）
    """

    def __init__(
        self,
        exchange: IExchange,
        risk: RiskManager,
        mode_guard: ModeGuard,
        order_timeout_s: float = 30.0,
        max_retry: int = 3,
        notifier: Optional["TelegramNotifier"] = None,
    ) -> None:
        self.exchange = exchange
        self.risk = risk
        self.mode_guard = mode_guard
        self.order_timeout_s = order_timeout_s
        self.max_retry = max_retry
        self.notifier = notifier
        self._pending: dict[str, str] = {}  # symbol -> client_order_id
        self._tp_sl_orders: dict[str, list[str]] = {}  # symbol -> [tp_oid, sl_oid]

    async def execute(self, target: TargetPosition) -> None:
        symbol = target.symbol

        current_pos = await self.exchange.get_position(symbol)
        mark = await self.exchange.get_mark_price(symbol)
        mark_price = mark.mark_price

        current_qty = Decimal("0")
        current_side = None
        if current_pos and current_pos.qty > 0:
            current_qty = current_pos.qty
            current_side = current_pos.side

        # 风控检查
        check = self.risk.check_target_position(
            target,
            current_qty * mark_price,
            mark_price,
        )

        if check.action == RiskAction.BLOCK:
            logger.warning("order_blocked_by_risk",
                           symbol=symbol, reason=check.reason)
            return

        effective_qty = target.target_qty
        if check.action == RiskAction.REDUCE_ONLY:
            effective_qty = check.adjusted_qty or Decimal("0")

        # 计算需要的动作
        await self._reconcile(
            symbol=symbol,
            target_direction=target.direction,
            target_qty=effective_qty,
            current_side=current_side,
            current_qty=current_qty,
            mark_price=mark_price,
            tp_price=target.tp_price,
            sl_price=target.sl_price,
        )

    async def _reconcile(
        self,
        symbol: str,
        target_direction: SignalDirection,
        target_qty: Decimal,
        current_side: Optional[PositionSide],
        current_qty: Decimal,
        mark_price: Decimal,
        tp_price: Optional[Decimal],
        sl_price: Optional[Decimal],
    ) -> None:
        if target_direction == SignalDirection.FLAT:
            if current_qty > 0:
                await self._close_position(symbol, current_side, current_qty, mark_price, reason="信号平仓")
            return

        target_pos_side = (
            PositionSide.LONG if target_direction == SignalDirection.LONG else PositionSide.SHORT
        )

        # 若持仓方向不一致，先平仓
        if current_side and current_side != target_pos_side and current_qty > 0:
            await self._close_position(symbol, current_side, current_qty, mark_price, reason="信号反转")
            current_qty = Decimal("0")

        delta = target_qty - current_qty
        if delta <= Decimal("0.001"):
            return  # 无需操作

        order_side = OrderSide.BUY if target_direction == SignalDirection.LONG else OrderSide.SELL
        direction_label = "多头 LONG" if target_direction == SignalDirection.LONG else "空头 SHORT"
        notional = round(float(delta * mark_price), 2)

        print(
            f"\n{'='*60}\n"
            f"  【开 仓】{symbol}  {direction_label}\n"
            f"  数量   : {float(delta):.4f} BTC\n"
            f"  开仓价 : ${float(mark_price):,.2f}\n"
            f"  名义仓位: ${notional:,.2f} USDT\n"
            + (f"  止盈价 : ${float(tp_price):,.2f}\n" if tp_price else "  止盈价 : --\n")
            + (f"  止损价 : ${float(sl_price):,.2f}\n" if sl_price else "  止损价 : --\n")
            + f"{'='*60}\n"
        )

        # Telegram 开仓通知
        if self.notifier:
            self.notifier.notify_open(
                symbol=symbol,
                direction=direction_label,
                qty=float(delta),
                price=float(mark_price),
                notional=notional,
                tp_price=float(tp_price) if tp_price else None,
                sl_price=float(sl_price) if sl_price else None,
            )

        await self._place_order_with_retry(
            Order(
                client_order_id=str(uuid.uuid4()),
                exchange=self.exchange.__class__.__name__.lower().replace("connector", ""),
                symbol=symbol,
                side=order_side,
                order_type=OrderType.MARKET,
                qty=delta,
            ),
            mark_price=mark_price,
        )

        # 挂止盈止损条件单
        if tp_price or sl_price:
            await self._place_tp_sl(symbol, target_direction, target_qty, tp_price, sl_price)

    async def _close_position(
        self,
        symbol: str,
        side: Optional[PositionSide],
        qty: Decimal,
        mark_price: Decimal,
        reason: str = "平仓",
    ) -> None:
        close_side = (
            OrderSide.SELL if side == PositionSide.LONG else OrderSide.BUY
        )
        direction_label = "多头 LONG" if side == PositionSide.LONG else "空头 SHORT"
        notional = round(float(qty * mark_price), 2)

        print(
            f"\n{'─'*60}\n"
            f"  【平 仓】{symbol}  平{direction_label}  原因: {reason}\n"
            f"  数量   : {float(qty):.4f} BTC\n"
            f"  平仓价 : ${float(mark_price):,.2f}\n"
            f"  名义仓位: ${notional:,.2f} USDT\n"
            f"  (PnL 详情见下方持仓账本输出)\n"
            f"{'─'*60}\n"
        )

        # 先撤掉已挂的 TP/SL 单
        await self._cancel_tp_sl(symbol)
        await self._place_order_with_retry(
            Order(
                client_order_id=str(uuid.uuid4()),
                exchange=Exchange.SIM,
                symbol=symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                qty=qty,
                reduce_only=True,
            ),
            mark_price=mark_price,
        )

    async def _place_tp_sl(
        self,
        symbol: str,
        direction: SignalDirection,
        qty: Decimal,
        tp_price: Optional[Decimal],
        sl_price: Optional[Decimal],
    ) -> None:
        await self._cancel_tp_sl(symbol)
        close_side = OrderSide.SELL if direction == SignalDirection.LONG else OrderSide.BUY
        order_ids = []

        if tp_price:
            tp_order = Order(
                client_order_id=f"tp_{uuid.uuid4().hex[:8]}",
                exchange=Exchange.SIM,
                symbol=symbol,
                side=close_side,
                order_type=OrderType.TAKE_PROFIT_MARKET,
                qty=qty,
                stop_price=tp_price,
                reduce_only=True,
            )
            check = self.risk.check_order(tp_order, tp_price)
            if check.action == RiskAction.ALLOW:
                await self._place_raw(tp_order)
                order_ids.append(tp_order.client_order_id)

        if sl_price:
            sl_order = Order(
                client_order_id=f"sl_{uuid.uuid4().hex[:8]}",
                exchange=Exchange.SIM,
                symbol=symbol,
                side=close_side,
                order_type=OrderType.STOP_MARKET,
                qty=qty,
                stop_price=sl_price,
                reduce_only=True,
            )
            check = self.risk.check_order(sl_order, sl_price)
            if check.action == RiskAction.ALLOW:
                await self._place_raw(sl_order)
                order_ids.append(sl_order.client_order_id)

        if order_ids:
            self._tp_sl_orders[symbol] = order_ids

    async def _cancel_tp_sl(self, symbol: str) -> None:
        for oid in self._tp_sl_orders.pop(symbol, []):
            try:
                await self.exchange.cancel_order(symbol, oid)
            except Exception:
                pass

    async def _place_order_with_retry(self, order: Order, mark_price: Decimal) -> None:
        check = self.risk.check_order(order, mark_price)
        if check.action != RiskAction.ALLOW:
            logger.warning("order_blocked", symbol=order.symbol, reason=check.reason)
            return

        if self.mode_guard.is_paper:
            pass  # PaperExchange 可以直接下单

        await self._place_raw(order)

    async def _place_raw(self, order: Order) -> None:
        for attempt in range(self.max_retry):
            try:
                result = await self.exchange.place_order(order)
                logger.info(
                    "order_sent",
                    symbol=order.symbol,
                    side=order.side.value,
                    qty=float(order.qty),
                    status=result.status.value,
                )
                return
            except Exception as exc:
                logger.warning("order_retry", attempt=attempt, error=str(exc))
                if attempt < self.max_retry - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))
        logger.error("order_failed_all_retries", symbol=order.symbol)
