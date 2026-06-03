"""流动性池与扫荡（equal highs/lows + sweep）."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.core.models import Kline


@dataclass
class LiquidityPool:
    kind: str  # highs | lows
    level: float
    swept: bool = False


class LiquidityTracker:
    def __init__(
        self,
        equal_threshold_pct: float = 0.0005,
        sweep_confirm_bars: int = 3,
    ) -> None:
        self.equal_threshold_pct = equal_threshold_pct
        self.sweep_confirm_bars = sweep_confirm_bars
        self._swing_highs: list[float] = []
        self._swing_lows: list[float] = []
        self.pools: list[LiquidityPool] = []
        self._pending_sweep: Optional[tuple[str, float, int]] = None

    def feed(self, kline: Kline) -> tuple[bool, bool]:
        """返回 (bull_sweep, bear_sweep) — 扫低后收回 / 扫高后收回."""
        if not kline.is_closed:
            return False, False
        h, l, c = float(kline.high), float(kline.low), float(kline.close)
        self._swing_highs.append(h)
        self._swing_lows.append(l)
        if len(self._swing_highs) > 100:
            self._swing_highs = self._swing_highs[-50:]
            self._swing_lows = self._swing_lows[-50:]

        self._update_pools()
        bull_sweep = bear_sweep = False

        for pool in self.pools:
            if pool.swept:
                continue
            if pool.kind == "lows" and l < pool.level and c > pool.level:
                pool.swept = True
                bull_sweep = True
            if pool.kind == "highs" and h > pool.level and c < pool.level:
                pool.swept = True
                bear_sweep = True

        return bull_sweep, bear_sweep

    def _update_pools(self) -> None:
        if len(self._swing_highs) < 5:
            return
        recent_h = self._swing_highs[-5:]
        recent_l = self._swing_lows[-5:]
        max_h = max(recent_h)
        min_l = min(recent_l)
        thr = self.equal_threshold_pct

        eq_highs = sum(1 for x in recent_h if abs(x - max_h) / max_h <= thr) >= 2
        eq_lows = sum(1 for x in recent_l if abs(x - min_l) / min_l <= thr) >= 2

        if eq_highs:
            self.pools.append(LiquidityPool(kind="highs", level=max_h))
        if eq_lows:
            self.pools.append(LiquidityPool(kind="lows", level=min_l))
        if len(self.pools) > 20:
            self.pools = self.pools[-20:]
