"""历史数据缺口扫描测试."""
from __future__ import annotations

import pytest

from src.core.models import Exchange, Kline
from src.marketdata.storage import MarketDataStorage


@pytest.mark.asyncio
async def test_find_coverage_gaps_middle_hole(tmp_path):
    db = str(tmp_path / "t.db")
    storage = MarketDataStorage(db)
    await storage.connect()

    step = 300_000
    base = 1_000_000
    klines = []
    for i in list(range(0, 10)) + list(range(20, 30)):
        ot = base + i * step
        klines.append(Kline(
            symbol="BTCUSDT", exchange=Exchange.BINANCE, interval="5m",
            open_time=ot, close_time=ot + step - 1,
            open=1, high=1, low=1, close=1, volume=1, quote_volume=1,
            num_trades=1, is_closed=True,
        ))
    await storage.save_klines_bulk(klines)

    start = base
    end = base + 30 * step
    gaps = await storage.find_coverage_gaps(
        "BTCUSDT", Exchange.BINANCE, "5m", start, end, step,
    )
    assert len(gaps) == 1
    assert gaps[0][0] == base + 10 * step
    assert gaps[0][1] == base + 20 * step
    await storage.close()
