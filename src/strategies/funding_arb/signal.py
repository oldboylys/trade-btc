"""资金费率套利信号生成."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.core.models import Exchange, FundingRate
from src.strategies.funding_arb.collector import FundingRateSnapshot


@dataclass
class ArbSignal:
    """套利信号：做空高资金费率，做多低资金费率."""
    symbol: str
    long_exchange: Exchange        # 做多的交易所（低资金费率）
    short_exchange: Exchange       # 做空的交易所（高资金费率）
    long_rate: Decimal
    short_rate: Decimal
    spread: Decimal
    expected_pnl_pct: Decimal      # 扣除手续费后的预期收益率（每次结算）
    long_mark_price: Optional[Decimal] = None
    short_mark_price: Optional[Decimal] = None
    ts_ms: int = 0
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        return self.spread > 0 and self.expected_pnl_pct > 0


class ArbSignalGenerator:
    """
    资金费率套利信号生成器。
    扣除预估手续费和滑点后，net spread 超过阈值才产生信号。
    """

    def __init__(
        self,
        min_spread: Decimal = Decimal("0.0002"),    # 最小套利利差 0.02%
        estimated_fee_pct: Decimal = Decimal("0.0008"),  # 双腿开仓总手续费约 0.08%
        estimated_slippage_pct: Decimal = Decimal("0.001"),  # 双腿滑点约 0.1%
    ) -> None:
        self.min_spread = min_spread
        self.estimated_fee_pct = estimated_fee_pct
        self.estimated_slippage_pct = estimated_slippage_pct
        self._total_cost = estimated_fee_pct + estimated_slippage_pct

    def generate(
        self,
        snapshot: FundingRateSnapshot,
        symbol: str,
    ) -> Optional[ArbSignal]:
        high_rate, low_rate, spread = snapshot.max_spread(symbol)
        if high_rate is None or low_rate is None:
            return None

        # 需要向高资金费率方做空以收取资金费率
        # 套利收益 = spread - 开仓手续费 - 滑点
        net_pnl_pct = spread - self._total_cost

        if net_pnl_pct < self.min_spread:
            return None

        import time
        return ArbSignal(
            symbol=symbol,
            long_exchange=low_rate.exchange,
            short_exchange=high_rate.exchange,
            long_rate=low_rate.rate,
            short_rate=high_rate.rate,
            spread=spread,
            expected_pnl_pct=net_pnl_pct,
            long_mark_price=low_rate.mark_price,
            short_mark_price=high_rate.mark_price,
            ts_ms=int(time.time() * 1000),
            reason=(
                f"spread={float(spread):.4f} "
                f"short@{high_rate.exchange.value}={float(high_rate.rate):.4f} "
                f"long@{low_rate.exchange.value}={float(low_rate.rate):.4f}"
            ),
        )
