"""下载历史 K 线至 marketdata.db（支持断点续传）."""
from __future__ import annotations

import argparse
import asyncio
import datetime
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.core.config import load_config
from src.marketdata.history_download import download_history, verify_coverage
from src.marketdata.storage import MarketDataStorage


def _parse_date(s: str) -> int:
    dt = datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 Binance 现货历史 K 线")
    parser.add_argument("--config-dir", default="config", help="配置目录")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--symbol", default=None, help="交易对，默认读 config")
    parser.add_argument(
        "--intervals",
        default=None,
        help="周期列表，逗号分隔，如 5m,1h",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="仅检查覆盖率，不下载",
    )
    args = parser.parse_args()

    cfg = load_config(args.config_dir)
    bt_cfg = cfg.get("backtest", {})
    md_cfg = cfg.get("market_data", {})

    symbol = args.symbol or bt_cfg.get("symbol") or md_cfg.get("symbols", ["BTCUSDT"])[0]
    intervals_raw = args.intervals or ",".join(bt_cfg.get("intervals", ["5m", "1h"]))
    intervals = [i.strip() for i in intervals_raw.split(",") if i.strip()]
    db_path = bt_cfg.get("db_path") or md_cfg.get("db_path", "data/marketdata.db")

    start_str = args.start or bt_cfg.get("start", "2024-06-01")
    start_ms = _parse_date(start_str)

    if args.end:
        end_ms = _parse_date(args.end)
    elif bt_cfg.get("end"):
        end_ms = _parse_date(bt_cfg["end"])
    else:
        today = datetime.datetime.now(datetime.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ) + datetime.timedelta(days=1)
        end_ms = int(today.timestamp() * 1000)

    print(f"下载 {symbol} {intervals} | {start_str} ~ end_ms={end_ms} -> {db_path}")

    async def _run() -> None:
        storage = MarketDataStorage(db_path)
        await storage.connect()
        try:
            if args.verify_only:
                print("=== 覆盖率检查 ===")
                for iv in intervals:
                    r = await verify_coverage(storage, symbol, iv, start_ms, end_ms)
                    status = "完整" if r["complete"] else f"缺失 {r['missing']:,} 根 ({r['gap_segments']} 段缺口)"
                    print(
                        f"  {iv}: {r['actual']:,}/{r['expected']:,} ({r['coverage_pct']}%) — {status}"
                    )
                return

            counts = await download_history(db_path, symbol, intervals, start_ms, end_ms)
            print("\n=== 下载完成 ===")
            for iv, n in counts.items():
                print(f"  {iv}: 本次新增 {n:,} 根")

            print("\n=== 覆盖率验证 ===")
            for iv in intervals:
                r = await verify_coverage(storage, symbol, iv, start_ms, end_ms)
                status = "完整" if r["complete"] else f"缺失 {r['missing']:,} 根，请重跑继续填补"
                print(
                    f"  {iv}: {r['actual']:,}/{r['expected']:,} ({r['coverage_pct']}%) — {status}"
                )
        finally:
            await storage.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
