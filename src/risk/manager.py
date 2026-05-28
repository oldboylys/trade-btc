"""风险控制模块：仓位/单笔/亏损熔断/异常行情/断连保护."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional

from src.core.logging import get_logger
from src.core.models import Order, OrderSide, OrderType, SignalDirection, TargetPosition

logger = get_logger("risk.manager")


class RiskAction(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REDUCE_ONLY = "reduce_only"


@dataclass
class RiskCheckResult:
    action: RiskAction
    reason: str = ""
    adjusted_qty: Optional[Decimal] = None  # REDUCE_ONLY 时给出最大允许数量


@dataclass
class RiskConfig:
    max_position_usdt: Decimal = Decimal("20000")
    max_single_order_usdt: Decimal = Decimal("5000")
    max_daily_loss_usdt: Decimal = Decimal("1000")
    max_consecutive_losses: int = 5
    price_deviation_pct: float = 0.05   # 异常价格偏差阈值
    disconnect_reduce_only: bool = True  # 断线后只允许减仓


@dataclass
class RiskState:
    daily_loss: Decimal = Decimal("0")
    consecutive_losses: int = 0
    is_circuit_broken: bool = False       # 连续亏损/日内亏损熔断
    is_disconnected: bool = False         # 连接断开
    last_known_prices: dict[str, Decimal] = field(default_factory=dict)
    total_position_usdt: Decimal = Decimal("0")


class RiskManager:
    """
    风控检查器，在执行层下单前调用 check() 做拦截。
    所有触发熔断的情况记录日志，并通知调用方。
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self.state = RiskState()
        self._on_circuit_break_callbacks: list = []

    # -------- 外部通知接口 --------

    def on_realized_pnl(self, pnl: Decimal) -> None:
        """每笔交易产生已实现PnL时调用."""
        if pnl < 0:
            self.state.daily_loss += abs(pnl)
            self.state.consecutive_losses += 1
            self._check_circuit_break()
        else:
            self.state.consecutive_losses = 0

    def on_price_update(self, symbol: str, price: Decimal) -> None:
        self.state.last_known_prices[symbol] = price

    def on_disconnect(self) -> None:
        self.state.is_disconnected = True
        logger.warning("risk_disconnect_protection_active")

    def on_reconnect(self) -> None:
        self.state.is_disconnected = False
        logger.info("risk_disconnect_cleared")

    def reset_daily(self) -> None:
        self.state.daily_loss = Decimal("0")
        self.state.is_circuit_broken = False
        logger.info("risk_daily_reset")

    def manual_reset_circuit_break(self) -> None:
        self.state.is_circuit_broken = False
        self.state.consecutive_losses = 0
        logger.warning("risk_circuit_break_manually_reset")

    # -------- 主检查接口 --------

    def check_target_position(
        self,
        target: TargetPosition,
        current_position_usdt: Decimal,
        current_mark_price: Decimal,
    ) -> RiskCheckResult:
        """检查策略给出的目标仓位是否合规."""

        # 1. 熔断检查
        if self.state.is_circuit_broken:
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason="circuit_break_active",
            )

        # 2. 断线保护：只允许减仓
        if self.state.is_disconnected and self.config.disconnect_reduce_only:
            if target.direction != SignalDirection.FLAT and target.target_qty > 0:
                return RiskCheckResult(
                    action=RiskAction.REDUCE_ONLY,
                    reason="disconnect_reduce_only",
                )

        # 3. 异常价格保护
        if not self._price_ok(target.symbol, current_mark_price):
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason=f"price_deviation_too_large: {target.symbol}",
            )

        # 4. 目标仓位名义价值上限
        if target.target_qty > 0:
            target_notional = target.target_qty * current_mark_price
            if target_notional > self.config.max_position_usdt:
                max_qty = (self.config.max_position_usdt / current_mark_price).quantize(
                    Decimal("0.001")
                )
                logger.warning(
                    "position_limit_hit",
                    requested=float(target_notional),
                    limit=float(self.config.max_position_usdt),
                )
                return RiskCheckResult(
                    action=RiskAction.REDUCE_ONLY,
                    reason="max_position_exceeded",
                    adjusted_qty=max_qty,
                )

        return RiskCheckResult(action=RiskAction.ALLOW)

    def check_order(
        self,
        order: Order,
        mark_price: Decimal,
    ) -> RiskCheckResult:
        """下单前最后一道检查."""
        if self.state.is_circuit_broken:
            return RiskCheckResult(action=RiskAction.BLOCK, reason="circuit_break")

        if self.state.is_disconnected and self.config.disconnect_reduce_only:
            if not order.reduce_only:
                return RiskCheckResult(action=RiskAction.BLOCK, reason="disconnect_reduce_only")

        # 单笔名义价值检查
        price = order.price or mark_price
        notional = price * order.qty
        if notional > self.config.max_single_order_usdt:
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason=f"single_order_notional_exceeded: {float(notional):.0f}",
            )

        return RiskCheckResult(action=RiskAction.ALLOW)

    # -------- 内部 --------

    def _check_circuit_break(self) -> None:
        triggered = False
        reason = ""

        if self.state.daily_loss >= self.config.max_daily_loss_usdt:
            triggered = True
            reason = f"daily_loss={float(self.state.daily_loss):.2f}"

        if self.state.consecutive_losses >= self.config.max_consecutive_losses:
            triggered = True
            reason = f"consecutive_losses={self.state.consecutive_losses}"

        if triggered and not self.state.is_circuit_broken:
            self.state.is_circuit_broken = True
            logger.error("circuit_break_triggered", reason=reason)
            for cb in self._on_circuit_break_callbacks:
                try:
                    cb(reason)
                except Exception:
                    pass

    def _price_ok(self, symbol: str, current_price: Decimal) -> bool:
        last = self.state.last_known_prices.get(symbol)
        if last is None or last == 0:
            self.state.last_known_prices[symbol] = current_price
            return True
        deviation = abs(current_price - last) / last
        return float(deviation) <= self.config.price_deviation_pct

    def on_circuit_break(self, callback) -> None:
        self._on_circuit_break_callbacks.append(callback)

    @property
    def is_healthy(self) -> bool:
        return (
            not self.state.is_circuit_broken
            and not self.state.is_disconnected
        )
