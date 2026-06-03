"""Volume Profile：POC / Value Area / HVN / LVN."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from src.core.models import Kline


@dataclass
class ProfileLevels:
    poc: float
    vah: float
    val: float
    hvn: list[float]
    lvn: list[float]
    total_volume: float
    bar_count: int


class VolumeProfileBuilder:
    """滚动窗口价格-成交量分布."""

    def __init__(
        self,
        lookback_bars: int = 288,
        value_area_pct: float = 0.70,
        bin_count: int = 50,
        tick_size: float = 10.0,
        hvn_ratio: float = 1.35,
        lvn_ratio: float = 0.55,
    ) -> None:
        self.lookback_bars = lookback_bars
        self.value_area_pct = value_area_pct
        self.bin_count = bin_count
        self.tick_size = tick_size
        self.hvn_ratio = hvn_ratio
        self.lvn_ratio = lvn_ratio
        self._bars: deque[Kline] = deque(maxlen=lookback_bars)
        self._last: Optional[ProfileLevels] = None

    def feed(self, kline: Kline) -> Optional[ProfileLevels]:
        if not kline.is_closed:
            return self._last
        self._bars.append(kline)
        if len(self._bars) < max(10, self.lookback_bars // 4):
            return self._last
        self._last = self._compute()
        return self._last

    @property
    def levels(self) -> Optional[ProfileLevels]:
        return self._last

    def _compute(self) -> ProfileLevels:
        prices = []
        volumes = []
        for bar in self._bars:
            tp = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
            vol = float(bar.volume) or float(bar.num_trades) or 1.0
            prices.append(tp)
            volumes.append(vol)

        lo = min(prices)
        hi = max(prices)
        if hi <= lo:
            hi = lo + self.tick_size

        n_bins = max(self.bin_count, 10)
        width = max(self.tick_size, (hi - lo) / n_bins)
        bins = [0.0] * n_bins
        bin_centers = [lo + (i + 0.5) * width for i in range(n_bins)]

        for p, v in zip(prices, volumes):
            idx = min(n_bins - 1, max(0, int((p - lo) / width)))
            bins[idx] += v

        total_vol = sum(bins) or 1.0
        poc_idx = max(range(n_bins), key=lambda i: bins[i])
        poc = bin_centers[poc_idx]

        target_va = total_vol * self.value_area_pct
        acc = bins[poc_idx]
        left, right = poc_idx, poc_idx
        while acc < target_va and (left > 0 or right < n_bins - 1):
            vol_left = bins[left - 1] if left > 0 else -1.0
            vol_right = bins[right + 1] if right < n_bins - 1 else -1.0
            if vol_right >= vol_left:
                right += 1
                acc += bins[right]
            else:
                left -= 1
                acc += bins[left]

        val = bin_centers[left]
        vah = bin_centers[right]
        avg_bin = total_vol / n_bins
        hvn = [bin_centers[i] for i in range(n_bins) if bins[i] >= avg_bin * self.hvn_ratio]
        lvn = [bin_centers[i] for i in range(n_bins) if 0 < bins[i] <= avg_bin * self.lvn_ratio]

        return ProfileLevels(
            poc=poc,
            vah=vah,
            val=val,
            hvn=hvn,
            lvn=lvn,
            total_volume=total_vol,
            bar_count=len(self._bars),
        )
