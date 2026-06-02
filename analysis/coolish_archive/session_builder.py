"""持仓回合（session）构建：净仓位归零或反手切分."""
from __future__ import annotations

import pandas as pd

from analysis.coolish_archive.settings import AnalysisConfig


def _sign_pos(pos: float, eps: float) -> int:
    if pos > eps:
        return 1
    if pos < -eps:
        return -1
    return 0


def build_sessions(trades: pd.DataFrame, cfg: AnalysisConfig) -> pd.DataFrame:
    """
    从成交重建持仓回合。
    使用 signed_home 累加作为 XBT 敞口；归零或反手结束一段。
    """
    sym = cfg.sessions.primary_symbol
    eps = cfg.sessions.position_flat_epsilon
    min_hold = cfg.sessions.min_hold_minutes / 60.0

    df = trades[trades["symbol"] == sym].copy()
    if df.empty:
        return pd.DataFrame()

    df = df.sort_values("timestamp").reset_index(drop=True)
    if "realisedPnl" in df.columns:
        df["realisedPnl"] = pd.to_numeric(df["realisedPnl"], errors="coerce").fillna(0)
        df["pnl_xbt"] = df["realisedPnl"] / 1e8
    else:
        df["pnl_xbt"] = 0.0

    if "lastPx" in df.columns:
        df["lastPx"] = pd.to_numeric(df["lastPx"], errors="coerce")

    sessions: list[dict] = []
    pos = 0.0
    sign = 0
    open_i: int | None = None
    session_id = 0

    def _close_session(close_i: int, reason: str) -> None:
        nonlocal session_id, open_i, sign, pos
        if open_i is None:
            return
        seg = df.iloc[open_i : close_i + 1]
        open_ts = seg["timestamp"].iloc[0]
        close_ts = seg["timestamp"].iloc[-1]
        hold_h = (close_ts - open_ts).total_seconds() / 3600.0
        if hold_h < min_hold:
            open_i = None
            sign = 0
            return

        side_label = "Long" if _sign_pos(float(seg["signed_home"].iloc[0]), eps) >= 0 else "Short"
        if sign > 0:
            side_label = "Long"
        elif sign < 0:
            side_label = "Short"

        open_px = seg["lastPx"].iloc[0] if pd.notna(seg["lastPx"].iloc[0]) else None
        close_px = seg["lastPx"].iloc[-1] if pd.notna(seg["lastPx"].iloc[-1]) else None

        sessions.append(
            {
                "session_id": session_id,
                "symbol": sym,
                "side": side_label,
                "open_time": open_ts,
                "close_time": close_ts,
                "hold_hours": hold_h,
                "fill_count": len(seg),
                "realised_pnl_xbt": float(seg["pnl_xbt"].sum()),
                "open_px": open_px,
                "close_px": close_px,
                "max_abs_position": float(seg["signed_home"].abs().cumsum().max()),
                "close_reason": reason,
            }
        )
        session_id += 1
        open_i = None
        sign = 0

    for i, row in df.iterrows():
        prev_sign = sign
        pos += float(row["signed_home"] or 0)
        sign = _sign_pos(pos, eps)

        if prev_sign == 0 and sign != 0:
            open_i = i
        elif prev_sign != 0 and sign == 0:
            _close_session(i, "flat")
            pos = 0.0
        elif prev_sign != 0 and sign != 0 and sign != prev_sign:
            _close_session(i, "flip")
            open_i = i
            sign = _sign_pos(pos, eps)

    if open_i is not None:
        _close_session(len(df) - 1, "end_of_data")
        sessions[-1]["close_reason"] = "still_open_or_end"

    out = pd.DataFrame(sessions)
    if not out.empty:
        out["year"] = out["open_time"].dt.year
        out["price_return_pct"] = _price_return_pct(out)
        out["outcome"] = out.apply(_classify_outcome, axis=1)
    return out


def _price_return_pct(df: pd.DataFrame) -> pd.Series:
    o = df["open_px"].astype(float)
    c = df["close_px"].astype(float)
    ret = (c - o) / o.replace(0, pd.NA) * 100
    short_mask = df["side"] == "Short"
    ret = ret.where(~short_mask, (o - c) / o.replace(0, pd.NA) * 100)
    return ret


def _classify_outcome(row: pd.Series) -> str:
    pnl = float(row.get("realised_pnl_xbt") or 0)
    if pnl > 1e-6:
        return "win"
    if pnl < -1e-6:
        return "loss"
    pr = row.get("price_return_pct")
    if pd.notna(pr):
        if pr > 0.05:
            return "win"
        if pr < -0.05:
            return "loss"
    return "flat"
