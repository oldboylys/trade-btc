"""市场结构：摆动点、BOS、CHoCH、趋势偏向."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.core.models import Kline


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class StructureState:
    bias: Bias = Bias.NEUTRAL
    last_swing_high: float = 0.0
    last_swing_low: float = 0.0
    bos_bull: bool = False
    bos_bear: bool = False
    choch_bull: bool = False
    choch_bear: bool = False


class StructureTracker:
    """Fractal 摆动 + BOS/CHoCH."""

    def __init__(self, swing_lookback: int = 3) -> None:
        self.swing_lookback = swing_lookback
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._closes: list[float] = []
        self._state = StructureState()
        self._prev_bias = Bias.NEUTRAL

    @property
    def state(self) -> StructureState:
        return self._state

    def feed(self, kline: Kline) -> StructureState:
        if not kline.is_closed:
            return self._state
        h, l, c = float(kline.high), float(kline.low), float(kline.close)
        self._highs.append(h)
        self._lows.append(l)
        self._closes.append(c)
        n = self.swing_lookback
        if len(self._highs) < 2 * n + 1:
            return self._state

        idx = len(self._highs) - n - 1
        window_h = self._highs[idx - n : idx + n + 1]
        window_l = self._lows[idx - n : idx + n + 1]
        is_swing_high = self._highs[idx] == max(window_h)
        is_swing_low = self._lows[idx] == min(window_l)

        if is_swing_high:
            self._state.last_swing_high = self._highs[idx]
        if is_swing_low:
            self._state.last_swing_low = self._lows[idx]

        close = c
        sh, sl = self._state.last_swing_high, self._state.last_swing_low
        self._state.bos_bull = False
        self._state.bos_bear = False
        self._state.choch_bull = False
        self._state.choch_bear = False

        if sh > 0 and close > sh:
            if self._prev_bias == Bias.BEARISH:
                self._state.choch_bull = True
            else:
                self._state.bos_bull = True
            self._prev_bias = Bias.BULLISH
            self._state.bias = Bias.BULLISH
        elif sl > 0 and close < sl:
            if self._prev_bias == Bias.BULLISH:
                self._state.choch_bear = True
            else:
                self._state.bos_bear = True
            self._prev_bias = Bias.BEARISH
            self._state.bias = Bias.BEARISH

        return self._state
