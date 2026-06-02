"""持仓回合统计聚类与画像."""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from analysis.coolish_archive.settings import AnalysisConfig

OPEN_1H_FEATURES = [
    "open_1h_rsi14",
    "open_1h_macd_hist",
    "open_1h_bb_pct",
    "open_1h_trend_bullish",
    "open_1h_vol_ratio",
]


def run_session_analytics(enriched: pd.DataFrame, cfg: AnalysisConfig) -> dict[str, Any]:
    if enriched.empty:
        return {"error": "no_sessions"}

    result: dict[str, Any] = {
        "session_count": len(enriched),
        "summary": _basic_summary(enriched),
        "win_loss_profile": _win_loss_profile(enriched),
        "long_short_profile": _long_short_profile(enriched),
        "yearly_evolution": _yearly_evolution(enriched),
        "clusters": _cluster_sessions(enriched),
        "h_hypothesis_checks": _hypothesis_checks(enriched),
    }
    return result


def _basic_summary(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "hold_hours_median": float(df["hold_hours"].median()),
        "hold_hours_p90": float(df["hold_hours"].quantile(0.9)),
        "win_rate_pct": float((df["outcome"] == "win").mean() * 100),
        "loss_rate_pct": float((df["outcome"] == "loss").mean() * 100),
        "total_pnl_xbt": float(df["realised_pnl_xbt"].sum()),
        "avg_pnl_xbt": float(df["realised_pnl_xbt"].mean()),
    }


def _win_loss_profile(df: pd.DataFrame) -> dict[str, Any]:
    profiles = {}
    for label in ("win", "loss", "flat"):
        sub = df[df["outcome"] == label]
        if sub.empty:
            continue
        entry: dict[str, Any] = {"count": len(sub)}
        for col in OPEN_1H_FEATURES:
            if col in sub.columns:
                entry[col.replace("open_1h_", "")] = {
                    "median": round(float(sub[col].median()), 4),
                    "mean": round(float(sub[col].mean()), 4),
                }
        if "open_1h_trend_bullish" in sub.columns:
            entry["trend_bullish_pct"] = round(
                float(sub["open_1h_trend_bullish"].mean() * 100), 2
            )
        profiles[label] = entry
    return profiles


def _long_short_profile(df: pd.DataFrame) -> dict[str, Any]:
    out = {}
    for side in ("Long", "Short"):
        sub = df[df["side"] == side]
        if sub.empty:
            continue
        out[side] = {
            "count": len(sub),
            "win_rate_pct": round(float((sub["outcome"] == "win").mean() * 100), 2),
            "median_hold_hours": round(float(sub["hold_hours"].median()), 2),
        }
    return out


def _yearly_evolution(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for year, g in df.groupby("year"):
        row: dict[str, Any] = {"year": int(year), "sessions": len(g)}
        if "open_1h_rsi14" in g.columns:
            row["open_rsi14_median"] = round(float(g["open_1h_rsi14"].median()), 2)
        if "open_1h_trend_bullish" in g.columns:
            row["trend_bullish_pct"] = round(
                float(g["open_1h_trend_bullish"].mean() * 100), 2
            )
        row["win_rate_pct"] = round(float((g["outcome"] == "win").mean() * 100), 2)
        rows.append(row)
    return rows


def _cluster_sessions(df: pd.DataFrame, k: int = 5) -> dict[str, Any]:
    cols = [c for c in OPEN_1H_FEATURES if c in df.columns]
    if len(cols) < 2:
        return {"error": "insufficient_features"}

    use_cols = [c for c in cols if df[c].notna().sum() >= len(df) * 0.5]
    if len(use_cols) < 2:
        use_cols = [c for c in cols if df[c].notna().sum() > k * 3]
    if len(use_cols) < 2:
        return {"error": "insufficient_features", "cols": cols}

    mat = df[use_cols].astype(float).fillna(df[use_cols].median())
    if len(mat) < k * 3:
        return {"error": "too_few_sessions", "n": len(mat)}
    cols = use_cols

    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler

        X = StandardScaler().fit_transform(mat)
        labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(X)
    except ImportError:
        # 无 sklearn：按 RSI 分位数分桶
        rsi_col = "open_1h_rsi14" if "open_1h_rsi14" in mat.columns else cols[0]
        labels = pd.qcut(mat[rsi_col], q=k, labels=False, duplicates="drop")
        if labels is None or len(np.unique(labels)) < 2:
            return {"error": "sklearn_not_installed", "hint": "pip install scikit-learn"}
    mat = mat.copy()
    mat["cluster"] = labels

    clusters = []
    for cid in range(k):
        sub = mat[mat["cluster"] == cid]
        sig: dict[str, Any] = {"cluster_id": cid, "count": len(sub)}
        for c in cols:
            short = c.replace("open_1h_", "")
            sig[short] = round(float(sub[c].mean()), 4)
        if "open_1h_trend_bullish" in cols:
            sig["trend_bullish_pct"] = round(
                float(sub["open_1h_trend_bullish"].mean() * 100), 2
            )
        clusters.append(sig)

    return {"k": k, "clusters": clusters}


def _hypothesis_checks(df: pd.DataFrame) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["pct_hold_over_24h"] = round(
        float((df["hold_hours"] > 24).mean() * 100), 2
    )
    if "open_1h_trend_bullish" in df.columns:
        checks["open_trend_bullish_pct"] = round(
            float(df["open_1h_trend_bullish"].mean() * 100), 2
        )
    if "open_1h_rsi14" in df.columns:
        checks["open_rsi14_median"] = round(float(df["open_1h_rsi14"].median()), 2)
    win = df[df["outcome"] == "win"]
    loss = df[df["outcome"] == "loss"]
    if not win.empty and not loss.empty and "open_1h_rsi14" in df.columns:
        checks["rsi14_median_win"] = round(float(win["open_1h_rsi14"].median()), 2)
        checks["rsi14_median_loss"] = round(float(loss["open_1h_rsi14"].median()), 2)
    return checks


def write_analytics_json(analytics: dict[str, Any], cfg: AnalysisConfig) -> None:
    path = cfg.output_dir / "session_analytics.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(analytics, f, ensure_ascii=False, indent=2, default=str)
