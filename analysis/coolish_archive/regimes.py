"""权益曲线与分周期 / 回撤分析."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from analysis.coolish_archive.settings import AnalysisConfig


def compute_regime_stats(
    cfg: AnalysisConfig,
    equity: pd.DataFrame,
    wallet: pd.DataFrame,
    daily_exposure: pd.DataFrame,
    tier1: dict[str, Any],
) -> dict[str, Any]:
    eq = equity.dropna(subset=["adjustedWealthMultipleVsBaseline"]).copy()
    if eq.empty:
        return {"error": "no_equity_data"}

    mult = eq["adjustedWealthMultipleVsBaseline"]
    peak = mult.cummax()
    dd = (mult - peak) / peak * 100

    drawdowns = _find_drawdown_episodes(eq, mult, dd)
    withdrawals = _withdrawal_alignment(wallet, eq)
    regime_compare = _regime_compare(cfg, tier1, daily_exposure)
    dd_behavior = _drawdown_exposure_behavior(drawdowns, daily_exposure)

    return {
        "equity_summary": {
            "baseline_multiple": round(float(mult.iloc[0]), 4) if len(mult) else None,
            "latest_multiple": round(float(mult.iloc[-1]), 4),
            "max_multiple": round(float(mult.max()), 4),
            "max_drawdown_pct": round(float(dd.min()), 2),
        },
        "drawdown_episodes": drawdowns[:10],
        "withdrawal_alignment": withdrawals,
        "regime_compare": regime_compare,
        "drawdown_exposure_behavior": dd_behavior,
    }


def _find_drawdown_episodes(
    eq: pd.DataFrame, mult: pd.Series, dd: pd.Series, threshold_pct: float = -15.0
) -> list[dict[str, Any]]:
    episodes: list[dict[str, Any]] = []
    in_dd = False
    start_i = 0
    trough_i = 0
    trough_dd = 0.0

    for i in range(len(dd)):
        if not in_dd and dd.iloc[i] <= threshold_pct:
            in_dd = True
            start_i = i
            trough_i = i
            trough_dd = dd.iloc[i]
        elif in_dd:
            if dd.iloc[i] < trough_dd:
                trough_dd = dd.iloc[i]
                trough_i = i
            if dd.iloc[i] >= -5.0 and i > trough_i:
                episodes.append(
                    {
                        "start": str(eq["timestamp"].iloc[start_i]),
                        "trough": str(eq["timestamp"].iloc[trough_i]),
                        "end": str(eq["timestamp"].iloc[i]),
                        "max_drawdown_pct": round(float(trough_dd), 2),
                        "recovery_days": (eq["timestamp"].iloc[i] - eq["timestamp"].iloc[trough_i]).days,
                    }
                )
                in_dd = False

    episodes.sort(key=lambda x: x["max_drawdown_pct"])
    return episodes


def _wallet_xbt_amount(df: pd.DataFrame) -> pd.Series:
    cur = df.get("currency", pd.Series(["XBt"] * len(df)))
    return df["amount"].astype(float).abs() / 1e8


def _withdrawal_alignment(wallet: pd.DataFrame, equity: pd.DataFrame) -> dict[str, Any]:
    w = wallet[wallet["transactType"] == "Withdrawal"].copy()
    if w.empty:
        return {"withdrawal_count": 0}
    w = w[w["transactStatus"] == "Completed"] if "transactStatus" in w.columns else w
    eq_peaks = equity.copy()
    eq_peaks["peak_mult"] = eq_peaks["adjustedWealthMultipleVsBaseline"].cummax()
    merged = pd.merge_asof(
        w.sort_values("timestamp"),
        eq_peaks[["timestamp", "adjustedWealthMultipleVsBaseline", "peak_mult"]].sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    near_peak = merged[
        merged["adjustedWealthMultipleVsBaseline"] >= merged["peak_mult"] * 0.95
    ]
    return {
        "withdrawal_count": int(len(w)),
        "withdrawal_total_xbt": round(float(_wallet_xbt_amount(w).sum()), 4),
        "withdrawals_near_equity_peak_pct": round(
            float(len(near_peak) / len(merged) * 100) if len(merged) else 0, 2
        ),
    }


def _regime_compare(
    cfg: AnalysisConfig,
    tier1: dict[str, Any],
    daily_exposure: pd.DataFrame,
) -> list[dict[str, Any]]:
    results = []
    yearly = {r["year"]: r for r in tier1.get("yearly_breakdown", {}).get("by_year", [])}
    for regime in cfg.regime_years:
        label = regime["label"]
        start = pd.Timestamp(regime["start"], tz="UTC")
        end = pd.Timestamp(regime["end"], tz="UTC")
        years = range(start.year, end.year + 1)
        trades_years = [yearly[y] for y in years if y in yearly]
        exp = pd.DataFrame()
        if not daily_exposure.empty and "date" in daily_exposure.columns:
            d = daily_exposure.copy()
            d["date"] = pd.to_datetime(d["date"], utc=True)
            exp = d[(d["date"] >= start) & (d["date"] <= end)]
        long_days = 0
        if not exp.empty:
            long_days = int((exp["exposure_side"] == "Long").sum())
            total_days = len(exp)
            long_day_pct = round(long_days / total_days * 100, 2) if total_days else 0
        else:
            long_day_pct = None
        results.append(
            {
                "regime": label,
                "years": list(years),
                "yearly_trades": trades_years,
                "long_exposure_day_pct": long_day_pct,
            }
        )
    return results


def _drawdown_exposure_behavior(
    drawdowns: list[dict[str, Any]],
    daily_exposure: pd.DataFrame,
) -> dict[str, Any]:
    if not drawdowns or daily_exposure.empty:
        return {"verdict": "insufficient_data"}
    worst = drawdowns[0]
    start = pd.Timestamp(worst["start"], tz="UTC")
    end = pd.Timestamp(worst.get("end", worst["trough"]), tz="UTC")
    d = daily_exposure.copy()
    d["date"] = pd.to_datetime(d["date"], utc=True)
    window = d[(d["date"] >= start) & (d["date"] <= end)]
    if window.empty:
        return {"verdict": "insufficient_data"}
    net_reduce = window["net_signed_home"].sum() < 0
    avg_side = window["exposure_side"].mode().iloc[0] if len(window) else "Unknown"
    return {
        "worst_drawdown_pct": worst["max_drawdown_pct"],
        "window_days": len(window),
        "net_signed_home_sum": round(float(window["net_signed_home"].sum()), 6),
        "predominant_side": str(avg_side),
        "verdict": "reduce_exposure" if net_reduce else "hold_through",
    }
