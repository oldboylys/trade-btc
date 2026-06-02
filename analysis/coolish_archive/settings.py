"""加载分析配置与路径."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent.parent


@dataclass
class SessionsConfig:
    primary_symbol: str
    min_hold_minutes: float
    klines_symbol: str
    klines_exchange: str
    klines_db: Path
    intervals: list[str]
    klines_mode: str
    klines_source: str
    session_window_buffer_hours: int
    history_start: str
    history_end: str
    position_flat_epsilon: float


@dataclass
class AnalysisConfig:
    data_dir: Path
    output_dir: Path
    trade_history: Path
    orders: Path
    wallet_history: Path
    equity_curve: Path
    manifest: Path
    btc_symbols: list[str]
    chunksize: int
    regime_years: list[dict[str, str]]
    sessions: SessionsConfig
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, config_path: Path | None = None) -> AnalysisConfig:
        config_path = config_path or _PKG_DIR / "config.yaml"
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        env_data = os.environ.get("COOLISH_DATA_DIR")
        data_dir = Path(env_data) if env_data else (_PKG_DIR / raw["data_dir"]).resolve()
        if not data_dir.is_absolute():
            data_dir = (_PKG_DIR / raw["data_dir"]).resolve()

        env_out = os.environ.get("COOLISH_OUTPUT_DIR")
        output_dir = Path(env_out) if env_out else (_PKG_DIR / raw["output_dir"]).resolve()
        if not env_out and not output_dir.is_absolute():
            output_dir = (_PKG_DIR / raw["output_dir"]).resolve()

        files = raw["files"]
        sess_raw = raw.get("sessions", {})
        klines_db = Path(sess_raw.get("klines_db", "../data/coolish_klines.db"))
        if not klines_db.is_absolute():
            klines_db = (_PKG_DIR / klines_db).resolve()

        sessions = SessionsConfig(
            primary_symbol=str(sess_raw.get("primary_symbol", "XBTUSD")),
            min_hold_minutes=float(sess_raw.get("min_hold_minutes", 5)),
            klines_symbol=str(sess_raw.get("klines_symbol", "BTCUSDT")),
            klines_exchange=str(sess_raw.get("klines_exchange", "binance")),
            klines_db=klines_db,
            intervals=list(sess_raw.get("intervals", ["5m", "15m", "1h", "4h"])),
            klines_mode=str(sess_raw.get("klines_mode", "full")),
            klines_source=str(sess_raw.get("klines_source", "binance")),
            session_window_buffer_hours=int(sess_raw.get("session_window_buffer_hours", 48)),
            history_start=str(sess_raw.get("history_start", "2020-05-01T00:00:00Z")),
            history_end=str(sess_raw.get("history_end", "2026-04-23T00:00:00Z")),
            position_flat_epsilon=float(sess_raw.get("position_flat_epsilon", 0.0001)),
        )

        return cls(
            data_dir=data_dir,
            output_dir=output_dir,
            trade_history=data_dir / files["trade_history"],
            orders=data_dir / files["orders"],
            wallet_history=data_dir / files["wallet_history"],
            equity_curve=data_dir / files["equity_curve"],
            manifest=data_dir / files["manifest"],
            btc_symbols=list(raw.get("btc_symbols", ["XBTUSD"])),
            chunksize=int(raw.get("chunksize", 50000)),
            regime_years=list(raw.get("regime_years", [])),
            sessions=sessions,
            raw=raw,
        )

    def indicators_dir(self) -> Path:
        return self.output_dir / "indicators"

    def ensure_indicators_dir(self) -> None:
        self.indicators_dir().mkdir(parents=True, exist_ok=True)

    def ensure_output(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def load_manifest(self) -> dict[str, Any]:
        with open(self.manifest, encoding="utf-8") as f:
            return json.load(f)
