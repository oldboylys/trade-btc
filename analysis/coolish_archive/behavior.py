"""Tier1 行为统计."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from analysis.coolish_archive.positions import PositionStats


def compute_tier1_stats(
    trades: pd.DataFrame,
    orders: pd.DataFrame,
    wallet: pd.DataFrame,
    equity: pd.DataFrame,
    pos_stats: PositionStats,
    btc_symbols: list[str],
) -> dict[str, Any]:
    btc_mask = trades["is_btc"]
    btc_trades = trades[btc_mask]
    all_trades = trades

    # 跨品种用 foreignNotional（USD）可比；homeNotional 单位因合约而异
    notional_col = "foreignNotional"
    if notional_col not in all_trades.columns:
        all_trades["_usd_notional"] = all_trades["signed_home"].abs()
        notional_col = "_usd_notional"
    else:
        all_trades = all_trades.copy()
        all_trades["_usd_notional"] = all_trades[notional_col].abs()

    symbol_dist = (
        all_trades.groupby("symbol")["_usd_notional"]
        .sum()
        .sort_values(ascending=False)
    )
    total_notional = symbol_dist.sum()
    symbol_pct = (symbol_dist / total_notional * 100).head(15).to_dict() if total_notional else {}

    btc_usd = all_trades.loc[btc_mask, "_usd_notional"].sum()
    all_usd = all_trades["_usd_notional"].sum()
    btc_usd_share = float(btc_usd / all_usd * 100) if all_usd else 0.0

    side_counts = btc_trades["side"].value_counts(normalize=True).to_dict()
    buy_pct = float(side_counts.get("Buy", 0) * 100)

    ord_type = {}
    if "ordType" in btc_trades.columns:
        ord_type = btc_trades["ordType"].value_counts(normalize=True).to_dict()
    limit_pct = float(ord_type.get("Limit", 0) * 100) if ord_type else None

    maker_stats = _liquidity_stats(btc_trades)
    partial_pct = _partial_fill_rate(btc_trades)

    yearly = _yearly_breakdown(btc_trades, btc_symbols)
    size_vs_equity = _size_vs_equity(btc_trades, equity)
    funding = _funding_stats(wallet)

    order_stats = _order_stats(orders)

    return {
        "symbol_concentration_pct": {k: round(v, 2) for k, v in symbol_pct.items()},
        "btc_trade_count": int(len(btc_trades)),
        "total_trade_count": int(len(all_trades)),
        "btc_notional_share_pct": round(btc_usd_share, 2),
        "xbtusd_notional_share_pct": round(
            float(
                all_trades.loc[all_trades["symbol"] == "XBTUSD", "_usd_notional"].sum()
                / all_usd
                * 100
            )
            if all_usd
            else 0,
            2,
        ),
        "buy_side_pct": round(buy_pct, 2),
        "sell_side_pct": round(100 - buy_pct, 2),
        "ord_type_distribution": {k: round(v * 100, 2) for k, v in ord_type.items()},
        "limit_order_pct": round(limit_pct, 2) if limit_pct is not None else None,
        "liquidity": maker_stats,
        "partial_fill_rate_pct": round(partial_pct, 2),
        "position": pos_stats.summary,
        "flips_per_year": pos_stats.flips_per_year,
        "yearly_breakdown": yearly,
        "size_vs_equity": size_vs_equity,
        "funding": funding,
        "order_stats": order_stats,
    }


def _liquidity_stats(btc_trades: pd.DataFrame) -> dict[str, Any]:
    liq = btc_trades["lastLiquidityInd"].dropna()
    if liq.empty:
        return {}
    vc = liq.value_counts(normalize=True)
    maker = float(vc.get("AddedLiquidity", 0) * 100)
    taker = float(vc.get("RemovedLiquidity", 0) * 100)
    return {
        "added_liquidity_pct": round(maker, 2),
        "removed_liquidity_pct": round(taker, 2),
        "maker_dominant": maker > 50,
    }


def _partial_fill_rate(btc_trades: pd.DataFrame) -> float:
    if "ordStatus" not in btc_trades.columns:
        return 0.0
    partial = btc_trades["ordStatus"].isin(["PartiallyFilled", "PartiallyFilled"]).sum()
    return float(partial / len(btc_trades) * 100) if len(btc_trades) else 0.0


def _yearly_breakdown(btc_trades: pd.DataFrame, btc_symbols: list[str]) -> dict[str, Any]:
    rows = []
    for year, g in btc_trades.groupby("year"):
        buy = (g["side"] == "Buy").mean() * 100
        rows.append(
            {
                "year": int(year),
                "trades": len(g),
                "buy_pct": round(float(buy), 2),
                "xbtusd_share_pct": round(
                    float((g["symbol"] == "XBTUSD").mean() * 100), 2
                ),
            }
        )
    return {"by_year": rows}


def _size_vs_equity(btc_trades: pd.DataFrame, equity: pd.DataFrame) -> dict[str, Any]:
    if equity.empty or "adjustedWealthXBT" not in equity.columns:
        return {}
    eq = equity[["timestamp", "adjustedWealthXBT"]].dropna()
    t = btc_trades[["timestamp", "signed_home"]].copy()
    t["abs_home"] = t["signed_home"].abs()
    merged = pd.merge_asof(
        t.sort_values("timestamp"),
        eq.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    merged = merged[merged["adjustedWealthXBT"] > 0]
    if merged.empty:
        return {}
    merged["notional_pct_equity"] = merged["abs_home"] / merged["adjustedWealthXBT"] * 100
    return {
        "median_trade_pct_equity": round(float(merged["notional_pct_equity"].median()), 4),
        "p90_trade_pct_equity": round(float(merged["notional_pct_equity"].quantile(0.9)), 4),
        "max_trade_pct_equity": round(float(merged["notional_pct_equity"].max()), 4),
    }


def _xbt_amount(series: pd.Series, currency: pd.Series) -> pd.Series:
    """BitMEX 钱包 amount 为 satoshi（1e-8 XBT）。"""
    out = series.astype(float) / 1e8
    return out.where(currency.str.upper().isin(["XBT", "XBTUSD"]), series.astype(float))


def _funding_stats(wallet: pd.DataFrame) -> dict[str, Any]:
    if "transactType" not in wallet.columns:
        return {}
    funding = wallet[wallet["transactType"] == "Funding"].copy()
    if funding.empty:
        return {"funding_events": 0}
    cur = funding.get("currency", pd.Series(["XBt"] * len(funding)))
    amt_xbt = _xbt_amount(funding["amount"], cur).sum()
    return {
        "funding_events": int(len(funding)),
        "funding_total_xbt": round(float(amt_xbt), 6),
        "funding_as_pct_of_realised": None,
    }


def _order_stats(orders: pd.DataFrame) -> dict[str, Any]:
    if orders.empty:
        return {}
    ord_type = orders["ordType"].value_counts(normalize=True).to_dict()
    stop_used = float(orders["stopPx"].notna().sum() / len(orders) * 100) if "stopPx" in orders.columns else 0
    return {
        "order_count": len(orders),
        "ord_type_pct": {k: round(v * 100, 2) for k, v in ord_type.items()},
        "stop_px_used_pct": round(stop_used, 2),
    }
