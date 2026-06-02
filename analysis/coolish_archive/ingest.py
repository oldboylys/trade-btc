"""CSV 读取与清洗."""
from __future__ import annotations

from typing import Iterator

import pandas as pd

from analysis.coolish_archive.settings import AnalysisConfig

TRADE_DTYPES = {
    "symbol": "string",
    "side": "string",
    "execType": "string",
    "ordType": "string",
    "ordStatus": "string",
    "lastLiquidityInd": "string",
    "orderID": "string",
}

TRADE_USECOLS = [
    "timestamp",
    "transactTime",
    "execType",
    "ordStatus",
    "symbol",
    "side",
    "orderQty",
    "lastQty",
    "leavesQty",
    "price",
    "lastPx",
    "avgPx",
    "settlCurrency",
    "execCost",
    "execComm",
    "realisedPnl",
    "homeNotional",
    "foreignNotional",
    "orderID",
    "execID",
    "ordType",
    "lastLiquidityInd",
    "stopPx",
    "timeInForce",
]


def _parse_ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def load_trades(cfg: AnalysisConfig) -> pd.DataFrame:
    """加载成交账本，仅保留 Trade 类型."""
    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        cfg.trade_history,
        usecols=lambda c: c in TRADE_USECOLS,
        chunksize=cfg.chunksize,
        low_memory=False,
    ):
        chunk = chunk[chunk["execType"] == "Trade"].copy()
        if chunk.empty:
            continue
        chunk["timestamp"] = _parse_ts(chunk["timestamp"])
        chunk["transactTime"] = _parse_ts(chunk["transactTime"])
        for col in ("lastQty", "homeNotional", "foreignNotional", "lastPx", "price", "execComm"):
            if col in chunk.columns:
                chunk[col] = pd.to_numeric(chunk[col], errors="coerce")
        chunks.append(chunk)

    if not chunks:
        return pd.DataFrame()
    df = pd.concat(chunks, ignore_index=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["signed_home"] = df.apply(_signed_home_notional, axis=1)
    df["year"] = df["timestamp"].dt.year
    df["is_btc"] = df["symbol"].isin(cfg.btc_symbols) | df["symbol"].str.startswith("XBT", na=False)
    return df


def _signed_home_notional(row: pd.Series) -> float:
    h = row.get("homeNotional")
    if pd.isna(h):
        return 0.0
    h = float(h)
    if row.get("side") == "Sell":
        return -abs(h) if h > 0 else h
    return abs(h) if h >= 0 else h


def load_orders(cfg: AnalysisConfig) -> pd.DataFrame:
    df = pd.read_csv(cfg.orders, low_memory=False)
    df["timestamp"] = _parse_ts(df["timestamp"])
    df["transactTime"] = _parse_ts(df["transactTime"])
    for col in ("orderQty", "cumQty", "leavesQty", "price", "avgPx", "stopPx"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["year"] = df["timestamp"].dt.year
    return df.sort_values("timestamp").reset_index(drop=True)


def load_wallet(cfg: AnalysisConfig) -> pd.DataFrame:
    df = pd.read_csv(cfg.wallet_history, low_memory=False)
    df["timestamp"] = _parse_ts(df["timestamp"])
    df["transactTime"] = _parse_ts(df["transactTime"])
    df["amount"] = pd.to_numeric(df.get("amount"), errors="coerce")
    df["fee"] = pd.to_numeric(df.get("fee"), errors="coerce")
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def load_equity(cfg: AnalysisConfig) -> pd.DataFrame:
    df = pd.read_csv(cfg.equity_curve, low_memory=False)
    df["timestamp"] = _parse_ts(df["timestamp"])
    for col in (
        "adjustedWealthXBT",
        "adjustedWealthMultipleVsBaseline",
        "adjustedMarkedWealthXBT",
        "walletBalanceXBTEquivalent",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("timestamp").reset_index(drop=True)


def sanity_check(cfg: AnalysisConfig, trades: pd.DataFrame) -> dict:
    manifest = cfg.load_manifest()
    th_meta = next(
        f for f in manifest["files"] if f["file"] == "api-v1-execution-tradeHistory.csv"
    )
    return {
        "trade_rows_loaded": len(trades),
        "trade_rows_manifest": th_meta["rows"],
        "note": "loaded rows are execType=Trade only; manifest includes all exec types",
        "first_time": str(trades["timestamp"].min()) if len(trades) else None,
        "last_time": str(trades["timestamp"].max()) if len(trades) else None,
        "manifest_first": th_meta.get("first_time"),
        "manifest_last": th_meta.get("last_time"),
    }
