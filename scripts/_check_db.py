import sqlite3
import datetime

c = sqlite3.connect("data/marketdata.db")
print("=== Kline counts ===")
for row in c.execute(
    "SELECT interval, COUNT(*), MIN(open_time), MAX(open_time) "
    "FROM klines WHERE symbol='BTCUSDT' AND interval IN ('5m','1h') GROUP BY interval"
):
    iv, cnt, mn, mx = row
    d0 = datetime.datetime.utcfromtimestamp(mn / 1000).strftime("%Y-%m-%d")
    d1 = datetime.datetime.utcfromtimestamp(mx / 1000).strftime("%Y-%m-%d")
    print(f"  {iv}: {cnt:,} bars  ({d0} ~ {d1})")
