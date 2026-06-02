"""当无法访问 Binance 时，用 XBTUSD 成交 lastPx 重采样为 OHLCV（仅供研究流水线）."""
from __future__ import annotations

import sqlite3

import pandas as pd

from analysis.coolish_archive.klines_download import CREATE_SQL, INTERVAL_MS
from analysis.coolish_archive.settings import AnalysisConfig

PANDAS_FREQ = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
}


def build_klines_from_trades(trades: pd.DataFrame, cfg: AnalysisConfig) -> dict[str, int]:
    sym = cfg.sessions.primary_symbol
    df = trades[trades["symbol"] == sym].copy()
    if df.empty or "lastPx" not in df.columns:
        return {}

    df["lastPx"] = pd.to_numeric(df["lastPx"], errors="coerce")
    df = df.dropna(subset=["timestamp", "lastPx"])
    df = df.set_index("timestamp").sort_index()

    cfg.sessions.klines_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.sessions.klines_db)
    conn.executescript(CREATE_SQL)

    counts: dict[str, int] = {}
    for interval in cfg.sessions.intervals:
        freq = PANDAS_FREQ.get(interval)
        if not freq:
            continue
        ohlc = df["lastPx"].resample(freq).ohlc()
        vol = df["lastPx"].resample(freq).count()
        bars = ohlc.dropna(subset=["close"])
        if bars.empty:
            continue

        rows = []
        step = INTERVAL_MS[interval]
        for ts, row in bars.iterrows():
            open_ms = int(ts.timestamp() * 1000)
            rows.append(
                (
                    cfg.sessions.klines_symbol,
                    interval,
                    open_ms,
                    open_ms + step - 1,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(vol.get(ts, 1)),
                )
            )

        conn.execute(
            "DELETE FROM klines WHERE symbol=? AND interval=?",
            (cfg.sessions.klines_symbol, interval),
        )
        conn.executemany(
            """INSERT OR REPLACE INTO klines
               (symbol, interval, open_time, close_time, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        counts[interval] = len(rows)
        print(f"  {interval}: {len(rows)} bars (from trades resample)")

    conn.close()
    return counts
