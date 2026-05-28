"""多周期指标流水线：维护多个周期的 DataFrame 并计算技术指标."""
from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Any

import pandas as pd

from src.core.models import Kline

try:
    import pandas_ta as ta  # type: ignore
    _HAS_PANDAS_TA = True
except ImportError:
    _HAS_PANDAS_TA = False


class IndicatorPipeline:
    """
    为单个 symbol/exchange 维护多周期 OHLCV 缓冲区，
    并在每根 K线闭合时计算并缓存技术指标。
    """

    REQUIRED_COLS = ["open", "high", "low", "close", "volume"]

    def __init__(
        self,
        intervals: list[str],
        max_bars: int = 500,
    ) -> None:
        self.intervals = intervals
        self.max_bars = max_bars

        # interval -> deque of dicts
        self._bars: dict[str, deque[dict]] = {
            iv: deque(maxlen=max_bars) for iv in intervals
        }
        # interval -> 最新计算出的特征 dict
        self._features: dict[str, dict[str, float]] = {iv: {} for iv in intervals}

    def feed(self, kline: Kline) -> dict[str, float]:
        """喂入一根闭合K线，更新指标，返回该周期最新特征."""
        if kline.interval not in self._bars:
            return {}
        self._bars[kline.interval].append({
            "ts": kline.open_time,
            "open": float(kline.open),
            "high": float(kline.high),
            "low": float(kline.low),
            "close": float(kline.close),
            "volume": float(kline.volume),
        })
        features = self._compute(kline.interval)
        self._features[kline.interval] = features
        return features

    def get_features(self, interval: str) -> dict[str, float]:
        return self._features.get(interval, {})

    def get_all_features(self) -> dict[str, dict[str, float]]:
        return dict(self._features)

    def _compute(self, interval: str) -> dict[str, float]:
        bars = list(self._bars[interval])
        if len(bars) < 30:
            return {}

        df = pd.DataFrame(bars)
        df.set_index("ts", inplace=True)

        feats: dict[str, float] = {}

        if _HAS_PANDAS_TA:
            feats.update(self._compute_pandas_ta(df, interval))
        else:
            feats.update(self._compute_manual(df))

        return feats

    def _compute_pandas_ta(self, df: pd.DataFrame, interval: str) -> dict[str, float]:
        feats: dict[str, float] = {}

        # 趋势
        ema20 = ta.ema(df["close"], length=20)
        ema50 = ta.ema(df["close"], length=50)
        ema200 = ta.ema(df["close"], length=min(200, len(df) - 1))
        if ema20 is not None and len(ema20):
            feats["ema20"] = float(ema20.iloc[-1])
        if ema50 is not None and len(ema50):
            feats["ema50"] = float(ema50.iloc[-1])
        if ema200 is not None and len(ema200):
            feats["ema200"] = float(ema200.iloc[-1])

        # MACD
        macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd is not None and len(macd):
            feats["macd"] = float(macd["MACD_12_26_9"].iloc[-1])
            feats["macd_signal"] = float(macd["MACDs_12_26_9"].iloc[-1])
            feats["macd_hist"] = float(macd["MACDh_12_26_9"].iloc[-1])

        # RSI
        rsi = ta.rsi(df["close"], length=14)
        if rsi is not None and len(rsi):
            feats["rsi14"] = float(rsi.iloc[-1])

        # Bollinger Bands
        bb = ta.bbands(df["close"], length=20)
        if bb is not None and len(bb):
            feats["bb_upper"] = float(bb["BBU_20_2.0"].iloc[-1])
            feats["bb_lower"] = float(bb["BBL_20_2.0"].iloc[-1])
            feats["bb_mid"] = float(bb["BBM_20_2.0"].iloc[-1])
            close = float(df["close"].iloc[-1])
            bw = feats["bb_upper"] - feats["bb_lower"]
            feats["bb_pct"] = (close - feats["bb_lower"]) / bw if bw > 0 else 0.5

        # ATR
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)
        if atr is not None and len(atr):
            feats["atr14"] = float(atr.iloc[-1])

        # Volume MA
        vol_ma20 = df["volume"].rolling(20).mean()
        if len(vol_ma20):
            feats["vol_ma20"] = float(vol_ma20.iloc[-1])
            feats["vol_ratio"] = float(df["volume"].iloc[-1] / feats["vol_ma20"]) \
                if feats["vol_ma20"] > 0 else 1.0

        # Stochastic
        stoch = ta.stoch(df["high"], df["low"], df["close"])
        if stoch is not None and len(stoch):
            feats["stoch_k"] = float(stoch["STOCHk_14_3_3"].iloc[-1])
            feats["stoch_d"] = float(stoch["STOCHd_14_3_3"].iloc[-1])

        feats["close"] = float(df["close"].iloc[-1])
        return feats

    def _compute_manual(self, df: pd.DataFrame) -> dict[str, float]:
        """不依赖 pandas-ta 的基础指标（备用）."""
        feats: dict[str, float] = {}
        close = df["close"]
        feats["close"] = float(close.iloc[-1])

        # EMA
        def ema(s: pd.Series, n: int) -> float:
            return float(s.ewm(span=n, adjust=False).mean().iloc[-1])

        feats["ema20"] = ema(close, 20)
        feats["ema50"] = ema(close, 50)

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        feats["rsi14"] = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        # BB
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        feats["bb_upper"] = float(ma20.iloc[-1] + 2 * std20.iloc[-1])
        feats["bb_lower"] = float(ma20.iloc[-1] - 2 * std20.iloc[-1])
        feats["bb_mid"] = float(ma20.iloc[-1])
        bw = feats["bb_upper"] - feats["bb_lower"]
        feats["bb_pct"] = (feats["close"] - feats["bb_lower"]) / bw if bw > 0 else 0.5

        feats["atr14"] = float(
            (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
        )
        return feats
