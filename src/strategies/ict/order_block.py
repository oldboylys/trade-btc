"""Order Block：位移前最后一根反向蜡烛."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.core.models import Kline


@dataclass
class OrderBlock:
    direction: str  # bull | bear
    high: float
    low: float
    bar_time: int
    age_bars: int = 0
    active: bool = True


class OrderBlockTracker:
    def __init__(
        self,
        displacement_atr_mult: float = 1.5,
        max_age_bars: int = 48,
    ) -> None:
        self.displacement_atr_mult = displacement_atr_mult
        self.max_age_bars = max_age_bars
        self._bars: list[Kline] = []
        self.blocks: list[OrderBlock] = []

    def feed(self, kline: Kline, atr: float = 0.0) -> list[OrderBlock]:
        if not kline.is_closed:
            return [b for b in self.blocks if b.active]
        self._bars.append(kline)
        for b in self.blocks:
            if b.active:
                b.age_bars += 1
                if b.age_bars > self.max_age_bars:
                    b.active = False

        if len(self._bars) >= 2 and atr > 0:
            prev, cur = self._bars[-2], self._bars[-1]
            body = abs(float(cur.close) - float(cur.open))
            if body >= self.displacement_atr_mult * atr:
                ob_bar = prev
                if float(cur.close) > float(cur.open):
                    self._add(OrderBlock(
                        direction="bull",
                        high=float(ob_bar.high),
                        low=float(ob_bar.low),
                        bar_time=ob_bar.open_time,
                    ))
                else:
                    self._add(OrderBlock(
                        direction="bear",
                        high=float(ob_bar.high),
                        low=float(ob_bar.low),
                        bar_time=ob_bar.open_time,
                    ))

        close = float(kline.close)
        for b in self.blocks:
            if not b.active:
                continue
            if b.direction == "bull" and close < b.low:
                b.active = False
            elif b.direction == "bear" and close > b.high:
                b.active = False

        if len(self._bars) > 300:
            self._bars = self._bars[-150:]
        return [b for b in self.blocks if b.active]

    def _add(self, ob: OrderBlock) -> None:
        self.blocks.append(ob)
        if len(self.blocks) > 30:
            self.blocks = self.blocks[-30:]

    def test_bull(self, low: float, close: float) -> Optional[OrderBlock]:
        for b in self.blocks:
            if b.active and b.direction == "bull" and b.low <= low <= b.high:
                return b
        return None

    def test_bear(self, high: float, close: float) -> Optional[OrderBlock]:
        for b in self.blocks:
            if b.active and b.direction == "bear" and b.low <= high <= b.high:
                return b
        return None
