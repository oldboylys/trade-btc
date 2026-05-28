"""滑点与手续费模型."""
from __future__ import annotations

from decimal import Decimal

from src.core.models import Order, OrderSide, OrderType


class FeeModel:
    """手续费模型（Taker/Maker）."""

    def __init__(
        self,
        taker_rate: Decimal = Decimal("0.0004"),  # 0.04%
        maker_rate: Decimal = Decimal("0.0002"),  # 0.02%
    ) -> None:
        self.taker_rate = taker_rate
        self.maker_rate = maker_rate

    def calc_fee(
        self, notional: Decimal, is_maker: bool = False
    ) -> Decimal:
        rate = self.maker_rate if is_maker else self.taker_rate
        return notional * rate


class SlippageModel:
    """滑点模型：固定比例 + 可选波动放大."""

    def __init__(
        self,
        base_slippage_pct: Decimal = Decimal("0.0005"),  # 0.05%
        impact_factor: Decimal = Decimal("0"),           # 冲击成本（量化交易量/流动性）
    ) -> None:
        self.base_slippage_pct = base_slippage_pct
        self.impact_factor = impact_factor

    def apply(
        self,
        price: Decimal,
        side: OrderSide,
        qty: Decimal,
        available_liquidity: Decimal | None = None,
    ) -> Decimal:
        slippage = self.base_slippage_pct
        if available_liquidity and available_liquidity > 0 and self.impact_factor > 0:
            impact = self.impact_factor * qty / available_liquidity
            slippage = slippage + impact

        if side == OrderSide.BUY:
            return price * (1 + slippage)
        else:
            return price * (1 - slippage)
