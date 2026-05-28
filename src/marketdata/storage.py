"""K线与成交数据落盘（SQLite via aiosqlite）."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiosqlite

from src.core.models import Exchange, Kline


CREATE_KLINES_SQL = """
CREATE TABLE IF NOT EXISTS klines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    exchange    TEXT    NOT NULL,
    interval    TEXT    NOT NULL,
    open_time   INTEGER NOT NULL,
    close_time  INTEGER NOT NULL,
    open        TEXT    NOT NULL,
    high        TEXT    NOT NULL,
    low         TEXT    NOT NULL,
    close       TEXT    NOT NULL,
    volume      TEXT    NOT NULL,
    quote_volume TEXT   NOT NULL,
    num_trades  INTEGER NOT NULL,
    is_closed   INTEGER NOT NULL DEFAULT 1,
    UNIQUE(symbol, exchange, interval, open_time)
);
CREATE INDEX IF NOT EXISTS idx_klines_lookup
    ON klines(symbol, exchange, interval, open_time);
"""


class MarketDataStorage:
    def __init__(self, db_path: str = "data/marketdata.db") -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.executescript(CREATE_KLINES_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def save_kline(self, kline: Kline) -> None:
        assert self._db, "Not connected"
        async with self._lock:
            await self._db.execute(
                """INSERT OR REPLACE INTO klines
                   (symbol,exchange,interval,open_time,close_time,
                    open,high,low,close,volume,quote_volume,num_trades,is_closed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    kline.symbol, kline.exchange.value, kline.interval,
                    kline.open_time, kline.close_time,
                    str(kline.open), str(kline.high), str(kline.low),
                    str(kline.close), str(kline.volume), str(kline.quote_volume),
                    kline.num_trades, int(kline.is_closed),
                ),
            )
            await self._db.commit()

    async def save_klines_bulk(self, klines: list[Kline]) -> None:
        if not klines:
            return
        assert self._db, "Not connected"
        rows = [
            (
                k.symbol, k.exchange.value, k.interval,
                k.open_time, k.close_time,
                str(k.open), str(k.high), str(k.low),
                str(k.close), str(k.volume), str(k.quote_volume),
                k.num_trades, int(k.is_closed),
            )
            for k in klines
        ]
        async with self._lock:
            await self._db.executemany(
                """INSERT OR REPLACE INTO klines
                   (symbol,exchange,interval,open_time,close_time,
                    open,high,low,close,volume,quote_volume,num_trades,is_closed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            await self._db.commit()

    async def load_klines(
        self,
        symbol: str,
        exchange: Exchange,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> list[Kline]:
        assert self._db, "Not connected"
        query = (
            "SELECT symbol,exchange,interval,open_time,close_time,"
            "open,high,low,close,volume,quote_volume,num_trades,is_closed "
            "FROM klines WHERE symbol=? AND exchange=? AND interval=?"
        )
        params: list[Any] = [symbol, exchange.value, interval]
        if start_ms is not None:
            query += " AND open_time >= ?"
            params.append(start_ms)
        if end_ms is not None:
            query += " AND open_time <= ?"
            params.append(end_ms)
        query += " ORDER BY open_time"
        if limit is not None:
            query += f" LIMIT {limit}"

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        return [
            Kline(
                symbol=row[0],
                exchange=Exchange(row[1]),
                interval=row[2],
                open_time=row[3],
                close_time=row[4],
                open=Decimal(row[5]),
                high=Decimal(row[6]),
                low=Decimal(row[7]),
                close=Decimal(row[8]),
                volume=Decimal(row[9]),
                quote_volume=Decimal(row[10]),
                num_trades=row[11],
                is_closed=bool(row[12]),
            )
            for row in rows
        ]
