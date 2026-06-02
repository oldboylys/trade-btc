"""持仓回合开/平仓时刻对齐多周期指标."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from analysis.coolish_archive.indicator_series import FEATURE_COLS, load_indicator_series
from analysis.coolish_archive.settings import AnalysisConfig

VALUE_COLS = [c for c in FEATURE_COLS if c not in ("open_time",)]


def _merge_asof_features(
    sessions: pd.DataFrame,
    indicators: pd.DataFrame,
    time_col: str,
    prefix: str,
    interval: str,
) -> pd.DataFrame:
    left = sessions[[time_col, "session_id"]].copy()
    left = left.rename(columns={time_col: "event_time"})
    def _to_ns_utc(s: pd.Series) -> pd.Series:
        return pd.to_datetime(s, utc=True).astype("datetime64[ns, UTC]")

    left["event_time"] = _to_ns_utc(left["event_time"])

    cols = [c for c in VALUE_COLS if c in indicators.columns]
    right = indicators[["open_time"] + cols].copy()
    right["open_time"] = _to_ns_utc(right["open_time"])
    left = left.sort_values("event_time")
    right = right.sort_values("open_time")
    # 使用 K 线 open_time（已闭合 bar 起点）作为 asof 键
    merged = pd.merge_asof(
        left,
        right,
        left_on="event_time",
        right_on="open_time",
        direction="backward",
    )
    rename = {c: f"{prefix}_{interval}_{c}" for c in cols}
    merged = merged.rename(columns=rename)
    return merged.drop(columns=["open_time", "event_time"], errors="ignore")


def enrich_sessions(sessions: pd.DataFrame, cfg: AnalysisConfig) -> pd.DataFrame:
    if sessions.empty:
        return sessions

    enriched = sessions.copy()
    for interval in cfg.sessions.intervals:
        try:
            ind = load_indicator_series(cfg, interval)
        except Exception as e:
            print(f"  警告: 无法加载 {interval} 指标: {e}")
            continue
        if ind.empty:
            continue

        open_part = _merge_asof_features(sessions, ind, "open_time", "open", interval)
        close_part = _merge_asof_features(sessions, ind, "close_time", "close", interval)

        for col in open_part.columns:
            if col.startswith("open_"):
                enriched[col] = open_part[col].values
        for col in close_part.columns:
            if col.startswith("close_"):
                enriched[col] = close_part[col].values
        for c in [x.replace(f"open_{interval}_", "") for x in open_part.columns if x.startswith("open_")]:
            oc, cc = f"open_{interval}_{c}", f"close_{interval}_{c}"
            if oc in enriched.columns and cc in enriched.columns:
                enriched[f"delta_{interval}_{c}"] = enriched[cc] - enriched[oc]

    if "open_1h_ema20" in enriched.columns and "open_1h_ema50" in enriched.columns:
        enriched["open_1h_ema_spread_pct"] = (
            (enriched["open_1h_ema20"] - enriched["open_1h_ema50"])
            / enriched["open_1h_ema50"].replace(0, pd.NA)
            * 100
        )

    return enriched


def export_enriched(df: pd.DataFrame, cfg: AnalysisConfig) -> Path:
    cfg.ensure_output()
    path = cfg.output_dir / "sessions_enriched.parquet"
    try:
        df.to_parquet(path, index=False)
    except ImportError:
        path = cfg.output_dir / "sessions_enriched.csv"
        df.to_csv(path, index=False)
    return path
