"""从 5m K 线聚合 1h 并写入 DB（填补历史缺口）."""
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.core.models import Exchange, Kline
from src.marketdata.storage import MarketDataStorage

HOUR_MS = 3_600_000


def aggregate_5m_to_1h(klines_5m: list[Kline]) -> list[Kline]:
    if not klines_5m:
        return []
    buckets: dict[int, list[Kline]] = {}
    for k in klines_5m:
        hour_start = (k.open_time // HOUR_MS) * HOUR_MS
        buckets.setdefault(hour_start, []).append(k)

    out: list[Kline] = []
    for hour_start in sorted(buckets):
        bars = buckets[hour_start]
        if len(bars) < 10:
            continue
        out.append(Kline(
            symbol=bars[0].symbol,
            exchange=bars[0].exchange,
            interval="1h",
            open_time=hour_start,
            close_time=hour_start + HOUR_MS - 1,
            open=bars[0].open,
            high=max(b.high for b in bars),
            low=min(b.low for b in bars),
            close=bars[-1].close,
            volume=sum(b.volume for b in bars),
            quote_volume=sum(b.quote_volume for b in bars),
            num_trades=sum(b.num_trades for b in bars),
            is_closed=True,
        ))
    return out


async def main(start_ms: int, end_ms: int) -> int:
    storage = MarketDataStorage("data/marketdata.db")
    await storage.connect()
    k5 = await storage.load_klines(
        "BTCUSDT", Exchange.BINANCE, "5m", start_ms=start_ms, end_ms=end_ms,
    )
    k1 = aggregate_5m_to_1h(k5)
    await storage.save_klines_bulk(k1)
    await storage.close()
    print(f"Aggregated {len(k1)} 1h bars from {len(k5)} 5m bars")
    return len(k1)


if __name__ == "__main__":
    # 2024-06-01 ~ 2024-06-05 连续 5m 段
    asyncio.run(main(1717200000000, 1717500000000))
