"""由成交重建持仓与持有周期."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PositionStats:
    hold_sessions: pd.DataFrame
    daily_exposure: pd.DataFrame
    flips_per_year: dict[int, int]
    summary: dict


def rebuild_positions(trades: pd.DataFrame, primary_symbol: str = "XBTUSD") -> PositionStats:
    """
    按 symbol 重建持仓；默认聚焦 XBTUSD。
    使用 signed_home 累加作为 XBT 敞口代理。
    """
    sym_trades = trades[trades["symbol"] == primary_symbol].copy()
    if sym_trades.empty:
        sym_trades = trades[trades["is_btc"]].copy()
        if sym_trades.empty:
            return PositionStats(
                hold_sessions=pd.DataFrame(),
                daily_exposure=pd.DataFrame(),
                flips_per_year={},
                summary={"error": "no_btc_trades"},
            )
        primary_symbol = sym_trades["symbol"].value_counts().index[0]
        sym_trades = trades[trades["symbol"] == primary_symbol].copy()

    sym_trades = sym_trades.sort_values("timestamp")
    sym_trades["cum_position"] = sym_trades["signed_home"].cumsum()

    sessions = _extract_hold_sessions(sym_trades, primary_symbol)
    daily = _daily_exposure(sym_trades)
    flips = _count_flips(sym_trades)

    hold_hours = sessions["hold_hours"] if len(sessions) else pd.Series(dtype=float)
    summary = {
        "primary_symbol": primary_symbol,
        "trade_count": len(sym_trades),
        "hold_session_count": len(sessions),
        "hold_hours_median": float(hold_hours.median()) if len(hold_hours) else None,
        "hold_hours_p90": float(hold_hours.quantile(0.9)) if len(hold_hours) else None,
        "hold_hours_mean": float(hold_hours.mean()) if len(hold_hours) else None,
        "hold_days_median": float(hold_hours.median() / 24) if len(hold_hours) else None,
        "pct_hold_over_24h": float((hold_hours > 24).mean() * 100) if len(hold_hours) else None,
        "pct_hold_over_168h": float((hold_hours > 168).mean() * 100) if len(hold_hours) else None,
        "flip_count_total": int(sessions["is_flip"].sum()) if "is_flip" in sessions.columns else 0,
    }
    return PositionStats(
        hold_sessions=sessions,
        daily_exposure=daily,
        flips_per_year=flips,
        summary=summary,
    )


def _extract_hold_sessions(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """持仓从开到平（或反手）的会话."""
    rows: list[dict] = []
    pos = 0.0
    open_ts = None
    open_side = None
    prev_sign = 0

    for _, r in df.iterrows():
        prev_pos = pos
        pos += float(r["signed_home"] or 0)
        ts = r["timestamp"]
        sign = 1 if pos > 1e-8 else (-1 if pos < -1e-8 else 0)

        if prev_sign == 0 and sign != 0:
            open_ts = ts
            open_side = "Long" if sign > 0 else "Short"
        elif prev_sign != 0 and sign == 0:
            if open_ts is not None:
                rows.append(_session_row(symbol, open_ts, ts, open_side, prev_sign, is_flip=False))
            open_ts = None
            open_side = None
        elif prev_sign != 0 and sign != 0 and sign != prev_sign:
            if open_ts is not None:
                rows.append(_session_row(symbol, open_ts, ts, open_side, prev_sign, is_flip=True))
            open_ts = ts
            open_side = "Long" if sign > 0 else "Short"
        prev_sign = sign

    if open_ts is not None and len(df):
        rows.append(
            _session_row(
                symbol, open_ts, df["timestamp"].iloc[-1], open_side, prev_sign, is_flip=False, open_end=True
            )
        )

    return pd.DataFrame(rows)


def _session_row(
    symbol: str,
    open_ts: pd.Timestamp,
    close_ts: pd.Timestamp,
    side: str | None,
    sign: int,
    is_flip: bool,
    open_end: bool = False,
) -> dict:
    hours = (close_ts - open_ts).total_seconds() / 3600.0
    return {
        "symbol": symbol,
        "side": side,
        "open_time": open_ts,
        "close_time": close_ts,
        "hold_hours": hours,
        "is_flip": is_flip,
        "still_open": open_end,
    }


def _daily_exposure(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = df["timestamp"].dt.floor("D")
    daily = (
        df.groupby("date")
        .agg(
            net_signed_home=("signed_home", "sum"),
            trade_count=("signed_home", "count"),
            last_cum_position=("cum_position", "last"),
        )
        .reset_index()
    )
    daily["exposure_side"] = np.where(
        daily["last_cum_position"] > 1e-8,
        "Long",
        np.where(daily["last_cum_position"] < -1e-8, "Short", "Flat"),
    )
    return daily


def _count_flips(df: pd.DataFrame) -> dict[int, int]:
    df = df.copy()
    sign = np.sign(df["cum_position"].values)
    sign[np.abs(df["cum_position"].values) < 1e-8] = 0
    flips = (np.diff(sign) != 0) & (sign[1:] != 0) & (sign[:-1] != 0)
    df["flip"] = False
    if len(flips):
        df.iloc[1: len(flips) + 1, df.columns.get_loc("flip")] = flips
    yearly = df[df["flip"]].groupby(df["timestamp"].dt.year).size()
    return {int(k): int(v) for k, v in yearly.items()}
