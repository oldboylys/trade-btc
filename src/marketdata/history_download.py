"""从 Binance 分页下载历史 K 线并写入 MarketDataStorage."""
from __future__ import annotations

import asyncio
import json
import ssl
import uuid
from decimal import Decimal

import aiohttp
import websockets

from src.core.logging import get_logger
from src.core.models import Exchange, Kline
from src.marketdata.storage import MarketDataStorage

logger = get_logger("marketdata.history_download")

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

REST_ENDPOINTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
]

BATCH_SIZE = 1500
WS_API_URL = "wss://ws-api.binance.com:443/ws-api/v3"
MAX_BATCH_RETRIES = 12


def _parse_kline_row(symbol: str, interval: str, row: list) -> Kline:
    return Kline(
        symbol=symbol,
        exchange=Exchange.BINANCE,
        interval=interval,
        open_time=int(row[0]),
        close_time=int(row[6]),
        open=Decimal(row[1]),
        high=Decimal(row[2]),
        low=Decimal(row[3]),
        close=Decimal(row[4]),
        volume=Decimal(row[5]),
        quote_volume=Decimal(row[7]),
        num_trades=int(row[8]),
        is_closed=True,
    )


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def expected_bar_count(start_ms: int, end_ms: int, step_ms: int) -> int:
    if end_ms <= start_ms:
        return 0
    return (end_ms - start_ms) // step_ms


async def _fetch_batch_rest(
    session: aiohttp.ClientSession,
    base_url: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = BATCH_SIZE,
) -> list:
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit,
    }
    url = f"{base_url}/api/v3/klines"
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _fetch_batch_ws_api(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = BATCH_SIZE,
) -> list:
    payload = {
        "id": str(uuid.uuid4())[:8],
        "method": "klines",
        "params": {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": limit,
        },
    }
    async with websockets.connect(
        WS_API_URL, ssl=_ssl_ctx(), open_timeout=30, close_timeout=10,
    ) as ws:
        await ws.send(json.dumps(payload))
        raw = await asyncio.wait_for(ws.recv(), timeout=45)
    resp = json.loads(raw)
    if resp.get("status") != 200:
        raise RuntimeError(f"ws_api error: {resp.get('error')}")
    return resp["result"]


async def _fetch_batch(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list:
    last_err: Exception | None = None
    for base in REST_ENDPOINTS:
        try:
            return await _fetch_batch_rest(
                session, base, symbol, interval, start_ms, end_ms, BATCH_SIZE,
            )
        except Exception as exc:
            last_err = exc
            await asyncio.sleep(0.25)

    try:
        return await _fetch_batch_ws_api(symbol, interval, start_ms, end_ms, BATCH_SIZE)
    except Exception as exc:
        raise RuntimeError(f"All endpoints failed @ {start_ms}: {last_err}; ws={exc}") from exc


async def _download_range(
    storage: MarketDataStorage,
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    range_start: int,
    range_end: int,
) -> int:
    step = INTERVAL_MS[interval]
    cursor = range_start
    total = 0
    stall_count = 0

    while cursor < range_end:
        batch_data = None
        for attempt in range(MAX_BATCH_RETRIES):
            try:
                batch_data = await _fetch_batch(session, symbol, interval, cursor, range_end)
                stall_count = 0
                break
            except Exception as exc:
                wait = min(2 ** attempt, 60)
                logger.warning(
                    "history_batch_retry",
                    interval=interval,
                    cursor=cursor,
                    attempt=attempt + 1,
                    error=str(exc),
                    wait_s=wait,
                )
                await asyncio.sleep(wait)

        if batch_data is None:
            logger.error("history_batch_abandoned", interval=interval, cursor=cursor)
            break

        if not batch_data:
            break

        klines = [_parse_kline_row(symbol, interval, row) for row in batch_data]
        await storage.save_klines_bulk(klines)
        total += len(klines)

        last_open = int(batch_data[-1][0])
        next_cursor = last_open + step
        if next_cursor <= cursor:
            stall_count += 1
            if stall_count >= 3:
                break
            cursor += step
        else:
            cursor = next_cursor

        if total % 5000 < BATCH_SIZE or len(batch_data) < BATCH_SIZE:
            logger.info(
                "history_batch_saved",
                interval=interval,
                batch=len(klines),
                range_total=total,
                last_open=last_open,
            )
        await asyncio.sleep(0.1)

    return total


async def _download_interval(
    storage: MarketDataStorage,
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> int:
    step = INTERVAL_MS.get(interval)
    if step is None:
        raise ValueError(f"Unsupported interval: {interval}")

    gaps = await storage.find_coverage_gaps(
        symbol, Exchange.BINANCE, interval, start_ms, end_ms, step,
    )
    expected = expected_bar_count(start_ms, end_ms, step)
    actual_before = await storage.count_klines(
        symbol, Exchange.BINANCE, interval, start_ms, end_ms,
    )

    logger.info(
        "history_coverage_scan",
        interval=interval,
        expected_bars=expected,
        actual_bars=actual_before,
        missing_bars=max(0, expected - actual_before),
        gap_segments=len(gaps),
    )

    total = 0
    for idx, (gap_start, gap_end) in enumerate(gaps, start=1):
        if gap_end <= gap_start:
            continue
        est_bars = expected_bar_count(gap_start, gap_end, step)
        logger.info(
            "history_fill_gap",
            interval=interval,
            segment=idx,
            total_segments=len(gaps),
            from_ms=gap_start,
            to_ms=gap_end,
            est_bars=est_bars,
        )
        total += await _download_range(
            storage, session, symbol, interval, gap_start, gap_end,
        )

    return total


async def verify_coverage(
    storage: MarketDataStorage,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> dict:
    step = INTERVAL_MS[interval]
    expected = expected_bar_count(start_ms, end_ms, step)
    actual = await storage.count_klines(
        symbol, Exchange.BINANCE, interval, start_ms, end_ms,
    )
    gaps = await storage.find_coverage_gaps(
        symbol, Exchange.BINANCE, interval, start_ms, end_ms, step,
    )
    missing = max(0, expected - actual)
    pct = (actual / expected * 100) if expected > 0 else 100.0
    return {
        "interval": interval,
        "expected": expected,
        "actual": actual,
        "missing": missing,
        "coverage_pct": round(pct, 2),
        "gap_segments": len(gaps),
        "complete": missing == 0 and len(gaps) == 0,
    }


async def download_history(
    db_path: str,
    symbol: str,
    intervals: list[str],
    start_ms: int,
    end_ms: int,
) -> dict[str, int]:
    """分页下载历史 K 线至 marketdata.db，自动扫描并填补中间缺口."""
    storage = MarketDataStorage(db_path)
    await storage.connect()

    timeout = aiohttp.ClientTimeout(total=180, connect=60, sock_read=120)

    counts: dict[str, int] = {}
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_ssl_ctx(), limit=10),
            timeout=timeout,
        ) as session:
            for interval in intervals:
                n = await _download_interval(
                    storage, session, symbol, interval, start_ms, end_ms,
                )
                counts[interval] = n
                report = await verify_coverage(storage, symbol, interval, start_ms, end_ms)
                logger.info(
                    "history_interval_done",
                    interval=interval,
                    new_bars=n,
                    coverage=report,
                )
    finally:
        await storage.close()

    return counts
