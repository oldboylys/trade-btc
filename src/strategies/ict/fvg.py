"""Fair Value Gap 检测与回踩."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.core.models import Kline


@dataclass
class FairValueGap:
    direction: str  # bull | bear
    top: float
    bottom: float
    mid: float
    bar_time: int
    active: bool = True


class FVGTracker:
    def __init__(self, min_gap_pct: float = 0.001, max_active: int = 20) -> None:
        self.min_gap_pct = min_gap_pct
        self.max_active = max_active
        self._bars: list[Kline] = []
        self.gaps: list[FairValueGap] = []

    def feed(self, kline: Kline) -> list[FairValueGap]:
        if not kline.is_closed:
            return [g for g in self.gaps if g.active]
        self._bars.append(kline)
        if len(self._bars) >= 3:
            self._detect(self._bars[-3], self._bars[-2], self._bars[-1])
        self._mitigate(float(kline.close))
        if len(self._bars) > 500:
            self._bars = self._bars[-200:]
        return [g for g in self.gaps if g.active]

    def _detect(self, b0: Kline, b1: Kline, b2: Kline) -> None:
        h0, l2 = float(b0.high), float(b2.low)
        l0, h2 = float(b0.low), float(b2.high)
        mid = (float(b1.close) + float(b1.open)) / 2
        ref = mid if mid > 0 else float(b1.close)

        if l2 > h0:
            gap = (l2 - h0) / ref
            if gap >= self.min_gap_pct:
                g = FairValueGap(
                    direction="bull",
                    top=l2,
                    bottom=h0,
                    mid=(l2 + h0) / 2,
                    bar_time=b2.open_time,
                )
                self._add(g)
        if h2 < l0:
            gap = (l0 - h2) / ref
            if gap >= self.min_gap_pct:
                g = FairValueGap(
                    direction="bear",
                    top=l0,
                    bottom=h2,
                    mid=(l0 + h2) / 2,
                    bar_time=b2.open_time,
                )
                self._add(g)

    def _add(self, gap: FairValueGap) -> None:
        self.gaps.append(gap)
        if len(self.gaps) > self.max_active:
            self.gaps = self.gaps[-self.max_active :]

    def _mitigate(self, close: float) -> None:
        for g in self.gaps:
            if not g.active:
                continue
            if g.direction == "bull" and close <= g.bottom:
                g.active = False
            elif g.direction == "bear" and close >= g.top:
                g.active = False

    def touch_mid_bull(self, low: float, close: float) -> Optional[FairValueGap]:
        for g in self.gaps:
            if g.active and g.direction == "bull" and low <= g.mid <= close:
                return g
        return None

    def touch_mid_bear(self, high: float, close: float) -> Optional[FairValueGap]:
        for g in self.gaps:
            if g.active and g.direction == "bear" and close <= g.mid <= high:
                return g
        return None
