"""从 Binance 下载 BTCUSDT 历史 K 线至 analysis 专用 SQLite."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import ssl
import time
from pathlib import Path

import aiohttp
import pandas as pd

from analysis.coolish_archive.settings import AnalysisConfig

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS klines (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    close_time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
);
CREATE INDEX IF NOT EXISTS idx_klines_range ON klines(symbol, interval, open_time);
"""

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
}

REST_ENDPOINTS = [
    "https://fapi.binance.com/fapi/v1/klines",
    "https://api.binance.com/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
]


def _ts_ms(iso: str) -> int:
    return int(pd.Timestamp(iso).timestamp() * 1000)


def _parse_rows(symbol: str, interval: str, data: list) -> list[tuple]:
    rows = []
    for k in data:
        rows.append(
            (
                symbol,
                interval,
                int(k[0]),
                int(k[6]),
                float(k[1]),
                float(k[2]),
                float(k[3]),
                float(k[4]),
                float(k[5]),
            )
        )
    return rows


async def _fetch_batch(
    session: aiohttp.ClientSession,
    url: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = 1500,
) -> list:
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit,
    }
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json()


async def download_klines(cfg: AnalysisConfig, intervals: list[str] | None = None) -> dict[str, int]:
    sess = cfg.sessions
    intervals = intervals or sess.intervals
    sess.klines_db.parent.mkdir(parents=True, exist_ok=True)

    start_ms = _ts_ms(sess.history_start)
    end_ms = _ts_ms(sess.history_end)

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    timeout = aiohttp.ClientTimeout(total=120, connect=30)

    conn = sqlite3.connect(sess.klines_db)
    conn.executescript(CREATE_SQL)
    counts: dict[str, int] = {}

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=ssl_ctx),
        timeout=timeout,
    ) as session:
        for interval in intervals:
            step = INTERVAL_MS[interval]
            existing_max = conn.execute(
                "SELECT MAX(open_time) FROM klines WHERE symbol=? AND interval=?",
                (sess.klines_symbol, interval),
            ).fetchone()[0]
            cursor = (existing_max + step) if existing_max else start_ms
            total = 0

            while cursor < end_ms:
                batch_data = None
                last_err: Exception | None = None
                for url in REST_ENDPOINTS:
                    try:
                        batch_data = await _fetch_batch(
                            session, url, sess.klines_symbol, interval,
                            cursor, end_ms, limit=1500,
                        )
                        break
                    except Exception as e:
                        last_err = e
                        await asyncio.sleep(0.5)

                if batch_data is None:
                    raise RuntimeError(f"所有端点失败 {interval} @ {cursor}: {last_err}")

                if not batch_data:
                    break

                rows = _parse_rows(sess.klines_symbol, interval, batch_data)
                conn.executemany(
                    """INSERT OR REPLACE INTO klines
                       (symbol, interval, open_time, close_time, open, high, low, close, volume)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                conn.commit()
                total += len(rows)
                last_open = int(batch_data[-1][0])
                if last_open <= cursor:
                    break
                cursor = last_open + step
                await asyncio.sleep(0.2)

            counts[interval] = total
            print(f"  {interval}: +{total} bars (total in db)")

    conn.close()
    return counts


def load_klines_df(cfg: AnalysisConfig, interval: str) -> pd.DataFrame:
    sess = cfg.sessions
    if not sess.klines_db.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(sess.klines_db)
    df = pd.read_sql_query(
        """SELECT open_time, close_time, open, high, low, close, volume
           FROM klines WHERE symbol=? AND interval=? ORDER BY open_time""",
        conn,
        params=(sess.klines_symbol, interval),
    )
    conn.close()
    if df.empty:
        return df
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df


def main() -> int:
    cfg = AnalysisConfig.load()
    print(f"K线库: {cfg.sessions.klines_db}")
    counts = asyncio.run(download_klines(cfg))
    print("完成:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
