"""Premium / Discount 区间（dealing range）."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.core.models import Kline


class Zone(str, Enum):
    PREMIUM = "premium"
    DISCOUNT = "discount"
    EQUILIBRIUM = "equilibrium"


@dataclass
class RangeState:
    high: float = 0.0
    low: float = 0.0
    equilibrium: float = 0.0
    ote_low: float = 0.62
    ote_high: float = 0.79


class PremiumDiscount:
    def __init__(self, ote_low: float = 0.62, ote_high: float = 0.79) -> None:
        self.ote_low = ote_low
        self.ote_high = ote_high
        self._state = RangeState(ote_low=ote_low, ote_high=ote_high)
        self._highs: list[float] = []
        self._lows: list[float] = []

    @property
    def state(self) -> RangeState:
        return self._state

    def feed(self, kline: Kline) -> RangeState:
        if not kline.is_closed:
            return self._state
        self._highs.append(float(kline.high))
        self._lows.append(float(kline.low))
        if len(self._highs) > 200:
            self._highs = self._highs[-100:]
            self._lows = self._lows[-100:]
        if len(self._highs) < 10:
            return self._state
        rh = max(self._highs[-50:])
        rl = min(self._lows[-50:])
        if rh > rl:
            self._state.high = rh
            self._state.low = rl
            self._state.equilibrium = (rh + rl) / 2
        return self._state

    def zone(self, price: float) -> Zone:
        st = self._state
        if st.high <= st.low:
            return Zone.EQUILIBRIUM
        fib = (price - st.low) / (st.high - st.low)
        if fib >= self.ote_high:
            return Zone.PREMIUM
        if fib <= self.ote_low:
            return Zone.DISCOUNT
        return Zone.EQUILIBRIUM

    def allows_long(self, price: float) -> bool:
        return self.zone(price) in (Zone.DISCOUNT, Zone.EQUILIBRIUM)

    def allows_short(self, price: float) -> bool:
        return self.zone(price) in (Zone.PREMIUM, Zone.EQUILIBRIUM)
