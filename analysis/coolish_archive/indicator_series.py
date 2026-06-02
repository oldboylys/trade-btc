"""全量 K 线批量计算技术指标（对齐 IndicatorPipeline / TV 默认参数）."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from analysis.coolish_archive.klines_download import load_klines_df
from analysis.coolish_archive.settings import AnalysisConfig

try:
    import pandas_ta as ta

    _HAS_TA = True
except ImportError:
    _HAS_TA = False

FEATURE_COLS = [
    "open_time",
    "close",
    "ema20",
    "ema50",
    "ema200",
    "macd",
    "macd_signal",
    "macd_hist",
    "rsi14",
    "bb_upper",
    "bb_mid",
    "bb_lower",
    "bb_pct",
    "atr14",
    "stoch_k",
    "stoch_d",
    "vol_ratio",
    "trend_bullish",
]


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """对 OHLCV DataFrame 计算指标列."""
    if len(df) < 30:
        return pd.DataFrame()

    out = df[["open_time", "close"]].copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    if _HAS_TA:
        ema20 = ta.ema(close, length=20)
        ema50 = ta.ema(close, length=50)
        ema200 = ta.ema(close, length=min(200, len(df) - 1))
        out["ema20"] = ema20
        out["ema50"] = ema50
        out["ema200"] = ema200

        macd = ta.macd(close, fast=12, slow=26, signal=9)
        if macd is not None:
            out["macd"] = macd["MACD_12_26_9"]
            out["macd_signal"] = macd["MACDs_12_26_9"]
            out["macd_hist"] = macd["MACDh_12_26_9"]

        out["rsi14"] = ta.rsi(close, length=14)

        bb = ta.bbands(close, length=20)
        if bb is not None:
            out["bb_upper"] = bb["BBU_20_2.0"]
            out["bb_mid"] = bb["BBM_20_2.0"]
            out["bb_lower"] = bb["BBL_20_2.0"]
            bw = out["bb_upper"] - out["bb_lower"]
            out["bb_pct"] = (close - out["bb_lower"]) / bw.replace(0, np.nan)

        out["atr14"] = ta.atr(high, low, close, length=14)

        stoch = ta.stoch(high, low, close)
        if stoch is not None:
            out["stoch_k"] = stoch["STOCHk_14_3_3"]
            out["stoch_d"] = stoch["STOCHd_14_3_3"]
    else:
        out["ema20"] = close.ewm(span=20, adjust=False).mean()
        out["ema50"] = close.ewm(span=50, adjust=False).mean()
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        out["rsi14"] = 100 - (100 / (1 + rs))
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        out["bb_upper"] = ma20 + 2 * std20
        out["bb_lower"] = ma20 - 2 * std20
        out["bb_mid"] = ma20
        bw = out["bb_upper"] - out["bb_lower"]
        out["bb_pct"] = (close - out["bb_lower"]) / bw.replace(0, np.nan)
        out["atr14"] = (high - low).rolling(14).mean()
        out["macd_hist"] = np.nan

    vol_ma = volume.rolling(20).mean()
    out["vol_ratio"] = volume / vol_ma.replace(0, np.nan)
    out["trend_bullish"] = (out["ema20"] > out["ema50"]).astype(float)

    return out


def build_all_indicator_series(cfg: AnalysisConfig) -> dict[str, Path]:
    """计算并缓存各周期指标 Parquet。"""
    cfg.ensure_indicators_dir()
    paths: dict[str, Path] = {}

    for interval in cfg.sessions.intervals:
        klines = load_klines_df(cfg, interval)
        if klines.empty:
            print(f"  跳过 {interval}: 无 K 线，请先 download-klines")
            continue
        feats = compute_features(klines)
        out_path = cfg.indicators_dir() / f"indicators_{interval}.parquet"
        feats.to_parquet(out_path, index=False)
        paths[interval] = out_path
        print(f"  {interval}: {len(feats)} 行 -> {out_path.name}")

    return paths


def load_indicator_series(cfg: AnalysisConfig, interval: str) -> pd.DataFrame:
    path = cfg.indicators_dir() / f"indicators_{interval}.parquet"
    if not path.exists():
        build_all_indicator_series(cfg)
    df = pd.read_parquet(path)
    if not pd.api.types.is_datetime64_any_dtype(df["open_time"]):
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    return df


def main() -> int:
    cfg = AnalysisConfig.load()
    build_all_indicator_series(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
